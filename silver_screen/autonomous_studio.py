"""One-click autonomous film production with persistent project memory.

The system coordinates free preproduction, prompt-ledger approval, durable video
generation, local visual QA, optional semantic QA, adaptive transition finishing,
model-routing recommendations, project memory, an edit-decision list, and optional
voice/caption finishing. "Blockbuster target" is an orchestration target, not a
guarantee that current foundation models will equal a human studio production.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .cinematic_continuity import rebuild_run_transitions
from .model_router import normalize_routing_config, route_queue
from .preproduction import build_preproduction_preview
from .production_memory import (
    load_project_memory, memory_context, merge_project_memory, project_id_for,
    record_memory_event, record_model_outcome, record_shot_outcome,
    save_project_memory, snapshot_memory_to_run,
)
from .runtime import RunWorkspace, load_run, utc_now
from .semantic_supervisor import inspect_run_semantics, semantic_settings
from .video_runtime import load_video_queue
from .visual_quality import inspect_run as inspect_visual_run

ProgressCallback = Callable[[str, int, str], None]


class AutonomousStudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class QualityProfile:
    key: str
    label: str
    semantic_target: float
    visual_target: float
    retries_per_shot: int
    description: str


QUALITY_PROFILES = {
    "screen_test": QualityProfile("screen_test", "Screen test", .72, .68, 1, "Fast identity, style, and pipeline verification."),
    "cinematic": QualityProfile("cinematic", "Cinematic", .80, .72, 2, "Balanced quality, cost, continuity, and repair depth."),
    "blockbuster_target": QualityProfile("blockbuster_target", "Blockbuster target", .87, .77, 3, "Maximum available planning, memory, QA, continuity, and repair. Output remains model-dependent."),
}


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def quality_profile(value: str | None) -> QualityProfile:
    return QUALITY_PROFILES.get(str(value or "blockbuster_target").casefold(), QUALITY_PROFILES["blockbuster_target"])


def recommended_creative_profile(genre: str, medium: str = "") -> str:
    token, visual = str(genre or "drama").casefold(), str(medium or "").casefold()
    if "animat" in visual:
        return "premium_animation"
    if token in {"thriller", "noir"}:
        return "modern_spy_thriller"
    if token == "horror":
        return "dark_psychological"
    if token in {"scifi", "fantasy", "western"}:
        return "stylized_genre"
    return "grounded_prestige"


def normalize_autonomous_config(value: Any = None) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    profile = quality_profile(raw.get("qualityProfile"))
    semantic_authorized = _bool(raw.get("semanticAuthorized"), False)
    return {
        "schemaVersion": 1,
        "enabled": _bool(raw.get("enabled"), True),
        "qualityProfile": profile.key,
        "qualityLabel": profile.label,
        "projectId": _clean(raw.get("projectId"), 100),
        "continuous": _bool(raw.get("continuous"), True),
        "semanticQa": _bool(raw.get("semanticQa"), True),
        "semanticAuthorized": semantic_authorized,
        "finishAudio": _bool(raw.get("finishAudio"), False),
        "burnCaptions": _bool(raw.get("burnCaptions"), True),
        "voiceProvider": str(raw.get("voiceProvider") or "openai").casefold(),
        "leadVoice": str(raw.get("leadVoice") or "coral"),
        "supportingVoice": str(raw.get("supportingVoice") or "onyx"),
        "narratorVoice": str(raw.get("narratorVoice") or "cedar"),
        "voiceInstructions": _clean(raw.get("voiceInstructions") or "Natural feature-film performance, restrained emotion, precise intention, clean diction, no announcer exaggeration.", 1200),
        "speechSpeed": _float(raw.get("speechSpeed"), 1.0, .7, 1.3),
        "wordsPerMinute": _int(raw.get("wordsPerMinute"), 145, 80, 240),
        "maxProviderCalls": _int(raw.get("maxProviderCalls"), 0, 0, 5000),
        "maxSpendUsd": _float(raw.get("maxSpendUsd"), 0, 0, 1_000_000),
        "costPerSecondUsd": _float(raw.get("costPerSecondUsd"), 0, 0, 1000),
        "batchSize": _int(raw.get("batchSize"), 0, 0, 256),
        "retriesPerShot": _int(raw.get("retriesPerShot"), profile.retries_per_shot, 0, 8),
        "transitionMode": str(raw.get("transitionMode") or "auto").casefold(),
        "modelRouting": normalize_routing_config({
            **dict(raw.get("modelRouting") or {}),
            "qualityTier": profile.key,
            "qualityBias": .90 if profile.key == "blockbuster_target" else .78,
            "costBias": .05 if profile.key == "blockbuster_target" else .14,
            "speedBias": .05 if profile.key == "blockbuster_target" else .08,
        }),
        "semantic": semantic_settings({
            **dict(raw.get("semantic") or {}),
            "authorized": semantic_authorized,
            "qualityTarget": profile.semantic_target,
            "automaticReject": False,
        }),
        "profile": asdict(profile),
    }


def _direction(brief: dict[str, Any], cfg: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    supplied = dict(brief.get("creativeDirection") or {})
    profile = quality_profile(cfg["qualityProfile"])
    note = " ".join(part for part in (
        _clean(supplied.get("directorNotes"), 1200),
        memory_context(memory, max_chars=1800),
        "BLOCKBUSTER-TARGET DISCIPLINE: preserve coherent physical action, readable geography, stable identity and wardrobe, motivated lighting, specific acting, editorial handles, and production-design continuity. Never replace an approved beat with empty spectacle." if profile.key == "blockbuster_target" else "",
    ) if part)[:3600]
    return {
        **supplied,
        "profile": supplied.get("profile") or recommended_creative_profile(str(brief.get("genre") or ""), str(supplied.get("medium") or "")),
        "scriptSource": "authored" if brief.get("authoredScript") else supplied.get("scriptSource", "generated"),
        "strictGate": True,
        "enforceApprovalGates": True,
        "approvals": {"scriptApproved": True, "promptsApproved": True, "budgetApproved": True},
        "directorNotes": note,
    }


def _shot_direction(brief: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    supplied = dict(brief.get("shotDirection") or {})
    return {
        **supplied,
        "audioStrategy": supplied.get("audioStrategy") or "dub_later",
        "coverageGate": True,
        "maximumPromptSimilarity": .86 if cfg["qualityProfile"] == "blockbuster_target" else .90,
        "minimumDistinctShotRatio": .90 if cfg["qualityProfile"] == "blockbuster_target" else .84,
    }


def prepare_autonomous_plan(brief: dict[str, Any], *, target_runtime_seconds: int, clip_duration_seconds: int = 8, max_shots: int | None = None, config: dict[str, Any] | None = None, output_root: str = "runs") -> dict[str, Any]:
    """Create a complete provider-free, one-click production contract."""
    if not isinstance(brief, dict) or len(str(brief.get("premise") or "").strip()) < 12:
        raise AutonomousStudioError("Enter a premise with at least 12 characters.")
    cfg = normalize_autonomous_config(config)
    project_id = project_id_for(brief, explicit=cfg.get("projectId") or None)
    cfg["projectId"] = project_id
    memory = load_project_memory(project_id, output_root)
    working = copy.deepcopy(brief)
    working["creativeDirection"] = _direction(working, cfg, memory)
    working["shotDirection"] = _shot_direction(working, cfg)
    planned = max(1, min(int(max_shots or 1000), int(math.ceil(max(1, target_runtime_seconds) / max(1, clip_duration_seconds)))))

    first = build_preproduction_preview(working, target_runtime_seconds=int(target_runtime_seconds), clip_duration_seconds=int(clip_duration_seconds), max_shots=planned, max_prompt_previews=min(60, planned))
    memory = merge_project_memory(memory, brief=first.get("brief") or working, state=first.get("state") or {}, preferences={"qualityProfile": cfg["qualityProfile"], "transitionMode": cfg["transitionMode"], "modelRouting": cfg["modelRouting"]})
    working["creativeDirection"] = _direction(working, cfg, memory)
    preview = build_preproduction_preview(working, target_runtime_seconds=int(target_runtime_seconds), clip_duration_seconds=int(clip_duration_seconds), max_shots=planned, max_prompt_previews=min(60, planned))
    memory = merge_project_memory(memory, brief=preview.get("brief") or working, state=preview.get("state") or {}, preferences={"qualityProfile": cfg["qualityProfile"], "creativeProfile": (preview.get("creativeDirection") or {}).get("profile")})
    save_project_memory(memory, output_root)

    approved = copy.deepcopy(preview.get("brief") or working)
    direction = copy.deepcopy(preview.get("creativeDirection") or {})
    direction.update({"enforceApprovalGates": True, "strictGate": True, "approvals": {"scriptApproved": True, "promptsApproved": True, "budgetApproved": True}})
    shot_direction = copy.deepcopy(preview.get("shotDirection") or {})
    ledger = copy.deepcopy(preview.get("promptLedger") or {})
    shot_direction.update({"enforcePromptLedger": True, "approvedPromptLedger": ledger, "approvedLedgerHash": str(ledger.get("ledgerHash") or "")})
    approved["creativeDirection"], approved["shotDirection"] = direction, shot_direction
    if brief.get("authoredScript"):
        approved["authoredScript"] = str(brief["authoredScript"])

    routes = route_queue(preview.get("queuePreview") or {}, state=preview.get("state") or {}, config=cfg["modelRouting"], memory=memory)
    render = preview.get("renderPlan") or {}
    retries = int(cfg["retriesPerShot"])
    shots = int(render.get("plannedShots", planned) or planned)
    recommended_calls = shots * (retries + 1)
    calls = int(cfg["maxProviderCalls"]) if int(cfg["maxProviderCalls"]) > 0 else recommended_calls
    batch = int(cfg["batchSize"]) if int(cfg["batchSize"]) > 0 else shots
    estimated = calls * int(render.get("clipDurationSeconds", clip_duration_seconds) or clip_duration_seconds) * float(cfg["costPerSecondUsd"])
    if cfg["maxSpendUsd"] > 0 and cfg["costPerSecondUsd"] <= 0:
        raise AutonomousStudioError("Enter current cost per generated second when using a dollar budget.")
    if cfg["maxSpendUsd"] > 0 and estimated > cfg["maxSpendUsd"]:
        raise AutonomousStudioError("The requested call ceiling exceeds the explicit dollar budget.")

    preview.update({
        "schemaVersion": 3,
        "autonomousConfig": cfg,
        "projectId": project_id,
        "approvedBrief": approved,
        "modelRoutes": routes,
        "productionMemory": memory,
        "costForecast": {"plannedShots": shots, "authorizedCalls": calls, "costPerGeneratedSecondUsd": cfg["costPerSecondUsd"], "estimatedMaximumVideoCostUsd": round(estimated, 4), "pricingConfigured": cfg["costPerSecondUsd"] > 0},
        "executionPlan": {"targetRuntimeSeconds": int(target_runtime_seconds), "clipDurationSeconds": int(clip_duration_seconds), "plannedShots": shots, "batchSize": max(1, batch), "retriesPerShot": retries, "maxProviderCalls": max(1, calls), "continuous": bool(cfg["continuous"]), "transitionMode": cfg["transitionMode"]},
        "providerCallsMade": 0,
    })
    record_memory_event(memory, "autonomous_plan_approved", detail=f"Approved {shots} planned shots", data={"qualityProfile": cfg["qualityProfile"], "ledgerHash": ledger.get("ledgerHash")})
    save_project_memory(memory, output_root)
    return preview


def _run_id(result: dict[str, Any]) -> str:
    return str((result.get("run") or {}).get("id") or (result.get("run") or {}).get("runId") or result.get("runId") or "")


def build_edit_decision_list(run_id: str, *, output_root: str = "runs") -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    queue = load_video_queue(workspace.media_dir) or {}
    transition_map = {str(item.get("toShot") or ""): item for item in ((queue.get("transitionPlan") or {}).get("transitions") or []) if isinstance(item, dict)}
    cursor, clips = 0.0, []
    shots = sorted((item for item in queue.get("shots") or [] if isinstance(item, dict) and item.get("status") == "verified"), key=lambda item: int(item.get("order", 0) or 0))
    for shot in shots:
        duration = float(shot.get("verifiedDurationSeconds") or shot.get("plannedDurationSeconds") or 0)
        transition = transition_map.get(str(shot.get("id") or ""), {})
        overlap = float(transition.get("durationSeconds", 0) or 0)
        start, end = max(0.0, cursor - overlap), max(0.0, cursor - overlap) + duration
        value = shot.get("path")
        path = Path(str(value)) if value else None
        if path and not path.is_absolute():
            path = (workspace.media_dir / path).resolve()
        clips.append({
            "shotId": shot.get("id"), "order": shot.get("order"), "scene": (shot.get("sourceScene") or {}).get("number"), "chapter": (shot.get("sourceScene") or {}).get("chapter"),
            "sourcePath": str(path or ""), "timelineStartSeconds": round(start, 3), "timelineEndSeconds": round(end, 3), "sourceInSeconds": 0.0, "sourceOutSeconds": round(duration, 3),
            "transitionIn": {"style": transition.get("style"), "durationSeconds": round(overlap, 3)} if transition else None,
            "locked": bool(shot.get("operatorLocked")), "quality": shot.get("semanticQuality") or shot.get("visualQuality") or {},
        })
        cursor = end
    edl = {"schemaVersion": 1, "runId": run_id, "createdAt": utc_now(), "title": (result.get("state") or {}).get("title"), "durationSeconds": round(cursor, 3), "tracks": {"video": clips, "dialogue": [], "narration": [], "music": [], "effects": [], "captions": []}, "note": "Machine-readable non-linear edit contract. Approved source clips are never deleted by timeline changes."}
    path = workspace.write_json("edit/edit_decision_list.json", edl)
    workspace.register_artifact("editDecisionList", path)
    return {"edl": edl, "path": str(path)}


def _quality(result: dict[str, Any], visual: dict[str, Any] | None, semantic: dict[str, Any] | None) -> dict[str, Any]:
    metrics = (result.get("media") or {}).get("metrics") or {}
    visual_report = (visual or {}).get("report") or {}
    semantic_report = (semantic or {}).get("report") or {}
    completion = float(metrics.get("completionRatio", 0) or 0)
    visual_score = float(visual_report.get("averageScore", 0) or 0)
    semantic_score = float(semantic_report.get("averageScore", 0) or 0)
    semantic_available = int(semantic_report.get("semanticReviewed", 0) or 0) > 0
    score = .42 * visual_score + .38 * semantic_score + .20 * completion if semantic_available else .72 * visual_score + .28 * completion
    return {
        "schemaVersion": 1, "createdAt": utc_now(), "projectQualityScore": round(score, 6), "scorePercent": round(score * 100, 1),
        "completionRatio": round(completion, 6), "visualAverage": round(visual_score, 6), "semanticAverage": round(semantic_score, 6) if semantic_available else None,
        "semanticAvailable": semantic_available, "verifiedShots": int(metrics.get("verifiedShots", 0) or 0), "plannedShots": int(metrics.get("plannedShots", 0) or 0),
        "rating": "master_candidate" if completion >= 1 and score >= .86 else "strong_cut" if score >= .78 else "review",
        "truthfulLimitation": "This internal score measures contract compliance and technical evidence. It is not a guarantee of Hollywood-studio quality.",
    }


def _finish_audio(workspace: RunWorkspace, result: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    if not cfg.get("finishAudio") or str(result.get("status") or "") != "complete":
        return None
    from .script_sync import ScriptSyncConfig, render_script_production
    from .voice_providers import provider_capabilities
    provider = str(cfg.get("voiceProvider") or "openai")
    if not provider_capabilities().get(provider):
        raise AutonomousStudioError(f"Automatic audio finishing is unavailable for {provider}.")
    script = str((result.get("state") or {}).get("script") or "").strip()
    if not script:
        return None
    sync = ScriptSyncConfig(provider=provider, lead_voice=cfg["leadVoice"], supporting_voice=cfg["supportingVoice"], narrator_voice=cfg["narratorVoice"], instructions=cfg["voiceInstructions"], speed=float(cfg["speechSpeed"]), words_per_minute=int(cfg["wordsPerMinute"]), preserve_source_audio=True, burn_captions=bool(cfg["burnCaptions"]), caption_style="cinematic", authorization_confirmed=True)
    rendered = render_script_production(workspace.path, result.get("media") or {}, script, sync)
    for key, name in (("final_video_path", "autonomousVoicedFilm"), ("captioned_video_path", "autonomousCaptionedFilm"), ("srt_path", "autonomousSrt"), ("word_alignment_path", "autonomousWordAlignment"), ("plan_path", "autonomousTimingPlan")):
        workspace.register_optional_artifact(name, rendered.get(key))
    return rendered


def finish_autonomous_run(run_id: str, *, project_id: str, config: dict[str, Any], output_root: str = "runs") -> dict[str, Any]:
    cfg = normalize_autonomous_config(config)
    workspace = RunWorkspace.open_existing(output_root, run_id)
    errors: list[str] = []
    transition = visual = semantic = audio = None
    try:
        transition = rebuild_run_transitions(run_id, output_root=output_root, mode=cfg["transitionMode"])
    except Exception as exc:
        errors.append(f"transition finishing: {exc}")
    try:
        visual = inspect_visual_run(run_id, output_root=output_root)
    except Exception as exc:
        errors.append(f"visual inspection: {exc}")
    if cfg["semanticQa"]:
        try:
            semantic = inspect_run_semantics(run_id, project_id=project_id, output_root=output_root, config={**dict(cfg.get("semantic") or {}), "authorized": cfg["semanticAuthorized"]})
        except Exception as exc:
            errors.append(f"semantic inspection: {exc}")
    result = load_run(run_id, output_root)
    try:
        audio = _finish_audio(workspace, result, cfg)
    except Exception as exc:
        errors.append(f"audio finishing: {exc}")
    queue = load_video_queue(workspace.media_dir) or {}
    memory = load_project_memory(project_id, output_root)
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        report = shot.get("semanticQuality") or shot.get("visualQuality") or {}
        route = shot.get("modelRoute") or {}
        accepted = bool(report.get("accepted", shot.get("status") == "verified"))
        record_shot_outcome(memory, shot=shot, report=report, route=route, accepted=accepted, repair=str(report.get("repairDirective") or ""))
        model = str(route.get("executionModel") or (result.get("media") or {}).get("model") or "")
        if model:
            record_model_outcome(memory, model=model, task=str(route.get("task") or "video"), success=accepted, quality_score=float(report.get("compositeScore", report.get("score", 0)) or 0))
    record_memory_event(memory, "autonomous_finishing_completed", detail=f"Finished run {run_id}", data={"errors": errors})
    save_project_memory(memory, output_root)
    snapshots = snapshot_memory_to_run(run_id, project_id, output_root=output_root)
    edl = build_edit_decision_list(run_id, output_root=output_root)
    quality = _quality(result, visual, semantic)
    quality_path = workspace.write_json("quality/project_quality_report.json", quality)
    workspace.register_artifact("projectQualityReport", quality_path)
    job = {"schemaVersion": 1, "runId": run_id, "projectId": project_id, "updatedAt": utc_now(), "status": result.get("status"), "config": cfg, "quality": quality, "audioFinished": bool(audio), "errors": errors, "artifacts": {**snapshots, "editDecisionList": edl["path"], "projectQualityReport": str(quality_path)}}
    job_path = workspace.write_json("autonomous_job.json", job)
    workspace.register_artifact("autonomousJob", job_path)
    return {"runId": run_id, "projectId": project_id, "result": result, "transition": transition, "visual": visual, "semantic": semantic, "audio": audio, "quality": quality, "memory": memory, "edl": edl["edl"], "job": job, "errors": errors}


def start_autonomous_production(plan: dict[str, Any], *, images: list[Any] | None = None, voices: list[Any] | None = None, output_root: str = "runs", progress: ProgressCallback | None = None) -> dict[str, Any]:
    from .pipeline import run_pipeline
    approved = copy.deepcopy(plan.get("approvedBrief") or {})
    execution = plan.get("executionPlan") or {}
    cfg = normalize_autonomous_config(plan.get("autonomousConfig"))
    project_id = str(plan.get("projectId") or cfg.get("projectId") or "")
    if not approved or not project_id:
        raise AutonomousStudioError("Build the autonomous plan before paid production.")
    result = run_pipeline(approved, images=list(images or []), voices=list(voices or []), output_root=output_root, persist=True, render_media=True, video_mode="ai-video", max_chapters=12, target_runtime_seconds=int(execution.get("targetRuntimeSeconds", 8) or 8), video_max_shots=int(execution.get("plannedShots", 1) or 1), video_batch_size=int(execution.get("batchSize", 1) or 1), video_max_retries=int(execution.get("retriesPerShot", 1) or 1), video_max_provider_calls=int(execution.get("maxProviderCalls", 1) or 1), video_max_spend_usd=float(cfg["maxSpendUsd"]) if cfg["maxSpendUsd"] > 0 else None, video_cost_per_second_usd=float(cfg["costPerSecondUsd"]) if cfg["costPerSecondUsd"] > 0 else None, video_continuous=bool(execution.get("continuous", True)), video_use_continuity=True, progress=progress)
    run_id = _run_id(result)
    if not run_id:
        return {"runId": "", "projectId": project_id, "result": result, "quality": {}, "errors": ["The pipeline returned no durable run ID."]}
    return finish_autonomous_run(run_id, project_id=project_id, config=cfg, output_root=output_root)


def continue_autonomous_production(run_id: str, *, output_root: str = "runs", batch_size: int | None = None, continuous: bool = True) -> dict[str, Any]:
    from .pipeline import resume_video_run
    workspace = RunWorkspace.open_existing(output_root, run_id)
    job_path = workspace.path / "autonomous_job.json"
    if job_path.exists():
        job = json.loads(job_path.read_text(encoding="utf-8"))
    else:
        result = load_run(run_id, output_root)
        brief = result.get("brief") or workspace.manifest.get("brief") or {}
        job = {"projectId": project_id_for(brief), "config": normalize_autonomous_config()}
    cfg = normalize_autonomous_config(job.get("config"))
    queue = load_video_queue(workspace.media_dir) or {}
    qcfg = queue.get("config") or {}
    resume_video_run(run_id, output_root=output_root, batch_size=int(batch_size or cfg.get("batchSize") or qcfg.get("batch_size") or 1), continuous=bool(continuous), max_retries=int(cfg.get("retriesPerShot") or qcfg.get("max_retries_per_shot") or 1), max_provider_calls=int(cfg.get("maxProviderCalls") or qcfg.get("max_provider_calls") or 1), use_continuity=True)
    return finish_autonomous_run(run_id, project_id=str(job.get("projectId") or cfg.get("projectId") or ""), config=cfg, output_root=output_root)


__all__ = ["AutonomousStudioError", "QUALITY_PROFILES", "QualityProfile", "build_edit_decision_list", "continue_autonomous_production", "finish_autonomous_run", "normalize_autonomous_config", "prepare_autonomous_plan", "quality_profile", "recommended_creative_profile", "start_autonomous_production"]
