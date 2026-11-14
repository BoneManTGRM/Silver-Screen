"""Private storage for completed Direct Create clips."""

from __future__ import annotations

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


def clip_filename(render_id: str) -> str:
    if not MODAL_RENDER_ID.fullmatch(render_id):
        raise ValueError("Invalid Modal render ID.")
    return f"{render_id}.mp4"


def save_completed_clip(volume: Any, directory: Path, render_id: str, data: bytes) -> None:
    destination = directory / clip_filename(render_id)
    with NamedTemporaryFile(dir=directory, prefix=".render-", delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            staged.replace(destination)
            volume.commit()
        finally:
            staged.unlink(missing_ok=True)


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
