"""Runtime health diagnostics for local, CI, and container deployments."""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .media import media_capabilities
from .runtime import resolve_runs_root, utc_now
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


def health_report(
    output_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_runs_root(output_root)
    writable, write_error = _check_output_root(root)
    capabilities = media_capabilities()
    voice = capabilities.get("voiceStudio") or {}
    ffmpeg = _ffmpeg_path()
    checks = {
        "corePackage": True,
        "outputWritable": writable,
        "streamlit": _module_available("streamlit"),
        "pillow": bool(capabilities.get("pillow")),
        "numpy": _module_available("numpy"),
        "moviepy": bool(capabilities.get("localPreview")),
        "ffmpeg": bool(ffmpeg),
        "aiVideoProvider": bool(capabilities.get("aiVideo")),
        "voiceMixing": bool(voice.get("audioMixing")),
        "openaiVoice": bool(voice.get("openai")),
        "elevenLabsVoice": bool(voice.get("elevenlabs")),
        "manualVoiceTracks": bool(voice.get("manual")),
    }
    critical_ok = checks["corePackage"] and checks["outputWritable"]
    if not critical_ok:
        status = "unhealthy"
    elif checks["pillow"] and checks["streamlit"]:
        status = "ready"
    else:
        status = "degraded"
    notes: list[str] = []
    if write_error:
        notes.append(f"Output root is not writable: {write_error}")
    if not checks["streamlit"]:
        notes.append(
            "Streamlit is not installed; CLI and core pipeline remain available."
        )
    if not checks["pillow"]:
        notes.append(
            "Pillow is not installed; static media cards are disabled."
        )
    if not checks["moviepy"] or not checks["ffmpeg"]:
        notes.append(
            "Local preview encoding is unavailable; AI video can still work "
            "when its provider is configured."
        )
    if not checks["aiVideoProvider"]:
        notes.append(
            "REPLICATE_API_TOKEN is not configured; actual AI video is unavailable."
        )
    if not checks["voiceMixing"]:
        notes.append(
            "FFmpeg audio mixing is unavailable; voiced-film assembly is disabled."
        )
    if not checks["openaiVoice"] and not checks["elevenLabsVoice"]:
        notes.append(
            "No speech API key is configured; authorized manual voice tracks remain available."
        )
    return {
        "status": status,
        "ready": critical_ok,
        "version": APP_VERSION,
        "checkedAt": utc_now(),
        "outputRoot": str(root.resolve()),
        "checks": checks,
        "mediaCapabilities": capabilities,
        "voiceCapabilities": voice,
        "ffmpegPath": ffmpeg,
        "notes": notes,
    }
