"""Render a longer Wan 14B clip by carrying the last displayed frame forward."""

from __future__ import annotations

import base64
from pathlib import Path
from subprocess import SubprocessError
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Callable

from .ai_video import VideoGenerationError
from .comfyui import WAN_14B_EXTENDED_MAX_SECONDS, WAN_14B_FPS, WAN_14B_LANDSCAPE_HD, WAN_14B_MAX_SECONDS
from .direct_generation import _encode_video, prepare_wan_reference, verify_direct_clip


REVIEW_FIELDS = ("hand_review", "face_review", "body_review")
_REVIEW_SEVERITY = {
    "inconclusive": 1, "unavailable": 1, "no_reference_face": 1,
    "possible_loss": 2, "possible_drift": 2, "possible_distortion": 2,
}


def wan14_segment_lengths(seconds: int) -> tuple[int, ...]:
    if type(seconds) is not int or not WAN_14B_MAX_SECONDS < seconds <= WAN_14B_EXTENDED_MAX_SECONDS:
        raise VideoGenerationError(f"Choose 6 to {WAN_14B_EXTENDED_MAX_SECONDS} seconds for an extended clip.")
    count = (seconds + WAN_14B_MAX_SECONDS - 1) // WAN_14B_MAX_SECONDS
    length, remainder = divmod(seconds, count)
    return (length + 1,) * remainder + (length,) * (count - remainder)


def _normalize_segment(
    data: bytes, path: Path, length: int, size: tuple[int, int], *, keep_ending: bool,
) -> None:
    duration = verify_direct_clip(data, expected_seconds=length, minimum_fps=WAN_14B_FPS, expected_size=size)
    if duration is None or duration < length - 0.001:
        raise VideoGenerationError(f"A segment returned less than its requested {length} seconds.")
    source = path.with_name("provider.mp4")
    source.write_bytes(data)
    # Wan typically emits one extra frame. For an ending photo, retain the
    # closing frame and remove the duplicated starting frame instead.
    start = int(keep_ending and duration >= length + 0.5 / WAN_14B_FPS)
    _encode_video([
        "-i", str(source), "-an",
        "-vf", f"fps={WAN_14B_FPS},trim=start_frame={start}:end_frame={start + length * WAN_14B_FPS},setpts=PTS-STARTPTS,setsar=1",
        "-r", str(WAN_14B_FPS), "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", str(path),
    ])


def _continuation_frame(path: Path, size: tuple[int, int]) -> str:
    frame = path.with_name("continuation.jpg")
    _encode_video([
        "-sseof", f"{-1 / WAN_14B_FPS:.6f}", "-i", str(path), "-frames:v", "1",
        "-q:v", "2", "-update", "1", str(frame),
    ])
    image, _ = prepare_wan_reference(frame, size, wan_14b=True, preserve_framing=True)
    return "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")


def render_extended_clip(
    prompt: str, settings: dict,
    render_segment: Callable[[str, dict], bytes | dict],
    progress: Callable[[int, int], None],
) -> dict:
    lengths = wan14_segment_lengths(settings.get("duration"))
    settings = dict(settings)
    original = settings.get("face_reference") or settings.get("image")
    ending = settings.pop("end_image", None)
    size = tuple(settings.get("video_size") or WAN_14B_LANDSCAPE_HD)
    seed = int(settings["seed"])
    settings.update(video_size=size, save_clip=False, allow_retake=False, record_stats=True)
    if original:
        settings.update(face_reference=original, check_hands=True)

    started = monotonic()
    reviews: dict[str, dict] = {}
    gpu_seconds = 0.0
    hardware: set[str] = set()
    offset = 0
    with TemporaryDirectory(prefix="silver-extended-") as directory:
        root = Path(directory)
        clips = []
        progress(0, len(lengths))
        for index, length in enumerate(lengths):
            last = index == len(lengths) - 1
            segment = {**settings, "duration": length, "seed": (seed + index) % (2**53)}
            direction = prompt
            if clips:
                segment["image"] = _continuation_frame(clips[-1], size)
                direction = (
                    "Continue directly from this frame. Preserve the subject, proportions, wardrobe, lighting and camera. "
                    "Carry the requested action forward smoothly from its current state. " + prompt
                )
            if last and ending:
                segment["end_image"] = ending
            try:
                result = render_segment(direction, segment)
                result = result if isinstance(result, dict) else {"data": result}
                clip = root / f"segment-{index}.mp4"
                _normalize_segment(result["data"], clip, length, size, keep_ending=bool(last and ending))
            except (VideoGenerationError, OSError, SubprocessError) as exc:
                raise VideoGenerationError(f"Segment {index + 1} of {len(lengths)} failed. {exc}") from exc
            clips.append(clip)
            for name in REVIEW_FIELDS:
                if item := result.get(name):
                    severity = _REVIEW_SEVERITY.get(item.get("status"), 0)
                    if name not in reviews or severity > _REVIEW_SEVERITY.get(reviews[name].get("status"), 0):
                        reviews[name] = dict(item)
                        if isinstance(item.get("first_issue_seconds"), (float, int)):
                            reviews[name]["first_issue_seconds"] = item["first_issue_seconds"] + offset
            if stats := result.get("render_stats"):
                gpu_seconds += stats.get("gpu_worker_seconds", 0)
                hardware.update(stats.get("gpus", []))
            offset += length
            progress(index + 1, len(lengths))

        manifest = root / "segments.txt"
        manifest.write_text("\n".join(f"file {clip.name}" for clip in clips), encoding="utf-8")
        final = root / "clip.mp4"
        _encode_video([
            "-f", "concat", "-i", str(manifest), "-map", "0:v:0", "-c:v", "copy",
            "-movflags", "+faststart", str(final),
        ])
        data = final.read_bytes()
        duration = verify_direct_clip(data, expected_seconds=offset, minimum_fps=WAN_14B_FPS, expected_size=size)
        if duration is None or abs(duration - offset) > 0.001:
            raise VideoGenerationError("The joined clip does not match the requested length.")
    return {
        "data": data, **reviews,
        "render_stats": {
            "model": "Wan 2.2 14B", "segments": len(lengths), "fps": WAN_14B_FPS,
            "width": size[0], "height": size[1], "requested_seconds": offset,
            "gpu_worker_seconds": round(gpu_seconds, 2), "gpus": sorted(hardware),
            "execution_seconds": round(monotonic() - started, 2),
        },
    }
