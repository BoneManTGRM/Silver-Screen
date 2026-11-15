"""Private storage for completed Direct Create clips."""

from __future__ import annotations

import json
import re
from heapq import nlargest
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, NamedTuple

from .ai_video import VideoGenerationError
from .comfyui import MAX_OUTPUT

CLIPS_VOLUME = "silver-screen-completed-clips"
CLIPS_MOUNT = Path("/mnt/silver-screen-clips")
MODAL_RENDER_ID = re.compile(r"fc-[0-9A-Z]{26}")
SUBMITTED_RENDER = re.compile(r"(fc-[0-9A-Z]{26})\.(0|[2-9]|1[0-2])\.submitted")
MAX_REVIEW_BYTES = 4096


class RecentRender(NamedTuple):
    render_id: str
    timestamp: int
    requested_seconds: int | None
    saved: bool
    image: bool


def clip_filename(render_id: str) -> str:
    if not MODAL_RENDER_ID.fullmatch(render_id):
        raise ValueError("Invalid Modal render ID.")
    return f"{render_id}.mp4"


def remember_submitted_render(volume: Any, render_id: str, seconds: int | None) -> None:
    name = f"{render_id}.{0 if seconds is None else seconds}.submitted"
    if not SUBMITTED_RENDER.fullmatch(name):
        raise ValueError("Invalid media submission.")
    with volume.batch_upload(force=True) as upload:
        upload.put_file(BytesIO(b"\n"), f"/{name}")


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


def save_completed_image(volume: Any, directory: Path, render_id: str, data: bytes) -> None:
    name = Path(clip_filename(render_id)).with_suffix(".png")
    _write_file(directory, directory / name, data)
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


def _read_media(volume: Any, filename: str) -> bytes:
    data = bytearray()
    for chunk in volume.read_file(filename):
        data.extend(chunk)
        if len(data) > MAX_OUTPUT:
            raise VideoGenerationError("The saved file exceeds the 64 MiB download limit.")
    return bytes(data)


def read_saved_clip(volume: Any, render_id: str) -> bytes:
    return _read_media(volume, clip_filename(render_id))


def read_saved_media(volume: Any, render_id: str) -> bytes:
    try:
        return read_saved_clip(volume, render_id)
    except FileNotFoundError:
        return _read_media(volume, Path(clip_filename(render_id)).with_suffix(".png").name)


def media_was_deleted(volume: Any, render_id: str) -> bool:
    filename = Path(clip_filename(render_id)).with_suffix(".deleted").name
    try:
        return any(volume.read_file(filename))
    except FileNotFoundError:
        return False


def delete_saved_media(volume: Any, render_id: str, *, image: bool) -> None:
    filename = Path(clip_filename(render_id))
    with volume.batch_upload(force=True) as upload:
        upload.put_file(BytesIO(b"\n"), f"/{filename.with_suffix('.deleted').name}")
    targets = (filename.with_suffix(".png"),) if image else (filename, filename.with_suffix(".json"))
    for target in targets:
        try:
            volume.remove_file(f"/{target.name}")
        except FileNotFoundError:
            pass


def latest_renders(volume: Any, limit: int = 20) -> list[RecentRender]:
    submitted: dict[str, tuple[int, int]] = {}
    saved: dict[str, tuple[int, bool]] = {}
    deleted = set()
    for entry in volume.iterdir("/", recursive=False):
        path = Path(entry.path)
        if path.suffix in {".mp4", ".png"} and MODAL_RENDER_ID.fullmatch(path.stem) and 0 < entry.size <= MAX_OUTPUT:
            saved[path.stem] = (entry.mtime, path.suffix == ".png")
        elif path.suffix == ".deleted":
            deleted.add(path.stem)
        elif receipt := SUBMITTED_RENDER.fullmatch(path.name):
            render_id, seconds = receipt.groups()
            if render_id not in submitted or entry.mtime > submitted[render_id][0]:
                submitted[render_id] = (entry.mtime, int(seconds))
    entries = []
    for render_id in (submitted.keys() | saved.keys()) - deleted:
        started, seconds = submitted.get(render_id, (0, -1))
        timestamp, image = saved.get(render_id, (started, seconds == 0))
        entries.append(RecentRender(render_id, timestamp, seconds if seconds > 0 else None, render_id in saved, image))
    return nlargest(limit, entries, key=lambda entry: (entry.timestamp, entry.render_id))
