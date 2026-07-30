"""Professional script-to-screen timing, speech, captions, and final assembly.

This module is additive to Voice Studio. It accepts an operator-authored script,
parses speaker lines and optional timecodes, fits the dialogue to verified video
shots, synthesizes authorized voices, measures the generated audio, produces
line and word timing artifacts, mixes each shot, and assembles a professional
voiced film without modifying the accepted silent source clips.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

from .ai_video import assemble_clips, verify_mp4
from .runtime import atomic_write_json, atomic_write_text, utc_now
from .voice_audio import mix_voice_into_clip, verify_audio
from .voice_config import VoiceStudioError, _ffmpeg_path, _relative, _safe_path
from .voice_providers import make_voice_provider

TIMECODE_LINE = re.compile(
    r"^\s*(?:\[(?P<start>\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\s*(?:-->|-|to)\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\]\s*)?"
    r"(?P<speaker>[A-Za-z0-9 ._'’-]{1,80})\s*:\s*(?P<text>.+?)\s*$"
)
SCENE_LINE = re.compile(r"^\s*(?:INT\.|EXT\.|SCENE\s+\d+|#|\[SCENE).*", re.I)
WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class ScriptSyncConfig:
    provider: str = "openai"
    model: str = ""
    lead_voice: str = "coral"
    supporting_voice: str = "onyx"
    narrator_voice: str = "cedar"
    voice_map: dict[str, str] | None = None
    instructions: str = "Professional cinematic performance with natural pacing and clear diction."
    speed: float = 1.0
    words_per_minute: int = 145
    line_gap_seconds: float = 0.18
    head_padding_seconds: float = 0.20
    tail_padding_seconds: float = 0.18
    preserve_source_audio: bool = False
    burn_captions: bool = False
    caption_style: str = "cinematic"
    authorization_confirmed: bool = False

    def normalized(self) -> "ScriptSyncConfig":
        provider = self.provider.strip().lower()
        if provider not in {"openai", "elevenlabs"}:
            raise VoiceStudioError("Script Sync supports OpenAI or ElevenLabs speech providers")
        if provider == "elevenlabs" and not self.authorization_confirmed:
            raise VoiceStudioError("ElevenLabs voice IDs require explicit authorization confirmation")
        return ScriptSyncConfig(
            provider=provider,
            model=self.model.strip(),
            lead_voice=self.lead_voice.strip(),
            supporting_voice=self.supporting_voice.strip(),
            narrator_voice=self.narrator_voice.strip(),
            voice_map=dict(self.voice_map or {}),
            instructions=self.instructions.strip()[:2000],
            speed=max(0.7, min(1.3, float(self.speed))),
            words_per_minute=max(90, min(220, int(self.words_per_minute))),
            line_gap_seconds=max(0.0, min(1.5, float(self.line_gap_seconds))),
            head_padding_seconds=max(0.0, min(2.0, float(self.head_padding_seconds))),
            tail_padding_seconds=max(0.0, min(2.0, float(self.tail_padding_seconds))),
            preserve_source_audio=bool(self.preserve_source_audio),
            burn_captions=bool(self.burn_captions),
            caption_style=self.caption_style if self.caption_style in {"cinematic", "clean", "social"} else "cinematic",
            authorization_confirmed=bool(self.authorization_confirmed),
        )


def _parse_timecode(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.replace(",", ".")
    parts = raw.split(":")
    try:
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return None


def parse_script(script: str) -> list[dict[str, Any]]:
    """Parse screenplay-style SPEAKER: dialogue and optional [start --> end] cues."""
    lines: list[dict[str, Any]] = []
    pending_action: list[str] = []
    for raw in str(script or "").replace("\r\n", "\n").split("\n"):
        text = raw.strip()
        if not text:
            continue
        if SCENE_LINE.match(text):
            pending_action = [text]
            continue
        match = TIMECODE_LINE.match(text)
        if match:
            spoken = " ".join(match.group("text").split()).strip(' "“”')
            if not spoken:
                continue
            lines.append(
                {
                    "id": f"script_{len(lines)+1:04d}",
                    "order": len(lines) + 1,
                    "speaker": " ".join(match.group("speaker").split()),
                    "text": spoken,
                    "explicitStart": _parse_timecode(match.group("start")),
                    "explicitEnd": _parse_timecode(match.group("end")),
                    "context": " ".join(pending_action),
                }
            )
            pending_action = []
        else:
            pending_action.append(text)
    if not lines:
        compact = " ".join(str(script or "").split())
        if compact:
            lines.append(
                {
                    "id": "script_0001",
                    "order": 1,
                    "speaker": "Narrator",
                    "text": compact,
                    "explicitStart": None,
                    "explicitEnd": None,
                    "context": "",
                }
            )
    return lines


def estimate_speech_seconds(text: str, *, wpm: int = 145, speed: float = 1.0) -> float:
    words = max(1, len(WORD_RE.findall(str(text or ""))))
    punctuation = len(re.findall(r"[.!?]", text)) * 0.18 + len(re.findall(r"[,;:]", text)) * 0.08
    return max(0.45, words / max(1.0, wpm * speed) * 60.0 + punctuation)


def _verified_shots(video_result: dict[str, Any]) -> list[dict[str, Any]]:
    queue = video_result.get("queue") or {}
    return sorted(
        [
            item for item in queue.get("shots") or []
            if isinstance(item, dict) and item.get("status") == "verified" and item.get("path")
        ],
        key=lambda item: int(item.get("order", 0) or 0),
    )


def build_timing_plan(
    script: str,
    video_result: dict[str, Any],
    config: ScriptSyncConfig,
) -> dict[str, Any]:
    """Fit authored lines to verified shots without silently dropping text."""
    cfg = config.normalized()
    source_lines = parse_script(script)
    shots = _verified_shots(video_result)
    if not shots:
        raise VoiceStudioError("Script Sync requires at least one verified video clip")
    cursor = 0.0
    shot_windows: list[dict[str, Any]] = []
    for shot in shots:
        duration = float(shot.get("verifiedDurationSeconds") or shot.get("plannedDurationSeconds") or 8)
        shot_windows.append({"shot": shot, "start": cursor, "end": cursor + duration, "duration": duration})
        cursor += duration
    total_video = cursor
    planned: list[dict[str, Any]] = []
    auto_cursor = cfg.head_padding_seconds
    for item in source_lines:
        estimate = estimate_speech_seconds(item["text"], wpm=cfg.words_per_minute, speed=cfg.speed)
        start = item.get("explicitStart")
        end = item.get("explicitEnd")
        if start is None:
            start = auto_cursor
        if end is None:
            end = start + estimate
        if end <= start:
            end = start + estimate
        start = max(0.0, float(start))
        end = min(total_video, float(end))
        if end <= start:
            raise VoiceStudioError(
                f"Script line {item['order']} starts after the available verified video runtime"
            )
        shot_index = min(len(shot_windows) - 1, next(
            (index for index, window in enumerate(shot_windows) if start < window["end"]),
            len(shot_windows) - 1,
        ))
        window = shot_windows[shot_index]
        local_start = max(0.0, start - window["start"])
        available = max(0.25, window["duration"] - local_start - cfg.tail_padding_seconds)
        target = min(end - start, available)
        overflow = max(0.0, estimate - target)
        record = dict(item)
        record.update(
            {
                "shotId": str(window["shot"].get("id")),
                "shotOrder": int(window["shot"].get("order", shot_index + 1) or shot_index + 1),
                "globalStartSeconds": round(start, 3),
                "globalEndSeconds": round(start + target, 3),
                "localStartSeconds": round(local_start, 3),
                "targetDurationSeconds": round(target, 3),
                "estimatedSpeechSeconds": round(estimate, 3),
                "overflowSeconds": round(overflow, 3),
                "status": "planned",
            }
        )
        planned.append(record)
        auto_cursor = start + target + cfg.line_gap_seconds
    overflow_total = round(sum(float(item["overflowSeconds"]) for item in planned), 3)
    return {
        "schemaVersion": 1,
        "createdAt": utc_now(),
        "videoDurationSeconds": round(total_video, 3),
        "config": asdict(cfg),
        "lines": planned,
        "metrics": {
            "lineCount": len(planned),
            "wordCount": sum(len(WORD_RE.findall(item["text"])) for item in planned),
            "estimatedSpeechSeconds": round(sum(float(item["estimatedSpeechSeconds"]) for item in planned), 3),
            "overflowSeconds": overflow_total,
            "fitStatus": "fits" if overflow_total <= 0.15 else "needs_timing_repair",
        },
    }


def _voice_for(speaker: str, cfg: ScriptSyncConfig, index: int) -> str:
    mapping = {str(key).casefold(): str(value) for key, value in (cfg.voice_map or {}).items()}
    if speaker.casefold() in mapping:
        return mapping[speaker.casefold()]
    if speaker.casefold() == "narrator":
        return cfg.narrator_voice
    return cfg.lead_voice if index == 0 else cfg.supporting_voice


def _safe_script_root(run_root: Path) -> Path:
    root = (run_root / "script_sync").resolve()
    if run_root.resolve() not in root.parents:
        raise VoiceStudioError("Script Sync workspace escaped the run directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _word_timings(line: dict[str, Any], measured_duration: float) -> list[dict[str, Any]]:
    words = WORD_RE.findall(str(line.get("text") or ""))
    if not words:
        return []
    weights = [max(1.0, len(re.sub(r"\W", "", word)) ** 0.65) for word in words]
    total = sum(weights)
    cursor = float(line.get("globalStartSeconds", 0) or 0)
    result: list[dict[str, Any]] = []
    for word, weight in zip(words, weights):
        duration = measured_duration * weight / total
        result.append({"word": word, "start": round(cursor, 3), "end": round(cursor + duration, 3)})
        cursor += duration
    return result


def _srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(lines: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, line in enumerate(lines, start=1):
        speaker = str(line.get("speaker") or "")
        prefix = "" if speaker.casefold() == "narrator" else f"{speaker}: "
        blocks.append("\n".join([
            str(index),
            f"{_srt_time(float(line['globalStartSeconds']))} --> {_srt_time(float(line['globalEndSeconds']))}",
            f"{prefix}{line['text']}",
        ]))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_vtt(lines: list[dict[str, Any]]) -> str:
    return "WEBVTT\n\n" + render_srt(lines).replace(",", ".")


def render_ass(lines: list[dict[str, Any]], style: str = "cinematic") -> str:
    presets = {
        "cinematic": ("Arial", 46, "&H00FFFFFF", "&H00101010", 2),
        "clean": ("Arial", 40, "&H00FFFFFF", "&H00000000", 1),
        "social": ("Arial", 54, "&H0000FFFF", "&H00101010", 2),
    }
    font, size, primary, outline, outline_width = presets.get(style, presets["cinematic"])
    header = f"""[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,{font},{size},{primary},&H000000FF,{outline},&H78000000,-1,0,0,0,100,100,0,0,1,{outline_width},1,2,90,90,64,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"""
    def ass_time(value: float) -> str:
        centis = max(0, round(value * 100))
        hours, rem = divmod(centis, 360000)
        minutes, rem = divmod(rem, 6000)
        seconds, cs = divmod(rem, 100)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"
    events = []
    for line in lines:
        speaker = str(line.get("speaker") or "")
        prefix = "" if speaker.casefold() == "narrator" else f"{speaker}: "
        text = (prefix + str(line.get("text") or "")).replace("\n", r"\N").replace(",", r"\,")
        events.append(
            f"Dialogue: 0,{ass_time(float(line['globalStartSeconds']))},{ass_time(float(line['globalEndSeconds']))},Default,{speaker},0,0,0,,{text}"
        )
    return header + "\n".join(events) + ("\n" if events else "")


def _combine_line_audio(
    inputs: list[tuple[Path, float]], destination: Path, duration: float
) -> Path:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise VoiceStudioError("FFmpeg is required for professional script synchronization")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y"]
    filters: list[str] = []
    labels: list[str] = []
    for index, (path, delay) in enumerate(inputs):
        command.extend(["-i", str(path)])
        label = f"a{index}"
        filters.append(f"[{index}:a]aresample=48000,adelay={max(0, round(delay*1000))}:all=1[{label}]")
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,apad,atrim=0:{duration:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[out]"
    )
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "pcm_s16le", "-ar", "48000", str(destination)
    ])
    completed = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        raise VoiceStudioError(f"Script audio mix failed: {completed.stderr[-1600:]}")
    verify_audio(destination)
    return destination


def _burn_ass(video: Path, ass: Path, destination: Path) -> Path:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise VoiceStudioError("FFmpeg is required to burn captions")
    escaped = str(ass).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    completed = subprocess.run(
        [ffmpeg, "-y", "-i", str(video), "-vf", f"ass='{escaped}'", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", "-movflags", "+faststart", str(destination)],
        capture_output=True, text=True, timeout=1800,
    )
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        raise VoiceStudioError(f"Caption render failed: {completed.stderr[-1600:]}")
    verify_mp4(destination)
    return destination


def render_script_production(
    run_root: str | Path,
    video_result: dict[str, Any],
    script: str,
    config: ScriptSyncConfig,
    *,
    provider_factory: Callable[[dict[str, Any]], Any] = make_voice_provider,
) -> dict[str, Any]:
    """Render an authored script against the existing verified shot timeline."""
    run_root = Path(run_root).resolve()
    root = _safe_script_root(run_root)
    cfg = config.normalized()
    plan = build_timing_plan(script, video_result, cfg)
    atomic_write_text(root / "authored_script.txt", script)
    atomic_write_json(root / "script_timing_plan.json", plan)
    provider_config = {
        "provider": cfg.provider,
        "model": cfg.model,
        "authorization_confirmed": cfg.authorization_confirmed,
    }
    provider = provider_factory(provider_config)
    shots = {str(shot.get("id")): shot for shot in _verified_shots(video_result)}
    line_dir = root / "lines"
    mixed_dir = root / "mixed_shots"
    line_dir.mkdir(parents=True, exist_ok=True)
    mixed_dir.mkdir(parents=True, exist_ok=True)
    signature_base = hashlib.sha256((script + json.dumps(asdict(cfg), sort_keys=True)).encode()).hexdigest()
    for index, line in enumerate(plan["lines"]):
        line["voice"] = _voice_for(str(line["speaker"]), cfg, index)
        audio_path = line_dir / f"{line['id']}.mp3"
        signature = hashlib.sha256(f"{signature_base}|{line['id']}|{line['voice']}|{line['text']}".encode()).hexdigest()
        line["signature"] = signature
        provider.synthesize(
            text=str(line["text"]), voice=str(line["voice"]), destination=audio_path,
            instructions=cfg.instructions, speed=cfg.speed, seed=index + 1,
        )
        metadata = verify_audio(audio_path)
        measured = float(metadata.get("durationSeconds") or line["estimatedSpeechSeconds"])
        available = float(line["targetDurationSeconds"])
        if measured > available + 0.12:
            raise VoiceStudioError(
                f"Line {line['order']} is {measured:.2f}s but only {available:.2f}s is available. Shorten the line or reduce voice speed."
            )
        line["audioPath"] = _relative(run_root, audio_path)
        line["audioDurationSeconds"] = round(measured, 3)
        line["globalEndSeconds"] = round(float(line["globalStartSeconds"]) + measured, 3)
        line["wordTimings"] = _word_timings(line, measured)
        line["status"] = "verified"
        line["completedAt"] = utc_now()
        atomic_write_json(root / "script_timing_plan.json", plan)
    dubbed_paths: list[Path] = []
    for shot_id, shot in sorted(shots.items(), key=lambda item: int(item[1].get("order", 0) or 0)):
        shot_lines = [line for line in plan["lines"] if line.get("shotId") == shot_id]
        source = _safe_path(run_root, str(shot.get("path")))
        if not shot_lines:
            dubbed_paths.append(source)
            continue
        duration = float(shot.get("verifiedDurationSeconds") or shot.get("plannedDurationSeconds") or 8)
        combined = root / "shot_audio" / f"{shot_id}.wav"
        _combine_line_audio(
            [(_safe_path(run_root, line["audioPath"]), float(line["localStartSeconds"])) for line in shot_lines],
            combined,
            duration,
        )
        dubbed = mixed_dir / f"{shot_id}.mp4"
        mix_voice_into_clip(
            source, combined, dubbed, duration=duration,
            preserve_source_audio=cfg.preserve_source_audio, delay_seconds=0.0,
        )
        dubbed_paths.append(dubbed)
    final = root / "professional_script_synced_film.mp4"
    assemble_clips(dubbed_paths, final)
    srt = root / "professional_subtitles.srt"
    vtt = root / "professional_subtitles.vtt"
    ass = root / "professional_subtitles.ass"
    atomic_write_text(srt, render_srt(plan["lines"]))
    atomic_write_text(vtt, render_vtt(plan["lines"]))
    atomic_write_text(ass, render_ass(plan["lines"], cfg.caption_style))
    word_json = root / "word_alignment.json"
    atomic_write_json(word_json, {"lines": [{"id": line["id"], "speaker": line["speaker"], "words": line["wordTimings"]} for line in plan["lines"]]})
    captioned: Path | None = None
    if cfg.burn_captions:
        captioned = root / "professional_script_synced_captioned.mp4"
        _burn_ass(final, ass, captioned)
    plan["status"] = "complete"
    plan["completedAt"] = utc_now()
    plan["artifacts"] = {
        "finalFilm": _relative(run_root, final),
        "captionedFilm": _relative(run_root, captioned) if captioned else None,
        "srt": _relative(run_root, srt),
        "vtt": _relative(run_root, vtt),
        "ass": _relative(run_root, ass),
        "wordAlignment": _relative(run_root, word_json),
    }
    plan["metrics"].update({
        "generatedLines": len(plan["lines"]),
        "verifiedLines": len(plan["lines"]),
        "syncCoverage": 1.0,
        "captioned": bool(captioned),
    })
    atomic_write_json(root / "script_timing_plan.json", plan)
    return {
        "status": "complete",
        "plan": plan,
        "final_video_path": str(final),
        "captioned_video_path": str(captioned) if captioned else None,
        "srt_path": str(srt),
        "vtt_path": str(vtt),
        "ass_path": str(ass),
        "word_alignment_path": str(word_json),
        "plan_path": str(root / "script_timing_plan.json"),
    }
