"""Reusable cast photos and original voice takes for a private studio."""

from __future__ import annotations

import io
import os
import re
import subprocess
import wave
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from PIL import Image, ImageOps

from .ai_video import VideoGenerationError, _ffmpeg_path

PHOTO_SLOTS = {"Your photo": "lead", "Co-star photo": "co-star", "Both of you in one photo": "together"}
VOICE_ROLES = {"Your voice": "lead", "Co-star voice": "co-star"}
MAX_UPLOAD = 20 * 1024 * 1024
TAKE_NAME = re.compile(r"[\w -]{1,48}")


def cast_library_root() -> Path | None:
    configured = os.getenv("SILVER_SCREEN_CAST_LIBRARY_DIR", "").strip()
    return Path(configured) if configured else None


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=".cast-", delete=False) as temp:
        pending = Path(temp.name)
        try:
            temp.write(data)
            temp.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


def _photo_path(root: Path, label: str) -> Path:
    return root / f"{PHOTO_SLOTS[label]}.jpg"


def save_cast_photo(root: Path, label: str, data: bytes) -> None:
    if not data or len(data) > MAX_UPLOAD:
        raise VideoGenerationError("Choose a photo smaller than 20 MiB.")
    try:
        with Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as photo:
            normalized = ImageOps.exif_transpose(photo).convert("RGB")
            normalized.thumbnail((1280, 1280))
            output = io.BytesIO()
            normalized.save(output, format="JPEG", quality=88, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise VideoGenerationError("Choose a readable JPEG, PNG, or WebP photo.") from exc
    _save(_photo_path(root, label), output.getvalue())


def saved_cast_photos(root: Path) -> dict[str, Path]:
    return {label: path for label in PHOTO_SLOTS if (path := _photo_path(root, label)).is_file()}


def saved_voice_takes(root: Path, role: str) -> dict[str, Path]:
    folder = root / "voices" / VOICE_ROLES[role]
    return {path.stem: path for path in sorted(folder.glob("*.wav")) if path.is_file()}


def _normalize_recording(data: bytes) -> tuple[bytes, float]:
    if not data or len(data) > MAX_UPLOAD:
        raise VideoGenerationError("Choose a recording smaller than 20 MiB.")
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise VideoGenerationError("FFmpeg is required to save voice takes.")
    with TemporaryDirectory() as directory:
        source = Path(directory) / "recording"
        converted = source.with_suffix(".wav")
        source.write_bytes(data)
        # Normalize file format without changing the recorded speech or its speed.
        result = subprocess.run([
            ffmpeg, "-nostdin", "-v", "error", "-i", str(source), "-map", "0:a:0",
            "-t", "61", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(converted),
        ], capture_output=True, timeout=45)
        data = converted.read_bytes() if result.returncode == 0 else b""
    if len(data) < 512:
        raise VideoGenerationError("Choose a recording with readable audio.")
    with wave.open(io.BytesIO(data), "rb") as recording:
        duration = recording.getnframes() / recording.getframerate()
    return data, duration


def save_voice_take(root: Path, role: str, name: str, data: bytes) -> None:
    name = name.strip()
    if not name or not TAKE_NAME.fullmatch(name):
        raise VideoGenerationError("Name the take using up to 48 letters, numbers, spaces, hyphens, or underscores.")
    takes = saved_voice_takes(root, role)
    if len(takes) >= 20 and name not in takes:
        raise VideoGenerationError("This voice already has 20 saved takes. Remove one or reuse its name to replace it.")
    data, duration = _normalize_recording(data)
    if duration > 60:
        raise VideoGenerationError("Save a voice take of 60 seconds or less.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _save(root / "voices" / VOICE_ROLES[role] / f"{name}.wav", data)


def save_clone_sample(root: Path, role: str, data: bytes) -> None:
    data, duration = _normalize_recording(data)
    if not 5 <= duration <= 30:
        raise VideoGenerationError("Record 5 to 30 seconds of one person's speech. Aim for 10 to 20 seconds.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _save(root / "clones" / f"{VOICE_ROLES[role]}.wav", data)


def saved_clone_samples(root: Path) -> dict[str, Path]:
    folder = root / "clones"
    return {label: path for label, role in VOICE_ROLES.items() if (path := folder / f"{role}.wav").is_file()}
