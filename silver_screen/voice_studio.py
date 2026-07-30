"""Durable authorized voices, narration, subtitles, and voiced-film assembly."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .ai_video import assemble_clips, verify_mp4
from .runtime import atomic_write_json, atomic_write_text, slugify, utc_now
from .voice_providers import (
    OPENAI_BUILTIN_VOICES,
    diagnose_voice_error,
    make_voice_provider,
    provider_capabilities,
)

VOICE_SCHEMA_VERSION = 1
VOICE_MODES = {"dialogue+narration", "dialogue", "narration"}
VOICE_PROVIDERS = {"openai", "elevenlabs", "manual"}
SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".wave", ".m4a", ".aac", ".ogg", ".flac", ".webm", ".mp4"
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ProviderFactory = Callable[[dict[str, Any]], Any]


class VoiceStudioError(RuntimeError):
    """Raised when authorized voice production cannot complete safely."""


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
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.exists():
            return str(sibling)
    return None


def voice_capabilities() -> dict[str, Any]:
    return {
        **provider_capabilities(),
        "ffmpeg": bool(_ffmpeg_path()),
        "ffprobe": bool(_ffprobe_path()),
        "audioMixing": bool(_ffmpeg_path()),
        "manualTracks": True,
        "subtitles": True,
        "supportedModes": sorted(VOICE_MODES),
    }


def _safe_path(root: Path, value: str | os.PathLike[str]) -> Path:
    path = (root / value).resolve()
    base = root.resolve()
    if path != base and base not in path.parents:
        raise VoiceStudioError("Voice artifact escaped the production workspace")
    return path


def _relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    base = root.resolve()
    if base not in resolved.parents:
        raise VoiceStudioError("Voice artifact escaped the production workspace")
    return resolved.relative_to(base).as_posix()


def _paths(root: Path) -> dict[str, Path]:
    audio = root / "audio"
    return {
        "root": audio,
        "config": audio / "voice_config.json",
        "cast": audio / "voice_cast.json",
        "plan": audio / "voice_plan.json",
        "runtime": audio / "voice_runtime.json",
        "scars": audio / "voice_scar_memory.json",
        "subtitles": audio / "subtitles.srt",
        "lines": audio / "lines",
        "dubbed": audio / "dubbed_clips",
        "inputs": audio / "inputs",
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_upload(upload: Any) -> bytes:
    if upload is None:
        raise VoiceStudioError("Required voice upload is missing")
    if isinstance(upload, (str, os.PathLike)):
        path = Path(upload)
        if not path.is_file():
            raise VoiceStudioError(f"Voice upload does not exist: {path}")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise VoiceStudioError(f"Voice upload exceeds 20 MB: {path.name}")
        return path.read_bytes()
    size = getattr(upload, "size", None)
    if isinstance(size, int) and size > MAX_UPLOAD_BYTES:
        raise VoiceStudioError("Voice upload exceeds 20 MB")
    if not hasattr(upload, "read"):
        raise VoiceStudioError("Unsupported voice upload")
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if hasattr(upload, "seek"):
        upload.seek(0)
    if len(data) > MAX_UPLOAD_BYTES:
        raise VoiceStudioError("Voice upload exceeds 20 MB")
    return data


def _upload_name(upload: Any, fallback: str) -> str:
    raw = Path(upload).name if isinstance(upload, (str, os.PathLike)) else str(
        getattr(upload, "name", "") or fallback
    )
    suffix = Path(raw).suffix.lower()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        suffix = Path(fallback).suffix or ".mp3"
    return f"{slugify(Path(raw).stem, fallback=Path(fallback).stem)}{suffix}"


def _persist_upload(upload: Any, destination: Path) -> Path:
    data = _read_upload(upload)
    if len(data) < 256:
        raise VoiceStudioError(f"Voice upload is empty or too small: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb", delete=False, dir=destination.parent, prefix=f".{destination.name}."
    ) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, destination)
    return destination


def normalize_voice_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    provider = str(source.get("provider") or "openai").lower().strip()
    if provider not in VOICE_PROVIDERS:
        provider = "openai"
    mode = str(source.get("mode") or "dialogue+narration").lower().strip()
    if mode not in VOICE_MODES:
        mode = "dialogue+narration"
    voice_map = source.get("voice_map")
    if not isinstance(voice_map, dict):
        voice_map = {}
    config = {
        "schemaVersion": VOICE_SCHEMA_VERSION,
        "enabled": bool(source.get("enabled")),
        "provider": provider,
        "mode": mode,
        "model": str(source.get("model") or "").strip(),
        "lead_voice": str(source.get("lead_voice") or "coral").strip(),
        "supporting_voice": str(source.get("supporting_voice") or "onyx").strip(),
        "narrator_voice": str(source.get("narrator_voice") or "cedar").strip(),
        "voice_map": {str(k): str(v) for k, v in voice_map.items()},
        "instructions": str(
            source.get("instructions")
            or "Deliver an expressive cinematic performance with clear diction."
        )[:2000],
        "speed": max(0.7, min(1.3, float(source.get("speed", 1.0) or 1.0))),
        "max_retries_per_line": max(
            0, min(5, int(source.get("max_retries_per_line", 1) or 0))
        ),
        "preserve_source_audio": bool(source.get("preserve_source_audio", False)),
        "subtitles": bool(source.get("subtitles", True)),
        "authorization_confirmed": bool(source.get("authorization_confirmed", False)),
        "custom_voice": bool(source.get("custom_voice", False)),
        "custom_voice_name": str(
            source.get("custom_voice_name") or "Silver Screen Voice"
        )[:120],
        "custom_voice_id": str(source.get("custom_voice_id") or "").strip(),
        "consent_id": str(source.get("consent_id") or "").strip(),
        "language": str(source.get("language") or "en-US")[:35],
        "voice_sample_path": str(source.get("voice_sample_path") or ""),
        "consent_recording_path": str(source.get("consent_recording_path") or ""),
        "manual_track_paths": [
            str(item) for item in source.get("manual_track_paths") or [] if item
        ],
        "allow_original_fallback": bool(source.get("allow_original_fallback", True)),
        "line_delay_seconds": max(
            0.0, min(2.0, float(source.get("line_delay_seconds", 0.2) or 0.0))
        ),
    }
    if provider == "openai":
        for key, fallback in (
            ("lead_voice", "coral"),
            ("supporting_voice", "onyx"),
            ("narrator_voice", "cedar"),
        ):
            value = str(config[key])
            if value not in OPENAI_BUILTIN_VOICES and not value.startswith("voice_"):
                config[key] = fallback
    return config


def _extract_request(
    voice_inputs: list[Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    for item in voice_inputs or []:
        if isinstance(item, dict) and (
            "enabled" in item or "provider" in item or "mode" in item
        ):
            request = dict(item)
            uploads = {
                "voice_sample": request.pop("voice_sample", None),
                "consent_recording": request.pop("consent_recording", None),
                "manual_tracks": list(request.pop("manual_tracks", None) or []),
            }
            return request, uploads
    return None, {}


def prepare_voice_config(
    root: Path, voice_inputs: list[Any] | None
) -> dict[str, Any]:
    paths = _paths(root)
    existing = _load_json(paths["config"])
    request, uploads = _extract_request(voice_inputs)
    if request is None:
        return normalize_voice_config(existing)
    config = normalize_voice_config(request)
    if uploads.get("voice_sample") is not None:
        if not config["authorization_confirmed"]:
            raise VoiceStudioError(
                "Custom voice enrollment requires explicit authorization confirmation"
            )
        upload = uploads["voice_sample"]
        destination = paths["inputs"] / f"sample-{_upload_name(upload, 'sample.wav')}"
        config["voice_sample_path"] = _relative(
            root, _persist_upload(upload, destination)
        )
    if uploads.get("consent_recording") is not None:
        if not config["authorization_confirmed"]:
            raise VoiceStudioError(
                "Custom voice enrollment requires explicit authorization confirmation"
            )
        upload = uploads["consent_recording"]
        destination = paths["inputs"] / f"consent-{_upload_name(upload, 'consent.wav')}"
        config["consent_recording_path"] = _relative(
            root, _persist_upload(upload, destination)
        )
    saved: list[str] = []
    for index, upload in enumerate(uploads.get("manual_tracks") or [], start=1):
        if not config["authorization_confirmed"]:
            raise VoiceStudioError(
                "Uploaded voice tracks require authorization confirmation"
            )
        destination = (
            paths["inputs"]
            / "manual"
            / f"{index:04d}-{_upload_name(upload, f'track-{index:04d}.mp3')}"
        )
        saved.append(_relative(root, _persist_upload(upload, destination)))
    if saved:
        config["manual_track_paths"] = saved
    for key in (
        "custom_voice_id",
        "consent_id",
        "voice_sample_path",
        "consent_recording_path",
        "manual_track_paths",
    ):
        if not config.get(key) and existing.get(key):
            config[key] = existing[key]
    paths["root"].mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["config"], config)
    return config


def validate_voice_config(config: dict[str, Any]) -> None:
    if not config.get("enabled"):
        return
    provider = str(config.get("provider") or "")
    capabilities = provider_capabilities()
    if provider in {"openai", "elevenlabs"} and not capabilities.get(provider):
        key = "OPENAI_API_KEY" if provider == "openai" else "ELEVENLABS_API_KEY"
        raise VoiceStudioError(f"{key} is not configured for voice generation")
    if provider == "elevenlabs":
        for key in ("lead_voice", "supporting_voice", "narrator_voice"):
            if not str(config.get(key) or "").strip():
                raise VoiceStudioError(f"{key} requires an ElevenLabs voice ID")
    if config.get("custom_voice"):
        if provider != "openai":
            raise VoiceStudioError(
                "In-app custom voice enrollment currently uses OpenAI consent"
            )
        if not config.get("authorization_confirmed"):
            raise VoiceStudioError(
                "Custom voice enrollment requires explicit authorization confirmation"
            )
        if not config.get("custom_voice_id") and (
            not config.get("voice_sample_path")
            or not config.get("consent_recording_path")
        ):
            raise VoiceStudioError(
                "Custom voice enrollment requires both a voice sample and consent recording"
            )
    if provider == "manual" and not config.get("manual_track_paths"):
        raise VoiceStudioError(
            "Manual voice mode requires one or more uploaded audio tracks"
        )
    if not _ffmpeg_path():
        raise VoiceStudioError("FFmpeg is required to mix voices into video")


def _characters(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in state.get("characters") or [] if isinstance(item, dict)
    ]


def build_voice_cast(
    state: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    records = []
    mapping = dict(config.get("voice_map") or {})
    for index, character in enumerate(_characters(state)):
        name = str(character.get("name") or f"Character {index + 1}")
        character_id = str(character.get("id") or name)
        voice = (
            mapping.get(character_id)
            or mapping.get(name)
            or (
                config.get("lead_voice")
                if index == 0
                else config.get("supporting_voice")
            )
        )
        records.append(
            {
                "characterId": character_id,
                "character": name,
                "role": character.get("role"),
                "provider": config.get("provider"),
                "voice": voice,
            }
        )
    return {
        "schemaVersion": VOICE_SCHEMA_VERSION,
        "provider": config.get("provider"),
        "model": config.get("model"),
        "characters": records,
        "narrator": {
            "characterId": "narrator",
            "character": "Narrator",
            "provider": config.get("provider"),
            "voice": config.get("narrator_voice"),
        },
    }


def _cast_voice(cast: dict[str, Any], speaker: str) -> str:
    for item in cast.get("characters") or []:
        if isinstance(item, dict) and str(
            item.get("character") or ""
        ).casefold() == speaker.casefold():
            return str(item.get("voice") or "")
    return str((cast.get("narrator") or {}).get("voice") or "")


def _dialogue(scene: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for shot in scene.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        raw = str(shot.get("dialogue") or "").strip()
        if not raw:
            continue
        match = re.match(r'^\s*([^:]{1,100}):\s*["“]?(.*?)["”]?\s*$', raw)
        if match:
            speaker = match.group(1).strip()
            text = match.group(2).strip().strip('"“”')
        else:
            speaker, text = "Narrator", raw
        if text:
            result.append((speaker, text))
    return result


def _compact_text(text: str, duration: float, speed: float) -> str:
    cleaned = " ".join(str(text or "").replace("\n", " ").split()).strip(' "“”')
    if not cleaned:
        return ""
    target = max(4, int(max(1.0, duration - 0.5) * 2.25 * speed))
    words = cleaned.split()
    if len(words) <= target:
        return cleaned
    candidate = " ".join(words[:target])
    punctuation = max(
        candidate.rfind("."), candidate.rfind("!"), candidate.rfind("?")
    )
    if punctuation >= max(15, len(candidate) // 2):
        return candidate[: punctuation + 1]
    return candidate if candidate[-1] in ".!?" else candidate + "."


def build_voice_plan(
    state: dict[str, Any],
    video_result: dict[str, Any],
    config: dict[str, Any],
    cast: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    queue = video_result.get("queue") or {}
    scenes = {
        int(scene.get("number", 0) or 0): scene
        for scene in state.get("scenes") or []
        if isinstance(scene, dict)
    }
    previous = {
        str(item.get("shotId")): item
        for item in (existing or {}).get("lines") or []
        if isinstance(item, dict)
    }
    lines = []
    for shot in sorted(
        [item for item in queue.get("shots") or [] if isinstance(item, dict)],
        key=lambda item: int(item.get("order", 0) or 0),
    ):
        shot_id = str(shot.get("id") or "")
        scene_number = int(
            (shot.get("sourceScene") or {}).get("number", 0) or 0
        )
        scene = scenes.get(scene_number, {})
        dialogues = _dialogue(scene)
        mode = str(config.get("mode") or "dialogue+narration")
        segment = int(shot.get("segment", 1) or 1)
        speaker = text = source = ""
        if mode in {"dialogue", "dialogue+narration"} and dialogues:
            speaker, text = dialogues[(segment - 1) % len(dialogues)]
            source = "dialogue"
        if not text and mode in {"narration", "dialogue+narration"}:
            speaker = "Narrator"
            text = str(scene.get("summary") or scene.get("action") or "")
            source = "narration"
        target = float(
            shot.get("verifiedDurationSeconds")
            or shot.get("plannedDurationSeconds")
            or 8
        )
        text = _compact_text(text, target, float(config.get("speed", 1.0)))
        voice = _cast_voice(cast, speaker or "Narrator")
        signature = hashlib.sha256(
            "|".join(
                [
                    text,
                    voice,
                    str(config.get("provider") or ""),
                    str(config.get("model") or ""),
                    str(config.get("instructions") or ""),
                    str(config.get("speed") or 1),
                ]
            ).encode("utf-8")
        ).hexdigest()
        prior = previous.get(shot_id, {})
        reusable = bool(
            prior.get("signature") == signature
            and prior.get("status") == "verified"
            and prior.get("audioPath")
            and prior.get("dubbedPath")
        )
        key = shot_id or f"shot_{int(shot.get('order', 0) or 0):04d}"
        lines.append(
            {
                "id": f"voice_{key}",
                "shotId": shot_id,
                "order": int(shot.get("order", 0) or 0),
                "scene": scene_number,
                "speaker": speaker,
                "source": source,
                "text": text,
                "voice": voice,
                "signature": signature,
                "videoStatus": str(shot.get("status") or "pending"),
                "provider": config.get("provider"),
                "model": config.get("model"),
                "targetDurationSeconds": target,
                "status": (
                    "verified"
                    if reusable
                    else ("pending" if text else "skipped")
                ),
                "attempts": int(prior.get("attempts", 0) or 0) if reusable else 0,
                "audioPath": prior.get("audioPath") if reusable else None,
                "dubbedPath": prior.get("dubbedPath") if reusable else None,
                "audioDurationSeconds": (
                    prior.get("audioDurationSeconds") if reusable else None
                ),
                "lastError": prior.get("lastError") if reusable else None,
                "repairs": list(prior.get("repairs") or []) if reusable else [],
                "createdAt": prior.get("createdAt") or utc_now(),
                "completedAt": prior.get("completedAt") if reusable else None,
            }
        )
    return {
        "schemaVersion": VOICE_SCHEMA_VERSION,
        "productionId": f"{queue.get('productionId') or 'video'}_voice",
        "createdAt": (existing or {}).get("createdAt") or utc_now(),
        "updatedAt": utc_now(),
        "status": (existing or {}).get("status") or "planned",
        "config": config,
        "cast": cast,
        "lines": lines,
        "events": list((existing or {}).get("events") or []),
        "scars": list((existing or {}).get("scars") or []),
        "metrics": {},
        "msil": {},
        "artifacts": dict((existing or {}).get("artifacts") or {}),
        "verifiedVideoShots": sum(
            1
            for shot in queue.get("shots") or []
            if isinstance(shot, dict) and shot.get("status") == "verified"
        ),
    }


def _record(
    plan: dict[str, Any],
    event: str,
    *,
    line_id: str | None = None,
    detail: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    events = plan.setdefault("events", [])
    events.append(
        {
            "at": utc_now(),
            "event": event,
            "lineId": line_id,
            "detail": detail,
            "data": data or {},
        }
    )
    if len(events) > 2000:
        del events[:-2000]


def _metrics(plan: dict[str, Any]) -> None:
    lines = [
        item for item in plan.get("lines") or [] if isinstance(item, dict)
    ]
    eligible = [
        item
        for item in lines
        if item.get("text") and item.get("videoStatus") == "verified"
    ]
    completed = [
        item for item in eligible if item.get("status") == "verified"
    ]
    blocked = [
        item for item in eligible if item.get("status") == "blocked"
    ]
    attempts = sum(int(item.get("attempts", 0) or 0) for item in eligible)
    provider_calls = (
        0
        if str((plan.get("config") or {}).get("provider") or "") == "manual"
        else attempts
    )
    repairs = sum(len(item.get("repairs") or []) for item in eligible)
    verified_video = int(plan.get("verifiedVideoShots", 0) or 0)
    completion = len(completed) / len(eligible) if eligible else 1.0
    coverage = min(1.0, len(completed) / max(1, verified_video))
    failure_rate = len(blocked) / len(eligible) if eligible else 0.0
    stability = max(
        0.0,
        min(
            1.0,
            completion * 0.6
            + coverage * 0.2
            + (1.0 - failure_rate) * 0.2,
        ),
    )
    verdict = (
        "attention"
        if blocked
        else "stable"
        if completion >= 1
        else "checkpoint"
        if completed
        else "planning"
    )
    plan["metrics"] = {
        "plannedLines": len(eligible),
        "generatedLines": len(completed),
        "dubbedClips": len(completed),
        "verifiedVideoShots": verified_video,
        "providerCalls": provider_calls,
        "renderAttempts": attempts,
        "repairs": repairs,
        "voiceSeconds": round(
            sum(
                float(item.get("audioDurationSeconds", 0) or 0)
                for item in completed
            ),
            3,
        ),
        "completionRatio": round(completion, 6),
        "coverageRatio": round(coverage, 6),
        "failedLines": len(blocked),
    }
    plan["msil"] = {
        "stabilityIndex": round(stability, 6),
        "failureRate": round(failure_rate, 6),
        "coverage": round(coverage, 6),
        "verdict": verdict,
    }


def _save(root: Path, plan: dict[str, Any], cast: dict[str, Any]) -> None:
    paths = _paths(root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    plan["updatedAt"] = utc_now()
    _metrics(plan)
    atomic_write_json(paths["cast"], cast)
    atomic_write_json(paths["plan"], plan)
    atomic_write_json(paths["scars"], plan.get("scars") or [])
    atomic_write_json(
        paths["runtime"],
        {
            "schemaVersion": VOICE_SCHEMA_VERSION,
            "productionId": plan.get("productionId"),
            "status": plan.get("status"),
            "updatedAt": plan.get("updatedAt"),
            "metrics": plan.get("metrics") or {},
            "msil": plan.get("msil") or {},
            "artifacts": plan.get("artifacts") or {},
            "lastError": next(
                (
                    item.get("lastError")
                    for item in plan.get("lines") or []
                    if isinstance(item, dict) and item.get("lastError")
                ),
                None,
            ),
        },
    )


def verify_audio(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size < 512:
        raise VoiceStudioError(f"Generated audio is missing or too small: {path}")
    metadata = {"bytes": path.stat().st_size, "durationSeconds": None}
    ffprobe = _ffprobe_path()
    if not ffprobe:
        return metadata
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise VoiceStudioError(f"FFprobe could not decode generated audio: {path}")
    try:
        duration = float(
            (json.loads(completed.stdout).get("format") or {}).get(
                "duration"
            )
            or 0
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise VoiceStudioError(
            f"Generated audio has no readable duration: {path}"
        ) from exc
    if duration <= 0.05:
        raise VoiceStudioError(f"Generated audio duration is invalid: {path}")
    metadata["durationSeconds"] = round(duration, 3)
    return metadata


def _has_audio(path: Path) -> bool:
    ffprobe = _ffprobe_path()
    if not ffprobe:
        return False
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def mix_voice_into_clip(
    video_path: Path,
    voice_path: Path,
    destination: Path,
    *,
    duration: float,
    preserve_source_audio: bool,
    delay_seconds: float = 0.2,
) -> Path:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise VoiceStudioError("FFmpeg is required to mix voices into video")
    verify_mp4(video_path)
    verify_audio(voice_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.2, duration)
    delay = max(0, int(delay_seconds * 1000))
    voice_filter = (
        f"[1:a]aresample=48000,adelay={delay}:all=1,apad,"
        f"atrim=0:{duration:.3f},volume=1.15[voice]"
    )
    if preserve_source_audio and _has_audio(video_path):
        graph = (
            f"[0:a]aresample=48000,volume=0.22,apad,"
            f"atrim=0:{duration:.3f}[base];{voice_filter};"
            "[base][voice]amix=inputs=2:duration=first:"
            "dropout_transition=0:normalize=0[aout]"
        )
    else:
        graph = voice_filter.replace("[voice]", "[aout]")
    completed = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(voice_path),
            "-filter_complex",
            graph,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        raise VoiceStudioError(
            f"FFmpeg voice mix failed: {completed.stderr[-1800:]}"
        )
    verify_mp4(destination, expected_duration=duration)
    return destination


def _shot_map(video_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(shot.get("id") or ""): shot
        for shot in (video_result.get("queue") or {}).get("shots") or []
        if isinstance(shot, dict)
    }


def _video_path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = str(shot.get("path") or "")
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else _safe_path(root, value)


def _reconcile(
    root: Path,
    plan: dict[str, Any],
    verified: dict[str, dict[str, Any]],
) -> None:
    for line in plan.get("lines") or []:
        if not isinstance(line, dict):
            continue
        shot_id = str(line.get("shotId") or "")
        line["videoStatus"] = (
            str(verified.get(shot_id, {}).get("status") or "pending")
            if shot_id
            else "pending"
        )
        if line.get("status") != "verified":
            continue
        try:
            if shot_id not in verified:
                raise VoiceStudioError("Matching video shot is no longer verified")
            verify_audio(_safe_path(root, str(line.get("audioPath") or "")))
            verify_mp4(
                _safe_path(root, str(line.get("dubbedPath") or "")),
                expected_duration=float(
                    line.get("targetDurationSeconds", 0) or 0
                ),
            )
        except Exception as exc:
            line.update(
                {
                    "status": "pending",
                    "lastError": f"Reconciliation: {exc}",
                    "audioPath": None,
                    "dubbedPath": None,
                    "audioDurationSeconds": None,
                    "completedAt": None,
                }
            )


def _manual_track(
    root: Path,
    config: dict[str, Any],
    line: dict[str, Any],
    index: int,
) -> Path:
    tracks = [
        _safe_path(root, item)
        for item in config.get("manual_track_paths") or []
    ]
    shot_id = str(line.get("shotId") or "").lower()
    for track in tracks:
        if shot_id and shot_id in track.stem.lower():
            return track
    if index < len(tracks):
        return tracks[index]
    raise VoiceStudioError(
        f"No manual audio track was supplied for {line.get('shotId')}"
    )


def _repair(
    line: dict[str, Any], diagnosis: str, attempt: int
) -> dict[str, Any]:
    repair = {
        "attempt": attempt,
        "code": diagnosis,
        "at": utc_now(),
        "strategy": "retry_same_line",
    }
    if diagnosis in {"invalid_audio", "unknown"}:
        repair["strategy"] = "shorten_and_simplify"
        words = str(line.get("text") or "").split()
        if len(words) > 6:
            line["text"] = " ".join(
                words[: max(6, math.ceil(len(words) * 0.75))]
            )
        line["instructionsOverride"] = (
            "Speak clearly, naturally, and without sound effects."
        )
    elif diagnosis == "rate_limit":
        repair["strategy"] = "bounded_backoff"
    elif diagnosis == "temporary":
        repair["strategy"] = "retry_after_temporary_failure"
    return repair


def _reinforce(
    plan: dict[str, Any], line: dict[str, Any], repair: dict[str, Any]
) -> None:
    key = (
        f"{repair.get('strategy')}:{line.get('speaker')}:"
        f"{line.get('provider')}"
    )
    scars = plan.setdefault("scars", [])
    for scar in scars:
        if isinstance(scar, dict) and scar.get("key") == key:
            scar["uses"] = int(scar.get("uses", 1) or 1) + 1
            scar["lastUsedAt"] = utc_now()
            return
    scars.append(
        {
            "key": key,
            "strategy": repair.get("strategy"),
            "speaker": line.get("speaker"),
            "provider": line.get("provider"),
            "uses": 1,
            "createdAt": utc_now(),
            "lastUsedAt": utc_now(),
        }
    )


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_subtitles(plan: dict[str, Any]) -> str:
    blocks, cursor, number = [], 0.0, 1
    for line in sorted(
        [item for item in plan.get("lines") or [] if isinstance(item, dict)],
        key=lambda item: int(item.get("order", 0) or 0),
    ):
        duration = float(line.get("targetDurationSeconds", 0) or 0)
        if line.get("status") == "verified" and line.get("text"):
            speaker = str(line.get("speaker") or "")
            prefix = f"{speaker}: " if speaker and speaker != "Narrator" else ""
            blocks.append(
                "\n".join(
                    [
                        str(number),
                        (
                            f"{_srt_time(cursor + 0.2)} --> "
                            f"{_srt_time(cursor + max(0.4, duration - 0.15))}"
                        ),
                        f"{prefix}{line.get('text')}",
                    ]
                )
            )
            number += 1
        cursor += duration
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _custom_voice(
    root: Path, config: dict[str, Any], provider: Any
) -> dict[str, Any]:
    if not config.get("custom_voice") or config.get("custom_voice_id"):
        return config
    if getattr(provider, "name", "") != "openai":
        raise VoiceStudioError(
            "Custom voice enrollment requires the OpenAI provider"
        )
    sample = _safe_path(root, config["voice_sample_path"])
    consent_recording = _safe_path(
        root, config["consent_recording_path"]
    )
    consent_id = str(config.get("consent_id") or "")
    if not consent_id:
        consent_id = provider.create_consent(
            recording=consent_recording,
            name=f"{config.get('custom_voice_name')} consent",
            language=str(config.get("language") or "en-US"),
        )
        config["consent_id"] = consent_id
        atomic_write_json(_paths(root)["config"], config)
    voice_id = provider.create_custom_voice(
        audio_sample=sample,
        consent_id=consent_id,
        name=str(
            config.get("custom_voice_name") or "Silver Screen Voice"
        ),
    )
    config["custom_voice_id"] = voice_id
    config["lead_voice"] = voice_id
    sample.unlink(missing_ok=True)
    consent_recording.unlink(missing_ok=True)
    config["voice_sample_path"] = ""
    config["consent_recording_path"] = ""
    atomic_write_json(_paths(root)["config"], config)
    return config


def process_voice_production(
    state: dict[str, Any],
    video_result: dict[str, Any],
    out_dir: str | os.PathLike[str],
    voice_inputs: list[Any] | None = None,
    *,
    provider_factory: ProviderFactory = make_voice_provider,
) -> dict[str, Any]:
    """Generate or resume voices only for verified video clips."""

    root = Path(out_dir).resolve()
    paths = _paths(root)
    config = prepare_voice_config(root, voice_inputs)
    if not config.get("enabled"):
        return {
            "ok": True,
            "enabled": False,
            "status": "disabled",
            "note": "Voice production is disabled.",
            "capabilities": voice_capabilities(),
        }
    validate_voice_config(config)
    cast = build_voice_cast(state, config)
    plan = build_voice_plan(
        state,
        video_result,
        config,
        cast,
        _load_json(paths["plan"]),
    )
    shots = _shot_map(video_result)
    verified = {
        shot_id: shot
        for shot_id, shot in shots.items()
        if shot.get("status") == "verified"
    }
    plan["verifiedVideoShots"] = len(verified)
    _reconcile(root, plan, verified)
    if not verified:
        plan["status"] = "planned"
        _save(root, plan, cast)
        return {
            "ok": True,
            "enabled": True,
            "status": "planned",
            "provider": config.get("provider"),
            "model": config.get("model"),
            "cast": cast,
            "plan": plan,
            "metrics": plan.get("metrics") or {},
            "msil": plan.get("msil") or {},
            "scars": plan.get("scars") or [],
            "warnings": [],
            "error": None,
            "assembled_video_path": None,
            "final_video_path": None,
            "partial_video_path": None,
            "subtitles_path": None,
            "config_path": str(paths["config"]),
            "cast_path": str(paths["cast"]),
            "plan_path": str(paths["plan"]),
            "runtime_path": str(paths["runtime"]),
            "scar_memory_path": str(paths["scars"]),
            "line_audio_paths": [],
            "dubbed_clip_paths": [],
            "capabilities": voice_capabilities(),
            "note": (
                "Voice plan is ready and will begin after the first "
                "video clip is verified."
            ),
        }

    provider = provider_factory(config)
    config = _custom_voice(root, config, provider)
    cast = build_voice_cast(state, config)
    plan["cast"], plan["config"], plan["status"] = cast, config, "running"
    _save(root, plan, cast)

    hard_error: str | None = None
    warnings: list[str] = []
    ordered_lines = sorted(
        [item for item in plan.get("lines") or [] if isinstance(item, dict)],
        key=lambda item: int(item.get("order", 0) or 0),
    )
    for index, line in enumerate(ordered_lines):
        shot_id = str(line.get("shotId") or "")
        shot = verified.get(shot_id)
        if (
            not shot
            or not line.get("text")
            or line.get("status") == "verified"
        ):
            continue
        video_path = _video_path(root, shot)
        if video_path is None:
            line["status"] = "blocked"
            line["lastError"] = "Verified video shot has no MP4 path"
            hard_error = line["lastError"]
            _save(root, plan, cast)
            continue
        audio_path = paths["lines"] / f"{line.get('id')}.mp3"
        dubbed_path = paths["dubbed"] / f"{shot_id}.mp4"
        allowed = int(config.get("max_retries_per_line", 1) or 0) + 1
        while int(line.get("attempts", 0) or 0) < allowed:
            line["attempts"] = int(line.get("attempts", 0) or 0) + 1
            line["status"], line["lastError"] = "generating", None
            _save(root, plan, cast)
            try:
                if config.get("provider") == "manual":
                    manual = _manual_track(root, config, line, index)
                    suffix = manual.suffix.lower()
                    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
                        suffix = ".wav"
                    audio_path = (
                        paths["lines"] / f"{line.get('id')}{suffix}"
                    )
                    audio_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(manual, audio_path)
                else:
                    provider.synthesize(
                        text=str(line.get("text") or ""),
                        voice=str(
                            config.get("custom_voice_id")
                            or line.get("voice")
                            or config.get("narrator_voice")
                        ),
                        destination=audio_path,
                        instructions=str(
                            line.get("instructionsOverride")
                            or config.get("instructions")
                            or ""
                        ),
                        speed=float(config.get("speed", 1.0) or 1.0),
                        seed=int(line.get("order", 0) or 0),
                    )
                metadata = verify_audio(audio_path)
                mix_voice_into_clip(
                    video_path,
                    audio_path,
                    dubbed_path,
                    duration=float(
                        line.get("targetDurationSeconds", 8) or 8
                    ),
                    preserve_source_audio=bool(
                        config.get("preserve_source_audio")
                    ),
                    delay_seconds=float(
                        config.get("line_delay_seconds", 0.2) or 0
                    ),
                )
                line.update(
                    {
                        "audioPath": _relative(root, audio_path),
                        "dubbedPath": _relative(root, dubbed_path),
                        "audioDurationSeconds": float(
                            metadata.get("durationSeconds")
                            or line.get("targetDurationSeconds")
                            or 0
                        ),
                        "status": "verified",
                        "completedAt": utc_now(),
                        "lastError": None,
                    }
                )
                if line.get("repairs"):
                    _reinforce(plan, line, line["repairs"][-1])
                _record(
                    plan,
                    "voice_line_verified",
                    line_id=str(line.get("id")),
                    detail=f"{line.get('speaker')} voiced {shot_id}",
                )
                _save(root, plan, cast)
                break
            except Exception as exc:
                error = str(exc)
                diagnosis = diagnose_voice_error(error)
                repair = _repair(
                    line,
                    diagnosis.code,
                    int(line.get("attempts", 0) or 0) + 1,
                )
                line["lastError"], line["status"] = error, "pending"
                line.setdefault("repairs", []).append(repair)
                _record(
                    plan,
                    "voice_tgrm_repair",
                    line_id=str(line.get("id")),
                    detail=error,
                    data={
                        "diagnosis": diagnosis.code,
                        "repair": repair,
                        "retryable": diagnosis.retryable,
                    },
                )
                _save(root, plan, cast)
                if (
                    not diagnosis.retryable
                    or int(line.get("attempts", 0) or 0) >= allowed
                ):
                    line["status"] = "blocked"
                    hard_error = error
                    _record(
                        plan,
                        "voice_line_blocked",
                        line_id=str(line.get("id")),
                        detail=error,
                    )
                    _save(root, plan, cast)
                    break

    dubbed: list[Path] = []
    completed_lines = 0
    for shot_id, shot in sorted(
        verified.items(),
        key=lambda item: int(item[1].get("order", 0) or 0),
    ):
        line = next(
            (
                item
                for item in plan.get("lines") or []
                if isinstance(item, dict)
                and item.get("shotId") == shot_id
            ),
            None,
        )
        if (
            line
            and line.get("status") == "verified"
            and line.get("dubbedPath")
        ):
            dubbed.append(_safe_path(root, line["dubbedPath"]))
            completed_lines += 1
        elif config.get("allow_original_fallback"):
            original = _video_path(root, shot)
            if original is not None:
                dubbed.append(original)

    eligible = sum(
        1
        for line in plan.get("lines") or []
        if isinstance(line, dict)
        and line.get("text")
        and str(line.get("shotId") or "") in verified
    )
    voice_complete = eligible == completed_lines
    video_complete = str(video_result.get("status") or "") == "complete"
    assembled: Path | None = None
    if dubbed:
        name = (
            "final_film_with_voices.mp4"
            if video_complete and voice_complete
            else "partial_film_with_voices.mp4"
        )
        assembled = assemble_clips(dubbed, paths["root"] / name)
        plan.setdefault("artifacts", {})[
            (
                "finalFilm"
                if video_complete and voice_complete
                else "partialFilm"
            )
        ] = _relative(root, assembled)

    subtitles_path: Path | None = None
    if config.get("subtitles"):
        subtitles = render_subtitles(plan)
        if subtitles:
            subtitles_path = paths["subtitles"]
            atomic_write_text(subtitles_path, subtitles)
            plan.setdefault("artifacts", {})["subtitles"] = _relative(
                root, subtitles_path
            )

    plan["verifiedVideoShots"] = len(verified)
    _metrics(plan)
    generated = int(
        (plan.get("metrics") or {}).get("generatedLines", 0) or 0
    )
    blocked = any(
        isinstance(line, dict)
        and line.get("videoStatus") == "verified"
        and line.get("status") == "blocked"
        for line in plan.get("lines") or []
    )
    status = (
        "blocked"
        if hard_error or blocked
        else "complete"
        if video_complete and voice_complete
        else "partial"
        if generated or verified
        else "planned"
    )
    plan["status"] = status
    if hard_error:
        warnings.append(hard_error)
    _save(root, plan, cast)

    return {
        "ok": status in {"complete", "partial"},
        "enabled": True,
        "status": status,
        "provider": config.get("provider"),
        "model": config.get("model"),
        "cast": cast,
        "plan": plan,
        "metrics": plan.get("metrics") or {},
        "msil": plan.get("msil") or {},
        "scars": plan.get("scars") or [],
        "warnings": warnings,
        "error": hard_error,
        "assembled_video_path": str(assembled) if assembled else None,
        "final_video_path": (
            str(assembled)
            if assembled and status == "complete"
            else None
        ),
        "partial_video_path": (
            str(assembled)
            if assembled and status != "complete"
            else None
        ),
        "subtitles_path": (
            str(subtitles_path) if subtitles_path else None
        ),
        "config_path": str(paths["config"]),
        "cast_path": str(paths["cast"]),
        "plan_path": str(paths["plan"]),
        "runtime_path": str(paths["runtime"]),
        "scar_memory_path": str(paths["scars"]),
        "line_audio_paths": [
            str(_safe_path(root, line["audioPath"]))
            for line in plan.get("lines") or []
            if isinstance(line, dict) and line.get("audioPath")
        ],
        "dubbed_clip_paths": [
            str(_safe_path(root, line["dubbedPath"]))
            for line in plan.get("lines") or []
            if isinstance(line, dict) and line.get("dubbedPath")
        ],
        "capabilities": voice_capabilities(),
        "note": (
            f"Generated {generated} voice line(s) for "
            f"{len(verified)} verified video clip(s). Status: {status}."
        ),
    }
