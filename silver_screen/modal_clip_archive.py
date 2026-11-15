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
from .comfyui import MAX_OUTPUT, WAN_14B_EXTENDED_MAX_SECONDS

CLIPS_VOLUME = "silver-screen-completed-clips"
CLIPS_MOUNT = Path("/mnt/silver-screen-clips")
MODAL_RENDER_ID = re.compile(r"fc-[0-9A-Z]{26}")
SUBMITTED_RENDER = re.compile(r"(fc-[0-9A-Z]{26})\.(0|[2-9]|[12][0-9])\.submitted")
MAX_REVIEW_BYTES = 4096


class RecentRender(NamedTuple):
    render_id: str
    timestamp: int
    requested_seconds: int | None
    saved: bool
    image: bool
    submitted_at: int | None = None


def clip_filename(render_id: str) -> str:
    if not MODAL_RENDER_ID.fullmatch(render_id):
        raise ValueError("Invalid Modal render ID.")
    return f"{render_id}.mp4"


def remember_submitted_render(volume: Any, render_id: str, seconds: int | None) -> None:
    name = f"{render_id}.{0 if seconds is None else seconds}.submitted"
    if not SUBMITTED_RENDER.fullmatch(name) or (seconds is not None and seconds > WAN_14B_EXTENDED_MAX_SECONDS):
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


def save_completed_clip(
    volume: Any, directory: Path, render_id: str, data: bytes, *,
    hand_review: dict | None = None, face_review: dict | None = None, body_review: dict | None = None,
    render_stats: dict | None = None,
) -> None:
    destination = directory / clip_filename(render_id)
    reviews = (
        {"hand_review": hand_review, "face_review": face_review, "body_review": body_review, "render_stats": render_stats}
        if face_review or body_review or render_stats else hand_review
    )
    if reviews:
        _write_file(directory, destination.with_suffix(".json"), json.dumps(reviews).encode())
    _write_file(directory, destination, data)
    volume.commit()


def save_completed_image(volume: Any, directory: Path, render_id: str, data: bytes) -> None:
    name = Path(clip_filename(render_id)).with_suffix(".png")
    _write_file(directory, directory / name, data)
    volume.commit()


def _read_small_json(volume: Any, filename: str) -> dict:
    metadata = bytearray()
    try:
        for chunk in volume.read_file(filename):
            metadata.extend(chunk)
            if len(metadata) > MAX_REVIEW_BYTES:
                return {}
        data = json.loads(metadata)
    except (FileNotFoundError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def read_saved_reviews(volume: Any, render_id: str) -> dict:
    filename = Path(clip_filename(render_id)).with_suffix(".json").name
    data = _read_small_json(volume, filename)
    if isinstance(data.get("status"), str):
        return {"hand_review": data}  # Legacy clips stored only the hand review.
    return {key: data[key] for key in ("hand_review", "face_review", "body_review", "render_stats")
            if isinstance(data.get(key), dict)}


def render_progress_filename(render_id: str) -> str:
    return Path(clip_filename(render_id)).with_suffix(".progress").name


def write_render_progress(volume: Any, directory: Path, render_id: str, complete: int, total: int) -> None:
    filename = render_progress_filename(render_id)
    _write_file(directory, directory / filename, json.dumps({"complete": complete, "total": total}).encode())
    volume.commit()


def read_render_progress(volume: Any, render_id: str) -> dict:
    raw = _read_small_json(volume, render_progress_filename(render_id))
    complete, total = raw.get("complete"), raw.get("total")
    if type(complete) is int and type(total) is int and 0 <= complete <= total <= 4 and total >= 2:
        return {"complete": complete, "total": total}
    return {}


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
            if int(seconds) > WAN_14B_EXTENDED_MAX_SECONDS:
                continue
            if render_id not in submitted or entry.mtime > submitted[render_id][0]:
                submitted[render_id] = (entry.mtime, int(seconds))
    entries = []
    for render_id in (submitted.keys() | saved.keys()) - deleted:
        started, seconds = submitted.get(render_id, (0, -1))
        timestamp, image = saved.get(render_id, (started, seconds == 0))
        entries.append(RecentRender(
            render_id, timestamp, seconds if seconds > 0 else None, render_id in saved, image, started or None,
        ))
    return nlargest(limit, entries, key=lambda entry: (entry.timestamp, entry.render_id))
