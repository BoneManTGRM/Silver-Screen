"""Runtime health diagnostics for local, CI, and container deployments."""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .media import media_capabilities
from .runtime import DEFAULT_RUNS_DIR, utc_now
from .science import APP_VERSION


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _ffmpeg_path() -> str | None:
    system_path = shutil.which("ffmpeg")
    if system_path:
        return system_path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _check_output_root(root: Path) -> tuple[bool, str | None]:
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="health_", dir=root):
            pass
        return True, None
    except Exception as exc:
        return False, str(exc)


def health_report(output_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(
        output_root or os.getenv("SILVER_SCREEN_RUNS_DIR") or DEFAULT_RUNS_DIR
    ).expanduser()
    writable, write_error = _check_output_root(root)
    capabilities = media_capabilities()
    ffmpeg = _ffmpeg_path()
    checks = {
        "corePackage": True,
        "outputWritable": writable,
        "streamlit": _module_available("streamlit"),
        "pillow": capabilities["pillow"],
        "numpy": _module_available("numpy"),
        "moviepy": capabilities["video"],
        "ffmpeg": bool(ffmpeg),
    }
    critical_ok = checks["corePackage"] and checks["outputWritable"]
    media_ok = checks["pillow"]
    if not critical_ok:
        status = "unhealthy"
    elif media_ok and checks["streamlit"]:
        status = "ready"
    else:
        status = "degraded"
    notes: list[str] = []
    if write_error:
        notes.append(f"Output root is not writable: {write_error}")
    if not checks["streamlit"]:
        notes.append("Streamlit is not installed; CLI and core pipeline remain available.")
    if not checks["pillow"]:
        notes.append("Pillow is not installed; media cards are disabled.")
    if not checks["moviepy"] or not checks["ffmpeg"]:
        notes.append("Video encoding is unavailable; PNG media cards remain supported when Pillow is present.")
    return {
        "status": status,
        "ready": critical_ok,
        "version": APP_VERSION,
        "checkedAt": utc_now(),
        "outputRoot": str(root.resolve()),
        "checks": checks,
        "ffmpegPath": ffmpeg,
        "notes": notes,
    }
