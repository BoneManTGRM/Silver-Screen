"""Standalone image and clip generation using the creator's prompt."""

from __future__ import annotations

import io
import re
import subprocess
import urllib.request
from pathlib import Path
from textwrap import fill
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from PIL import Image, ImageOps

from .ai_video import (
    DEFAULT_MODEL, ReplicateVideoClient, VideoGenerationError, _ffmpeg_path,
    _ffprobe_path, verify_mp4,
)
from .comfyui import IMAGE_SIZES, WAN_14B_FPS, wan_reference_size
from .voice_audio import _has_audio, combine_voice_tracks, mix_voice_into_clip, verify_audio

IMAGE_MODEL = "black-forest-labs/flux-schnell"
CLIP_MODEL = DEFAULT_MODEL
OUTPUT_LIMIT = 64 * 1024 * 1024
FILM_LIMIT = 128 * 1024 * 1024
RECORDING_LIMIT = 20 * 1024 * 1024
REFERENCE_LIMIT = 1_500_000
FILM_CANVASES = {
    "Landscape HD · 1280 × 720": (1280, 720),
    "Portrait HD · 720 × 1280": (720, 1280),
    "Landscape compact · 960 × 540": (960, 540),
}
FORMAT_NAMES = {"portrait": "9:16", "vertical": "9:16", "landscape": "16:9", "horizontal": "16:9", "square": "1:1"}
FORMAT_LABELS = re.compile(
    r"\b(?:aspect ratio|frame format|output format|format)\s*(?:[:=]\s*|of\s+)?"
    r"(\d+\s*:\s*\d+|portrait|vertical|landscape|horizontal|square)\b",
    re.IGNORECASE,
)
FORMAT_PHRASES = re.compile(
    r"\b(?:in\s+(\d+\s*:\s*\d+)|(\d+\s*:\s*\d+)\s+(?:format|frame|video|clip|image)|"
    r"(portrait|vertical|landscape|horizontal|square)\s+(?:orientation|frame|video|clip|image))\b",
    re.IGNORECASE,
)
CLIP_LENGTH = re.compile(
    r"\b(?:clip length|(?:clip |video )?duration|(?:clip|video) (?:should |must )?lasts?|"
    r"make (?:it|this|the clip|the video))\s*(?:[:=]\s*|of\s+|is\s+)?(\d{1,3})\s*(?:seconds?|secs?|s)\b"
    r"|(?<![.\d])(\d{1,3})\s*[- ]\s*(?:seconds?|secs?)\s+"
    r"(?:long|(?:(?:portrait|vertical|landscape|horizontal|square)\s+)?(?:clip|video))\b",
    re.IGNORECASE,
)
NEGATED_INSTRUCTION = re.compile(
    r"\b(?:not|no|avoid|without|don['’]t)\s+(?:(?:a|an|the|in|use|make)\s+)*$", re.IGNORECASE,
)
QUOTED_TEXT = re.compile(r"""["“][^"”\n]*["”]|(?<!\w)['‘][^'’\n]*['’](?!\w)""")


def direct_prompt_aspect(prompt: str) -> str | None:
    """Read a clear frame instruction without interpreting the scene description."""
    prompt = QUOTED_TEXT.sub(" ", prompt)
    ratios = set()
    for pattern in (FORMAT_LABELS, FORMAT_PHRASES):
        for match in pattern.finditer(prompt):
            if NEGATED_INSTRUCTION.search(prompt[:match.start()]):
                continue
            value = "".join(next(group for group in match.groups() if group).lower().split())
            ratios.add(FORMAT_NAMES.get(value, value))
    if len(ratios) == 1:
        ratio = next(iter(ratios))
        if ratio in IMAGE_SIZES:
            return ratio
    return None


def direct_prompt_seconds(prompt: str) -> int | None:
    """Read the total length, leaving durations of individual actions alone."""
    prompt = QUOTED_TEXT.sub(" ", prompt)
    lengths = {
        int(next(group for group in match.groups() if group))
        for match in CLIP_LENGTH.finditer(prompt)
        if not NEGATED_INSTRUCTION.search(prompt[:match.start()])
    }
    return next(iter(lengths)) if len(lengths) == 1 else None


def _open_reference(source: Path | BinaryIO) -> Image.Image:
    try:
        with Image.open(source) as uploaded:
            return ImageOps.exif_transpose(uploaded).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise VideoGenerationError("The uploaded image could not be read.") from exc
    finally:
        if not isinstance(source, Path):
            source.seek(0)


def _reference_jpeg(frame: Image.Image) -> bytes:
    for quality in (94, 85, 75):
        buffer = io.BytesIO()
        frame.save(buffer, format="JPEG", quality=quality, subsampling=0, optimize=True)
        if buffer.tell() <= REFERENCE_LIMIT:
            return buffer.getvalue()
    raise VideoGenerationError("The reference image exceeds the render upload limit.")


def prepare_wan_reference(
    source: Path | BinaryIO, frame_size: tuple[int, int] | None, *,
    wan_14b: bool = False, preserve_framing: bool = False,
) -> tuple[bytes, tuple[int, int]]:
    """Prepare the exact starting frame that will be shown in the preview."""
    photo = _open_reference(source)
    size = frame_size or wan_reference_size(photo.width, photo.height, wan_14b=wan_14b)
    if preserve_framing:
        frame = ImageOps.pad(photo, size, Image.Resampling.LANCZOS, color="black")
    else:
        frame = ImageOps.fit(photo, size, Image.Resampling.LANCZOS)
    return _reference_jpeg(frame), size


def prepare_edit_reference(source: Path | BinaryIO) -> bytes:
    """Normalize orientation and limit upload size without cropping the image."""
    photo = _open_reference(source)
    photo.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
    return _reference_jpeg(photo)


def preserve_reference_appearance(prompt: str) -> str:
    return (
        f"{prompt}\n\nKeep the appearance of the people in the starting image throughout the shot. "
        "Preserve facial features, body proportions, hairstyle, clothing, and accessories "
        "while performing the requested motion and camera direction."
    )


def submit_direct_generation(
    client: ReplicateVideoClient,
    prompt: str,
    *,
    aspect_ratio: str,
    duration: int = 8,
    generate_audio: bool = True,
) -> dict[str, Any]:
    if not prompt.strip():
        raise VideoGenerationError("Enter a prompt before generating.")

    inputs: dict[str, Any] = {"prompt": prompt, "aspect_ratio": aspect_ratio}
    if client.model == IMAGE_MODEL:
        inputs["output_format"] = "png"
    elif client.model == CLIP_MODEL:
        inputs.update(duration=duration, resolution="720p", generate_audio=generate_audio)
    else:
        raise VideoGenerationError("This model is unavailable in Direct Create.")

    endpoint = f"https://api.replicate.com/v1/models/{client.model}/predictions"
    return client._request_json("POST", endpoint, {"input": inputs})


def _delivery_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "replicate.delivery" or host.endswith(".replicate.delivery")
    )


class _DeliveryRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _delivery_url(newurl):
            raise VideoGenerationError("Replicate redirected the output outside its delivery service.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_direct_output(prediction: dict[str, Any], *, image: bool) -> bytes:
    output = prediction.get("output")
    if isinstance(output, list):
        output = next((item for item in output if isinstance(item, str)), None)
    if not isinstance(output, str) or not _delivery_url(output):
        raise VideoGenerationError("Replicate did not return a downloadable output.")

    with urllib.request.build_opener(_DeliveryRedirects()).open(output, timeout=60) as response:
        data = response.read(OUTPUT_LIMIT + 1)
    return verify_direct_output(data, image=image)


def verify_direct_output(
    data: bytes, *, image: bool, expected_seconds: int | None = None,
    expected_size: tuple[int, int] | None = None,
) -> bytes:
    if image:
        _check_output_size(data)
        with Image.open(io.BytesIO(data), formats=["PNG"]) as generated:
            _verify_frame_size(generated.size, expected_size)
            generated.verify()
    else:
        verify_direct_clip(data, expected_seconds=expected_seconds, expected_size=expected_size)
    return data


def _verify_frame_size(actual: tuple[int, int], expected: tuple[int, int] | None) -> None:
    if expected is not None and actual != expected:
        raise VideoGenerationError(f"The render did not match the requested {expected[0]} × {expected[1]} frame.")


def _check_output_size(data: bytes) -> None:
    if not data or len(data) > OUTPUT_LIMIT:
        raise VideoGenerationError("The generated file is empty or exceeds the 64 MiB download limit.")


def verify_direct_clip(
    data: bytes, *, expected_seconds: int | None = None, minimum_fps: int | None = None,
    expected_size: tuple[int, int] | None = None,
) -> float | None:
    """Verify delivery, with a full decode and freeze check for new GPU renders."""
    _check_output_size(data)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "clip.mp4"
        path.write_bytes(data)
        metadata = _verified_clip(path)
        _verify_frame_size((metadata["width"], metadata["height"]), expected_size)
        duration = metadata["videoDurationSeconds"] or metadata["durationSeconds"]
        fps = metadata["frameRate"]
        frame_seconds = 1 / (fps or 24)
        if expected_seconds and duration is not None and duration + frame_seconds + 0.001 < expected_seconds:
            raise VideoGenerationError(f"The render returned {duration:.1f} seconds of a {expected_seconds}-second clip.")
        if minimum_fps is not None:
            if not duration or not fps or fps < minimum_fps - 0.01:
                raise VideoGenerationError(f"The render did not deliver video at {minimum_fps} frames per second.")
            _verify_render_motion(path, duration)
    return duration


def _verify_render_motion(path: Path, duration: float) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise VideoGenerationError("FFmpeg is required to check the complete render.")
    try:
        result = subprocess.run([
            ffmpeg, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
            "-i", str(path), "-map", "0:V:0", "-an", "-sn",
            "-vf", "scale=256:-2,freezedetect=n=-60dB:d=0.75,metadata=mode=print:file=-",
            "-f", "null", "-",
        ], capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise VideoGenerationError("The rendered video took too long to verify.") from exc
    if result.returncode or result.stderr:
        raise VideoGenerationError("The rendered video could not be decoded from start to finish.")

    frozen = 0.0
    start = None
    for line in result.stdout.splitlines():
        key, _, seconds = line.partition("=")
        if key == "lavfi.freezedetect.freeze_start":
            start = float(seconds)
        elif key == "lavfi.freezedetect.freeze_end" and start is not None:
            frozen += float(seconds) - start
            start = None
    if start is not None:
        frozen += duration - start
    if frozen >= max(1.0, duration * 0.6):
        raise VideoGenerationError("Most of the rendered video is frozen. Try a prompt with one clear movement.")


def _verified_clip(source: Path) -> dict[str, Any]:
    metadata = verify_mp4(source)
    if _ffprobe_path() and not (metadata["width"] and metadata["height"]):
        raise VideoGenerationError("The generated MP4 has no readable video track.")
    return metadata


def _clip_timing(source: Path) -> tuple[float, float | None]:
    metadata = _verified_clip(source)
    duration = metadata["videoDurationSeconds"] or metadata["durationSeconds"]
    if not duration:
        raise VideoGenerationError("FFprobe is required to read the clip's duration.")
    return duration, metadata.get("frameRate")


def _encode_video(command: list[str], *, cwd: Path | None = None) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise VideoGenerationError("FFmpeg is required to export video.")
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-y", *command],
        capture_output=True, text=True, timeout=300, cwd=cwd,
    )
    if result.returncode != 0:
        raise VideoGenerationError(f"Video export failed: {result.stderr[-1000:]}")


def finish_short_video(
    source: Path, destination: Path, *, seconds: int, brand: str = "", cta: str = "",
) -> Path:
    """Export the requested runtime and optionally overlay a final end card."""
    available, source_fps = _clip_timing(source)
    if available + 0.25 < seconds:
        raise VideoGenerationError(f"The footage is {available:.1f} seconds long. The cut requires {seconds} seconds.")
    start = max(0, seconds - 3)
    brand, cta = brand.strip(), cta.strip()
    fps = WAN_14B_FPS if source_fps == WAN_14B_FPS else 24
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="short-export-", dir=destination.parent) as directory:
        work = Path(directory)
        filters = [f"fps={fps},tpad=stop_mode=clone:stop_duration=0.25"]
        for text, size, bottom in ((brand, 22, 0.26 if cta else 0.1), (cta, 30, 0.07)):
            if not text:
                continue
            # FFmpeg receives text through a file, and percent signs are literal.
            name = f"line-{size}.txt"
            (work / name).write_text(fill(text, width=26), encoding="utf-8")
            filters.append(
                f"drawtext=font=DejaVu Sans:textfile={name}:expansion=none:"
                f"fontsize=h/{size}:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=8:"
                f"x=(w-tw)/2:y=h-th-h*{bottom}:enable='gte(t,{start})'"
            )
        staged = work / "export.mp4"
        _encode_video(
            ["-i", str(source.resolve()), "-vf", ",".join(filters), "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18", "-c:a", "copy",
             "-t", str(seconds), "-movflags", "+faststart", staged.name],
            cwd=work,
        )
        duration, _ = _clip_timing(staged)
        if abs(duration - seconds) > 1 / fps + 0.001:
            raise VideoGenerationError("The exported video does not match the selected length.")
        staged.replace(destination)
    return destination


def finish_direct_clip(
    video: bytes, recordings: list[tuple[bytes, float]], *, smooth: bool,
    preserve_audio: bool,
) -> bytes:
    """Mix recordings and optionally conform to 48 fps using the source frames."""
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.mp4"
        source.write_bytes(video)
        duration, _ = _clip_timing(source)
        if recordings:
            tracks = []
            for index, (recording, start) in enumerate(recordings):
                if len(recording) > RECORDING_LIMIT:
                    raise VideoGenerationError("Each recording must be 20 MiB or smaller.")
                track = root / f"recording-{index}.audio"
                track.write_bytes(recording)
                length = verify_audio(track)["durationSeconds"]
                if length is None or start + length > duration + 0.05:
                    raise VideoGenerationError(
                        f"Recording {index + 1} extends past the {duration:.2f}-second clip. "
                        "Move it earlier or record a shorter line."
                    )
                tracks.append((track, start))
            mixed = combine_voice_tracks(tracks, root / "voices.wav", duration)
            source = mix_voice_into_clip(
                source, mixed, root / "voiced.mp4", duration=duration,
                preserve_source_audio=preserve_audio, delay_seconds=0,
            )
        if smooth:
            destination = root / "smooth.mp4"
            _encode_video([
                "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "fps=48:round=up", "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination),
            ])
            source = destination
        return verify_direct_output(source.read_bytes(), image=False)


def assemble_direct_film(
    clips: list[bytes], runtime: int, *, smooth: bool, canvas: tuple[int, int] = (960, 540),
) -> bytes:
    """Export an ordered cut without running past its requested runtime."""
    if not clips or runtime <= 0:
        raise VideoGenerationError("Add rendered shots and choose a film runtime.")
    if canvas not in FILM_CANVASES.values():
        raise VideoGenerationError("Choose a supported film canvas.")
    if sum(map(len, clips)) > FILM_LIMIT:
        raise VideoGenerationError("The selected shots exceed the 128 MiB film workspace.")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        sources = []
        for index, clip in enumerate(clips):
            source = root / f"source-{index}.mp4"
            source.write_bytes(clip)
            sources.append((source, *_clip_timing(source)))
        available = sum(duration for _, duration, _ in sources)
        if available < runtime:
            raise VideoGenerationError(
                f"Your shots total {available:.1f} seconds. Render another shot or shorten the cut."
            )

        width, height = canvas
        remaining = float(runtime)
        normalized = []
        source_rates = {source_fps for _, _, source_fps in sources}
        fps = 24
        if smooth or (WAN_14B_FPS in source_rates and len(source_rates) > 1):
            # 48 is a common multiple of the Wan rates. Retain the same frames and timing.
            fps = 48
        elif source_rates == {WAN_14B_FPS}:
            fps = WAN_14B_FPS
        for index, (source, duration, _) in enumerate(sources):
            if remaining < 0.02:
                break
            length = min(remaining, duration)
            destination = root / f"shot-{index}.mp4"
            command = ["-i", str(source), "-map", "0:v:0", "-map", "0:a:0"]
            if not _has_audio(source):
                command = ["-i", str(source), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                           "-map", "0:v:0", "-map", "1:a:0"]
            resize = (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")
            _encode_video([
                *command, "-vf", f"{resize},fps={fps}:round=up", "-af", "apad", "-t", f"{length:.3f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000", str(destination),
            ])
            normalized.append(destination)
            remaining -= length

        manifest = root / "shots.txt"
        manifest.write_text("\n".join(f"file {shot.name}" for shot in normalized), encoding="utf-8")
        film = root / "film.mp4"
        _encode_video([
            "-f", "concat", "-i", str(manifest), "-map", "0:v:0", "-map", "0:a:0",
            "-vf", f"setpts=PTS-STARTPTS,fps={fps},tpad=stop_mode=clone:stop=1,trim=end_frame={runtime * fps}",
            "-af", f"aresample=async=1:first_pts=0,apad,atrim=0:{runtime}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
            "-t", str(runtime), "-movflags", "+faststart", str(film),
        ])
        actual_runtime, _ = _clip_timing(film)
        if abs(actual_runtime - runtime) > 1 / fps:
            raise VideoGenerationError("The rendered cut does not match your chosen runtime.")
        if film.stat().st_size > FILM_LIMIT:
            raise VideoGenerationError("The film exceeds the 128 MiB download limit.")
        return film.read_bytes()
