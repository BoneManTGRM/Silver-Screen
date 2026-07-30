"""Audio validation, mixing, continuity, and subtitle utilities."""
from __future__ import annotations
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any
from .ai_video import verify_mp4
from .runtime import utc_now
from .voice_config import (
    AUDIO_SUFFIXES,
    VoiceStudioError,
    _ffmpeg_path,
    _ffprobe_path,
    _relative,
    _safe_path,
)


def verify_audio(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size < 512:
        raise VoiceStudioError(f"Generated audio is missing or too small: {path}")
    metadata: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "durationSeconds": None,
    }
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
            (json.loads(completed.stdout).get("format") or {}).get("duration") or 0
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
    audio_path: Path,
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
    verify_audio(audio_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.2, float(duration))
    delay_ms = max(0, int(float(delay_seconds) * 1000))
    voice_filter = (
        f"[1:a]aresample=48000,adelay={delay_ms}:all=1,apad,"
        f"atrim=0:{duration:.3f},volume=1.15[voice]"
    )
    command = [ffmpeg, "-y", "-i", str(video_path), "-i", str(audio_path)]
    if preserve_source_audio and _has_audio(video_path):
        graph = (
            f"[0:a]aresample=48000,volume=0.22,apad,atrim=0:{duration:.3f}[base];"
            f"{voice_filter};[base][voice]amix=inputs=2:duration=first:"
            "dropout_transition=0:normalize=0[aout]"
        )
    else:
        graph = voice_filter.replace("[voice]", "[aout]")
    command.extend(
        [
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
        ]
    )
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=600
    )
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        raise VoiceStudioError(
            f"FFmpeg voice mix failed: {completed.stderr[-1800:]}"
        )
    verify_mp4(destination, expected_duration=duration)
    return destination


def _video_shots(video_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    queue = video_result.get("queue") or {}
    return {
        str(shot.get("id") or ""): shot
        for shot in queue.get("shots") or []
        if isinstance(shot, dict)
    }


def _video_path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = str(shot.get("path") or "")
    return _safe_path(root, value) if value else None


def _reconcile(
    root: Path,
    plan: dict[str, Any],
    verified_video: dict[str, dict[str, Any]],
) -> None:
    for line in plan.get("lines") or []:
        if not isinstance(line, dict):
            continue
        shot_id = str(line.get("shotId") or "")
        line["videoStatus"] = "verified" if shot_id in verified_video else "pending"
        if line.get("status") != "verified":
            continue
        try:
            if shot_id not in verified_video:
                raise VoiceStudioError("The matching video shot is no longer verified")
            if not line.get("audioPath") or not line.get("dubbedPath"):
                raise VoiceStudioError("Voice line is missing persisted artifacts")
            verify_audio(_safe_path(root, line["audioPath"]))
            verify_mp4(
                _safe_path(root, line["dubbedPath"]),
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
    eligible_index: int,
) -> Path:
    paths = [
        _safe_path(root, value) for value in config.get("manual_track_paths") or []
    ]
    shot_id = str(line.get("shotId") or "").casefold()
    for path in paths:
        if shot_id and shot_id in path.stem.casefold():
            return path
    if eligible_index < len(paths):
        return paths[eligible_index]
    raise VoiceStudioError(f"No manual audio track was supplied for {shot_id}")


def _repair_line(
    line: dict[str, Any], diagnosis_code: str, attempt: int
) -> dict[str, Any]:
    repair = {
        "attempt": attempt,
        "code": diagnosis_code,
        "at": utc_now(),
        "strategy": "retry_same_line",
    }
    if diagnosis_code in {"invalid_audio", "unknown"}:
        repair["strategy"] = "shorten_and_simplify"
        words = str(line.get("text") or "").split()
        if len(words) > 7:
            line["text"] = " ".join(
                words[: max(7, math.ceil(len(words) * 0.75))]
            )
        line["instructionsOverride"] = (
            "Speak clearly and naturally without sound effects."
        )
    elif diagnosis_code == "rate_limit":
        repair["strategy"] = "bounded_backoff"
    elif diagnosis_code == "temporary":
        repair["strategy"] = "retry_after_temporary_failure"
    return repair


def _reinforce(
    plan: dict[str, Any], line: dict[str, Any], repair: dict[str, Any]
) -> None:
    key = (
        f"{repair.get('strategy')}:{line.get('speaker')}:{line.get('provider')}"
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
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_subtitles(plan: dict[str, Any]) -> str:
    cursor = 0.0
    blocks: list[str] = []
    index = 1
    lines = sorted(
        [item for item in plan.get("lines") or [] if isinstance(item, dict)],
        key=lambda item: int(item.get("order", 0) or 0),
    )
    for line in lines:
        duration = float(line.get("targetDurationSeconds", 0) or 0)
        if line.get("status") == "verified" and line.get("text"):
            start = cursor + 0.2
            end = cursor + max(0.4, duration - 0.15)
            speaker = str(line.get("speaker") or "")
            prefix = f"{speaker}: " if speaker and speaker != "Narrator" else ""
            blocks.append(
                "\n".join(
                    [
                        str(index),
                        f"{_srt_time(start)} --> {_srt_time(end)}",
                        f"{prefix}{line.get('text')}",
                    ]
                )
            )
            index += 1
        cursor += duration
    return "\n\n".join(blocks) + ("\n" if blocks else "")
