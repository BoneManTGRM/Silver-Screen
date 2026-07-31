"""Portable media metadata probing with FFmpeg-only fallback.

Streamlit deployments can have the imageio-ffmpeg executable without a sibling
``ffprobe`` binary. Transition assembly still needs real source duration,
dimensions, and audio-presence metadata, so this module parses FFmpeg's input
summary when ffprobe is unavailable.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


_DURATION = re.compile(
    r"Duration:\s*(?P<hours>\d+):(?P<minutes>\d+):"
    r"(?P<seconds>\d+(?:\.\d+)?)"
)
_DIMENSIONS = re.compile(
    r"Video:.*?(?P<width>\d{2,5})x(?P<height>\d{2,5})(?:[\s,])"
)


def _ffmpeg_path() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ffprobe_path() -> str | None:
    path = shutil.which("ffprobe")
    if path:
        return path
    ffmpeg = _ffmpeg_path()
    sibling = Path(ffmpeg).with_name("ffprobe") if ffmpeg else None
    return str(sibling) if sibling and sibling.exists() else None


def _empty() -> dict[str, Any]:
    return {
        "duration": 0.0,
        "width": None,
        "height": None,
        "audio": False,
        "probe": None,
    }


def _probe_with_ffprobe(path: Path) -> dict[str, Any] | None:
    ffprobe = _ffprobe_path()
    if not ffprobe:
        return None
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
        streams = payload.get("streams") or []
        video = next(
            (
                stream
                for stream in streams
                if stream.get("codec_type") == "video"
            ),
            {},
        )
        return {
            "duration": float(
                (payload.get("format") or {}).get("duration") or 0
            ),
            "width": video.get("width"),
            "height": video.get("height"),
            "audio": any(
                stream.get("codec_type") == "audio" for stream in streams
            ),
            "probe": "ffprobe",
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _probe_with_ffmpeg(path: Path) -> dict[str, Any] | None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None
    # FFmpeg prints the complete input metadata before rejecting the command for
    # having no output. A non-zero return code is expected and is not a failure.
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=45,
    )
    output = "\n".join(
        part for part in (completed.stderr, completed.stdout) if part
    )
    duration_match = _DURATION.search(output)
    dimensions_match = _DIMENSIONS.search(output)
    if not duration_match and not dimensions_match:
        return None
    seconds = 0.0
    if duration_match:
        seconds = (
            int(duration_match.group("hours")) * 3600
            + int(duration_match.group("minutes")) * 60
            + float(duration_match.group("seconds"))
        )
    return {
        "duration": seconds,
        "width": (
            int(dimensions_match.group("width"))
            if dimensions_match
            else None
        ),
        "height": (
            int(dimensions_match.group("height"))
            if dimensions_match
            else None
        ),
        "audio": "Audio:" in output,
        "probe": "ffmpeg",
    }


def probe_media(path: str | Path) -> dict[str, Any]:
    """Return duration, frame dimensions, and audio presence for a media file.

    The function prefers ffprobe and transparently falls back to FFmpeg input
    metadata. It never treats an unavailable optional probe binary as an
    eight-second default.
    """

    candidate = Path(path)
    if not candidate.is_file():
        return _empty()
    primary = _probe_with_ffprobe(candidate)
    if primary and float(primary.get("duration") or 0) > 0:
        return primary
    fallback = _probe_with_ffmpeg(candidate)
    if fallback:
        if primary:
            for key in ("width", "height"):
                if primary.get(key) is not None:
                    fallback[key] = primary[key]
            fallback["audio"] = bool(
                primary.get("audio") or fallback.get("audio")
            )
        return fallback
    return primary or _empty()
