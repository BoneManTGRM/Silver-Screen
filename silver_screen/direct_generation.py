"""Standalone image and clip generation using the creator's prompt."""

from __future__ import annotations

import io
import subprocess
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from .ai_video import (
    DEFAULT_MODEL, ReplicateVideoClient, VideoGenerationError, _ffmpeg_path,
    _ffprobe_path, verify_mp4,
)
from .voice_audio import _has_audio, combine_voice_tracks, mix_voice_into_clip, verify_audio

IMAGE_MODEL = "black-forest-labs/flux-schnell"
CLIP_MODEL = DEFAULT_MODEL
OUTPUT_LIMIT = 64 * 1024 * 1024
FILM_LIMIT = 128 * 1024 * 1024
RECORDING_LIMIT = 20 * 1024 * 1024
FILM_CANVASES = {
    "Landscape HD · 1280 × 720": (1280, 720),
    "Portrait HD · 720 × 1280": (720, 1280),
    "Landscape compact · 960 × 540": (960, 540),
}


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


def verify_direct_output(data: bytes, *, image: bool) -> bytes:
    if not data or len(data) > OUTPUT_LIMIT:
        raise VideoGenerationError("The generated file is empty or exceeds the 64 MiB download limit.")

    if image:
        with Image.open(io.BytesIO(data), formats=["PNG"]) as generated:
            generated.verify()
    else:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(data)
            _verified_clip(path)
    return data


def _verified_clip(source: Path) -> dict[str, Any]:
    metadata = verify_mp4(source)
    if _ffprobe_path() and not (metadata["width"] and metadata["height"]):
        raise VideoGenerationError("The generated MP4 has no readable video track.")
    return metadata


def _clip_timing(source: Path) -> tuple[float, float | None]:
    metadata = _verified_clip(source)
    duration = metadata["durationSeconds"]
    if not duration:
        raise VideoGenerationError("FFprobe is required to read the clip's duration.")
    return duration, metadata.get("frameRate")


def _encode_video(command: list[str]) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise VideoGenerationError("FFmpeg is required to export video.")
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-y", *command],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise VideoGenerationError(f"Video export failed: {result.stderr[-1000:]}")


def _motion_filter(source_fps: float | None) -> str:
    if source_fps is not None and source_fps >= 48:
        return "fps=48"
    # Padding provides the look-ahead frames FFmpeg needs at the end of a shot.
    return ("tpad=stop_mode=clone:stop_duration=0.25,"
            "minterpolate=fps=48:mi_mode=mci:mc_mode=aobmc:vsbmc=1:scd=fdiff")


def finish_direct_clip(
    video: bytes, recordings: list[tuple[bytes, float]], *, smooth: bool,
    preserve_audio: bool,
) -> bytes:
    """Mix recordings and optionally interpolate motion at the original speed."""
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.mp4"
        source.write_bytes(video)
        duration, source_fps = _clip_timing(source)
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
                "-vf", _motion_filter(source_fps), "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
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
        for index, (source, duration, source_fps) in enumerate(sources):
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
            motion = _motion_filter(source_fps) if smooth else "fps=24"
            _encode_video([
                *command, "-vf", f"{resize},{motion}", "-af", "apad", "-t", f"{length:.3f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000", str(destination),
            ])
            normalized.append(destination)
            remaining -= length

        manifest = root / "shots.txt"
        manifest.write_text("\n".join(f"file {shot.name}" for shot in normalized), encoding="utf-8")
        film = root / "film.mp4"
        fps = 48 if smooth else 24
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
