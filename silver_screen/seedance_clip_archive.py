"""Store completed Seedance clips with the studio's other private media."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

from .ai_video import VideoGenerationError
from .modal_clip_archive import CLIPS_VOLUME, _write_file
from .modal_renderer import modal_configured
from .runtime import resolve_runs_root
from .seedance import MAX_SEEDANCE_MP4, SEEDANCE_TASK_ID

_ARCHIVE = "seedance-clips"


def _clip_path(task_id: str) -> tuple[Path, str]:
    if not SEEDANCE_TASK_ID.fullmatch(task_id):
        raise VideoGenerationError("Enter the full BytePlus task ID beginning with cgt-.")
    name = f"{_ARCHIVE}/{task_id}.mp4"
    return resolve_runs_root("runs") / name, "/" + name


def save_seedance_clip(task_id: str, data: bytes) -> None:
    path, remote_path = _clip_path(task_id)
    if not 0 < len(data) <= MAX_SEEDANCE_MP4:
        raise VideoGenerationError("The Seedance clip exceeds the studio's download limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_file(path.parent, path, data)
    if modal_configured():
        import modal

        try:
            with modal.Volume.from_name(CLIPS_VOLUME).batch_upload(force=True) as upload:
                upload.put_file(path, remote_path)
        except (modal.exception.Error, OSError) as exc:
            raise VideoGenerationError("The clip is ready. Its private archive could not be confirmed.") from exc


def read_seedance_clip(task_id: str) -> bytes | None:
    path, remote_path = _clip_path(task_id)
    if not path.is_file() and modal_configured():
        import modal

        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, prefix=".seedance-", delete=False) as handle:
            staged = Path(handle.name)
            try:
                for chunk in modal.Volume.from_name(CLIPS_VOLUME).read_file(remote_path):
                    if handle.tell() + len(chunk) > MAX_SEEDANCE_MP4:
                        raise VideoGenerationError("The saved Seedance clip exceeds the studio's download limit.")
                    handle.write(chunk)
                handle.close()
                staged.replace(path)
            except (FileNotFoundError, modal.exception.NotFoundError):
                return None
            except (modal.exception.Error, OSError) as exc:
                raise VideoGenerationError("The private clip archive could not be checked.") from exc
            finally:
                staged.unlink(missing_ok=True)
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        data = handle.read(MAX_SEEDANCE_MP4 + 1)
    if not 0 < len(data) <= MAX_SEEDANCE_MP4:
        raise VideoGenerationError("The saved Seedance clip is empty or exceeds the studio's download limit.")
    return data
