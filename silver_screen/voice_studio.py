"""Authorized voice orchestration and voiced-film assembly."""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from typing import Any
from .ai_video import assemble_clips
from .runtime import (
    RunWorkspace,
    atomic_write_json,
    atomic_write_text,
    load_run,
    utc_now,
)
from .voice_providers import diagnose_voice_error, make_voice_provider
from .voice_config import (
    AUDIO_SUFFIXES,
    ProviderFactory,
    VoiceStudioError,
    _load_json,
    _paths,
    _record_event,
    _relative,
    _safe_path,
    _save,
    _serializable_config,
    build_voice_cast,
    build_voice_plan,
    normalize_voice_config,
    prepare_voice_config,
    validate_voice_config,
    voice_capabilities,
)
from .voice_audio import (
    _manual_track,
    _reconcile,
    _reinforce,
    _repair_line,
    _video_path,
    _video_shots,
    mix_voice_into_clip,
    render_subtitles,
    verify_audio,
)


def _resolve_custom_voice(
    root: Path, config: dict[str, Any], provider: Any
) -> str | None:
    if not config.get("custom_voice") or config.get("custom_voice_id"):
        return str(config.get("custom_voice_id") or "") or None
    consent_path = _safe_path(root, config["consent_recording_path"])
    sample_path = _safe_path(root, config["voice_sample_path"])
    consent_id = str(config.get("consent_id") or "")
    if not consent_id:
        consent_id = provider.create_consent(
            recording=consent_path,
            name=f"{config.get('custom_voice_name')} consent",
            language=str(config.get("language") or "en-US"),
        )
        config["consent_id"] = consent_id
        atomic_write_json(_paths(root)["config"], _serializable_config(config))
    voice_id = provider.create_custom_voice(
        audio_sample=sample_path,
        consent_id=consent_id,
        name=str(config.get("custom_voice_name") or "Silver Screen Voice"),
    )
    config["custom_voice_id"] = voice_id
    config["lead_voice"] = voice_id
    atomic_write_json(_paths(root)["config"], _serializable_config(config))
    return voice_id


def _planned_result(
    root: Path,
    plan: dict[str, Any],
    config: dict[str, Any],
    *,
    silent_video: str | None,
) -> dict[str, Any]:
    paths = _paths(root)
    artifacts = plan.get("artifacts") or {}
    final_value = artifacts.get("finalVoicedFilm") or artifacts.get(
        "partialVoicedFilm"
    )
    final_path = str(_safe_path(root, final_value)) if final_value else None
    dubbed_paths = [
        str(_safe_path(root, line["dubbedPath"]))
        for line in plan.get("lines") or []
        if isinstance(line, dict)
        and line.get("status") == "verified"
        and line.get("dubbedPath")
    ]
    line_paths = [
        str(_safe_path(root, line["audioPath"]))
        for line in plan.get("lines") or []
        if isinstance(line, dict)
        and line.get("status") == "verified"
        and line.get("audioPath")
    ]
    blocked_error = next(
        (
            str(line.get("lastError"))
            for line in plan.get("lines") or []
            if isinstance(line, dict)
            and line.get("status") == "blocked"
            and line.get("lastError")
        ),
        None,
    )
    return {
        "enabled": bool(config.get("enabled")),
        "status": plan.get("status"),
        "provider": config.get("provider"),
        "model": config.get("model"),
        "mode": config.get("mode"),
        "metrics": plan.get("metrics") or {},
        "msil": plan.get("msil") or {},
        "cast": plan.get("cast") or {},
        "plan": plan,
        "line_audio_paths": line_paths,
        "dubbed_clip_paths": dubbed_paths,
        "final_video_path": final_path if plan.get("status") == "complete" else None,
        "partial_video_path": (
            final_path if plan.get("status") != "complete" else None
        ),
        "silent_video_path": silent_video,
        "subtitles_path": (
            str(paths["subtitles"]) if paths["subtitles"].exists() else None
        ),
        "config_path": str(paths["config"]),
        "cast_path": str(paths["cast"]),
        "plan_path": str(paths["plan"]),
        "runtime_path": str(paths["runtime"]),
        "scar_memory_path": str(paths["scars"]),
        "warnings": [blocked_error] if blocked_error else [],
        "error": blocked_error,
        "capabilities": voice_capabilities(),
    }


def process_voice_production(
    state: dict[str, Any],
    video_result: dict[str, Any],
    out_dir: str | os.PathLike[str],
    *,
    voice_inputs: list[Any] | None = None,
    provider_factory: ProviderFactory = make_voice_provider,
) -> dict[str, Any]:
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    config = prepare_voice_config(root, voice_inputs)
    silent_video = (
        video_result.get("final_video_path")
        or video_result.get("partial_video_path")
        or video_result.get("hero_path")
    )
    if not config.get("enabled"):
        return {
            "enabled": False,
            "status": "disabled",
            "metrics": {},
            "msil": {"verdict": "disabled"},
            "silent_video_path": silent_video,
            "warnings": [],
            "error": None,
            "capabilities": voice_capabilities(),
        }
    validate_voice_config(config)
    provider = provider_factory(config)
    custom_voice_id = (
        _resolve_custom_voice(root, config, provider) if provider is not None else None
    )
    if custom_voice_id:
        config["lead_voice"] = custom_voice_id
    cast = build_voice_cast(state, config)
    existing = _load_json(_paths(root)["plan"])
    plan = build_voice_plan(state, video_result, config, cast, existing)
    _record_event(
        plan,
        "voice_production_opened",
        detail=f"{config.get('provider')} / {config.get('mode')}",
    )
    video_shots = _video_shots(video_result)
    verified_video = {
        key: value for key, value in video_shots.items() if value.get("status") == "verified"
    }
    _reconcile(root, plan, verified_video)
    _save(root, plan, cast)
    paths = _paths(root)
    eligible = [
        line
        for line in sorted(
            [item for item in plan.get("lines") or [] if isinstance(item, dict)],
            key=lambda item: int(item.get("order", 0) or 0),
        )
        if line.get("videoStatus") == "verified" and line.get("text")
    ]
    for eligible_index, line in enumerate(eligible):
        if line.get("status") == "verified":
            continue
        allowed_attempts = int(config.get("max_retries_per_line", 1) or 0) + 1
        if int(line.get("attempts", 0) or 0) >= allowed_attempts:
            line["status"] = "blocked"
            line["lastError"] = line.get("lastError") or "Voice retry budget exhausted"
            continue
        while line.get("status") != "verified" and int(
            line.get("attempts", 0) or 0
        ) < allowed_attempts:
            line["attempts"] = int(line.get("attempts", 0) or 0) + 1
            line["status"] = "generating"
            line["lastError"] = None
            _save(root, plan, cast)
            try:
                if config.get("provider") == "manual":
                    source_audio = _manual_track(
                        root, config, line, eligible_index
                    )
                    suffix = (
                        source_audio.suffix.lower()
                        if source_audio.suffix.lower() in AUDIO_SUFFIXES
                        else ".wav"
                    )
                    audio_path = paths["lines"] / f"{line.get('id')}{suffix}"
                    audio_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_audio, audio_path)
                else:
                    audio_path = paths["lines"] / f"{line.get('id')}.mp3"
                    instructions = str(
                        line.get("instructionsOverride")
                        or config.get("instructions")
                        or ""
                    )
                    provider.synthesize(
                        text=str(line.get("text") or ""),
                        voice=str(
                            line.get("voice") or config.get("narrator_voice") or ""
                        ),
                        destination=audio_path,
                        instructions=instructions,
                        speed=float(config.get("speed", 1.0) or 1.0),
                        seed=int(
                            (state.get("seed") or 0)
                            + int(line.get("order", 0) or 0)
                            + int(line.get("attempts", 0) or 0)
                        ),
                    )
                audio_metadata = verify_audio(audio_path)
                shot = verified_video.get(str(line.get("shotId") or ""))
                if not shot:
                    raise VoiceStudioError("The matching video shot is not verified")
                video_path = _video_path(root, shot)
                if video_path is None:
                    raise VoiceStudioError("Verified video shot has no path")
                dubbed_path = paths["dubbed"] / f"{line.get('shotId')}.mp4"
                mix_voice_into_clip(
                    video_path,
                    audio_path,
                    dubbed_path,
                    duration=float(line.get("targetDurationSeconds", 0) or 0),
                    preserve_source_audio=bool(config.get("preserve_source_audio")),
                    delay_seconds=float(
                        config.get("line_delay_seconds", 0.2) or 0.0
                    ),
                )
                line.update(
                    {
                        "status": "verified",
                        "audioPath": _relative(root, audio_path),
                        "dubbedPath": _relative(root, dubbed_path),
                        "audioDurationSeconds": audio_metadata.get("durationSeconds"),
                        "lastError": None,
                        "completedAt": utc_now(),
                    }
                )
                if line.get("repairs"):
                    _reinforce(plan, line, line["repairs"][-1])
                _record_event(
                    plan,
                    "voice_line_verified",
                    line_id=str(line.get("id")),
                    detail=f"{line.get('speaker')} / {line.get('voice')}",
                )
            except Exception as exc:
                error = str(exc)
                diagnosis = diagnose_voice_error(error)
                line["lastError"] = error
                repair = _repair_line(
                    line,
                    diagnosis.code,
                    int(line.get("attempts", 0) or 0) + 1,
                )
                line.setdefault("repairs", []).append(repair)
                _record_event(
                    plan,
                    "voice_tgrm_repair",
                    line_id=str(line.get("id")),
                    detail=error,
                    data={"diagnosis": diagnosis.code, **repair},
                )
                if not diagnosis.retryable or int(
                    line.get("attempts", 0) or 0
                ) >= allowed_attempts:
                    line["status"] = "blocked"
                    _save(root, plan, cast)
                    break
                line["status"] = "pending"
            _save(root, plan, cast)
    verified_lines = [
        line for line in eligible if line.get("status") == "verified"
    ]
    if config.get("subtitles"):
        subtitle_text = render_subtitles(plan)
        if subtitle_text:
            atomic_write_text(paths["subtitles"], subtitle_text)
            plan.setdefault("artifacts", {})["subtitles"] = _relative(
                root, paths["subtitles"]
            )
    if verified_lines:
        dubbed = [
            _safe_path(root, line["dubbedPath"]) for line in verified_lines
        ]
        video_metrics = video_result.get("metrics") or {}
        planned_video = int(video_metrics.get("plannedShots", 0) or 0)
        complete = bool(
            video_result.get("status") == "complete"
            and len(verified_lines) >= planned_video
            and not any(line.get("status") == "blocked" for line in eligible)
        )
        destination = paths["root"] / (
            "final_film_with_voices.mp4"
            if complete
            else "partial_film_with_voices.mp4"
        )
        assemble_clips(dubbed, destination)
        plan.setdefault("artifacts", {})[
            "finalVoicedFilm" if complete else "partialVoicedFilm"
        ] = _relative(root, destination)
    blocked = [line for line in eligible if line.get("status") == "blocked"]
    complete_video = video_result.get("status") == "complete"
    if blocked:
        plan["status"] = "blocked"
    elif complete_video and len(verified_lines) == len(eligible):
        plan["status"] = "complete"
        plan["completedAt"] = utc_now()
    elif verified_lines:
        plan["status"] = "partial"
    else:
        plan["status"] = "waiting_for_video" if not eligible else "planned"
    _save(root, plan, cast)
    return _planned_result(root, plan, config, silent_video=silent_video)


def merge_voice_result(
    video_result: dict[str, Any], voice: dict[str, Any]
) -> dict[str, Any]:
    result = dict(video_result)
    result["voice"] = voice
    voiced = voice.get("final_video_path") or voice.get("partial_video_path")
    if voiced:
        result["silent_video_path"] = (
            video_result.get("final_video_path")
            or video_result.get("partial_video_path")
            or video_result.get("hero_path")
        )
        result["hero_path"] = voiced
        if voice.get("status") == "complete":
            result["final_video_path"] = voiced
            result["partial_video_path"] = None
        else:
            result["partial_video_path"] = voiced
    result.setdefault("warnings", []).extend(
        [warning for warning in voice.get("warnings") or [] if warning]
    )
    if voice.get("status") == "blocked" and not result.get("error"):
        result["voice_error"] = voice.get("error")
    return result


def attach_voice_to_run(
    run_id: str,
    voice_inputs: list[Any],
    *,
    output_root: str | None = "runs",
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    state = result.get("state") or {}
    video_result = result.get("media") or {}
    if not state or str(video_result.get("mode") or "") != "ai-video":
        raise VoiceStudioError(
            "The saved run does not contain an AI-video production"
        )
    workspace.update(
        status="running",
        stage="voice_production",
        progress=max(72, int(workspace.manifest.get("progress", 72) or 72)),
        error=None,
    )
    voice = process_voice_production(
        state, video_result, workspace.media_dir, voice_inputs=voice_inputs
    )
    merged = merge_voice_result(video_result, voice)
    result["media"] = merged
    result["voice"] = voice
    result.setdefault("artifacts", {})["voiceConfig"] = voice.get("config_path")
    result["artifacts"]["voiceCast"] = voice.get("cast_path")
    result["artifacts"]["voicePlan"] = voice.get("plan_path")
    result["artifacts"]["voiceRuntime"] = voice.get("runtime_path")
    result["artifacts"]["voiceScarMemory"] = voice.get("scar_memory_path")
    result["artifacts"]["subtitles"] = voice.get("subtitles_path")
    result["artifacts"]["voicedFilm"] = voice.get(
        "final_video_path"
    ) or voice.get("partial_video_path")
    for name in (
        "voiceConfig",
        "voiceCast",
        "voicePlan",
        "voiceRuntime",
        "voiceScarMemory",
        "subtitles",
        "voicedFilm",
    ):
        workspace.register_optional_artifact(
            name, result["artifacts"].get(name)
        )
    result["warnings"] = list(
        dict.fromkeys(
            [
                *(result.get("warnings") or []),
                *(voice.get("warnings") or []),
            ]
        )
    )
    workspace.persist_result(result)
    workspace.write_json("result.json", result)
    completion = float(
        (voice.get("metrics") or {}).get("completionRatio", 0) or 0
    )
    if voice.get("status") == "complete" and video_result.get("status") == "complete":
        result["status"] = "complete"
        workspace.complete(
            {
                "title": state.get("title"),
                "voiceMetrics": voice.get("metrics") or {},
                "voiceMsil": voice.get("msil") or {},
            }
        )
    elif voice.get("status") == "blocked":
        result["status"] = "blocked"
        workspace.checkpoint(
            status="blocked",
            stage="voice_blocked",
            progress=min(99, 72 + round(completion * 26)),
            extra={
                "title": state.get("title"),
                "voiceMetrics": voice.get("metrics") or {},
                "voiceError": voice.get("error"),
            },
        )
    else:
        result["status"] = "partial"
        workspace.checkpoint(
            status="partial",
            stage="voice_checkpoint",
            progress=min(99, 72 + round(completion * 26)),
            extra={
                "title": state.get("title"),
                "voiceMetrics": voice.get("metrics") or {},
            },
        )
    bundle = workspace.build_bundle(str(state.get("title") or run_id))
    result.setdefault("artifacts", {})["bundle"] = str(bundle)
    workspace.write_json("result.json", result)
    return result
