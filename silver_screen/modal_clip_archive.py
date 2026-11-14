"""Private storage for completed Direct Create clips."""

from __future__ import annotations

import json
import re
from heapq import nlargest
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .ai_video import VideoGenerationError
from .comfyui import MAX_OUTPUT

CLIPS_VOLUME = "silver-screen-completed-clips"
CLIPS_MOUNT = Path("/mnt/silver-screen-clips")
MODAL_RENDER_ID = re.compile(r"fc-[0-9A-Z]{26}")
MAX_REVIEW_BYTES = 4096


def clip_filename(render_id: str) -> str:
    if not MODAL_RENDER_ID.fullmatch(render_id):
        raise ValueError("Invalid Modal render ID.")
    return f"{render_id}.mp4"


def _write_file(directory: Path, destination: Path, data: bytes) -> None:
    with NamedTemporaryFile(dir=directory, prefix=".render-", delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)


def save_completed_clip(volume: Any, directory: Path, render_id: str, data: bytes, *, hand_review: dict | None = None) -> None:
    destination = directory / clip_filename(render_id)
    if hand_review is not None:
        _write_file(directory, destination.with_suffix(".json"), json.dumps(hand_review).encode())
    _write_file(directory, destination, data)
    volume.commit()


def read_saved_hand_review(volume: Any, render_id: str) -> dict | None:
    filename = Path(clip_filename(render_id)).with_suffix(".json").name
    metadata = bytearray()
    try:
        for chunk in volume.read_file(filename):
            metadata.extend(chunk)
            if len(metadata) > MAX_REVIEW_BYTES:
                return None
        data = json.loads(metadata)
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_saved_clip(volume: Any, render_id: str) -> bytes:
    data = bytearray()
    for chunk in volume.read_file(clip_filename(render_id)):
        data.extend(chunk)
        if len(data) > MAX_OUTPUT:
            raise VideoGenerationError("The saved clip exceeds the 64 MiB download limit.")
    return bytes(data)


def latest_saved_clips(volume: Any, limit: int = 20) -> list[tuple[str, int]]:
    entries = (
        (entry.mtime, Path(entry.path).stem) for entry in volume.iterdir("/", recursive=False)
        if entry.path.endswith(".mp4") and MODAL_RENDER_ID.fullmatch(Path(entry.path).stem)
        and 0 < entry.size <= MAX_OUTPUT
    )
    return [(render_id, modified) for modified, render_id in nlargest(limit, entries)]
