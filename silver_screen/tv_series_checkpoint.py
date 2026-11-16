"""Snapshot the files needed to reopen or continue a paused episode."""

import re
import shutil
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from .runtime import validate_run_id


MAX_CHECKPOINT_BYTES = 256 * 1024 * 1024
REQUIRED_FILES = {"manifest.json", "result.json", "media/video_queue.json"}
OPTIONAL_FILES = {
    "autonomous_job.json", "media/cast-reference.txt", "media/video_scar_memory.json",
    "media/video_runtime.json", "media/episode.mp4", "media/final_ai_film.mp4",
}
MEDIA_FILES = re.compile(r"media/(?:clips/[\w-]+\.mp4|continuity/[\w-]+\.jpg|"
                         r"shot-references/[0-9]{4}\.jpg|visual_quality/retakes/[\w-]+/[\w-]+\.mp4)")


def _allowed(name: str) -> bool:
    return name in REQUIRED_FILES or name in OPTIONAL_FILES or MEDIA_FILES.fullmatch(name) is not None


def build_checkpoint(workspace: Path, destination: Path) -> None:
    validate_run_id(workspace.name)
    if not all((workspace / path).is_file() for path in REQUIRED_FILES):
        raise ValueError("The episode has no complete render checkpoint to save.")
    files = [(workspace / name) for name in sorted(REQUIRED_FILES | OPTIONAL_FILES)]
    for directory in ("media/clips", "media/continuity", "media/shot-references", "media/visual_quality/retakes"):
        files.extend((workspace / directory).rglob("*"))
    total = 0
    with ZipFile(destination, "w", compression=ZIP_STORED) as archive:
        for path in files:
            name = path.relative_to(workspace).as_posix()
            if not path.is_file() or not _allowed(name):
                continue
            total += path.stat().st_size
            if total + 1_000_000 > MAX_CHECKPOINT_BYTES:
                raise ValueError("The episode checkpoint exceeds the 256 MiB archive limit.")
            archive.write(path, name)


def unpack_checkpoint(source: Path, workspace: Path) -> None:
    validate_run_id(workspace.name)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(source) as archive, TemporaryDirectory(prefix="episode-restore-", dir=workspace.parent) as staging:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (not REQUIRED_FILES.issubset(names) or len(names) != len(set(names))
                    or not all(_allowed(name) for name in names)
                    or sum(entry.file_size for entry in entries) > MAX_CHECKPOINT_BYTES):
                raise ValueError("The saved episode checkpoint could not be read.")
            restored = Path(staging) / workspace.name
            for entry in entries:
                destination = restored.joinpath(*PurePosixPath(entry.filename).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as payload, destination.open("wb") as output:
                    shutil.copyfileobj(payload, output)
            restored.replace(workspace)
    except BadZipFile as exc:
        raise ValueError("The saved episode checkpoint could not be read.") from exc
