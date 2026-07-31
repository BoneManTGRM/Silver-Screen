"""Durable closed-loop Autonomous Director orchestration.

The Autonomous Director does not replace the underlying video models. It turns
Silver-Screen into a model-independent production supervisor that plans the
film, binds paid work to an approved shot ledger and world memory, routes each
shot, verifies actual footage, preserves accepted candidates, applies bounded
retakes, rebuilds transitions, optionally adds voices, creates a delivery
master, and emits an evidence report.
"""

from __future__ import annotations

import copy
import html
import json
import os
from pathlib import Path
from typing import Any, Callable

from .candidate_selection import (
    CandidateSelectionError,
    resolve_candidate_retake,
    schedule_candidate_retake,
    shot_quality_score,
)
from .cinematic_continuity import rebuild_run_transitions
from .delivery_master import DeliveryMasterError, create_delivery_master
from .preproduction import build_preproduction_preview
from .previsualization import build_animatic_manifest, render_animatic
from .runtime import (
    RunWorkspace,
    atomic_write_json,
    atomic_write_text,
    load_run,
    utc_now,
)
from .semantic_supervisor import inspect_semantic_run
from .video_runtime import load_video_queue, save_video_queue, update_video_metrics
from .visual_quality import inspect_run as inspect_visual_run


class AutonomousDirectorError(RuntimeError):
    """Raised when autonomous production cannot proceed safely."""


def _clean(value: Any, limit: int = 1800) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    try:
        from . import autonomous_config

        for name in (
            "normalize_autonomous_config",
            "normalize_autonomous_settings",
            "normalize_config",
        ):
            function = getattr(autonomous_config, name, None)
            if callable(function):
                result = function(source)
                if isinstance(result, dict):
                    source = {**source, **result}
                    break
    except Exception:
        pass
    profile = str(source.get("profile") or "prestige").strip().casefold()
    if profile not in {"blockbuster", "prestige", "efficient", "custom"}:
        profile = "prestige"
    defaults = {
        "blockbuster": {
            "planningAttempts": 4,
            "visualTarget": 0.80,
            "semanticTarget": 0.82,
            "transitionTarget": 0.78,
            "projectTarget": 0.82,
            "maxRetakes": 10,
            "maxRetakesPerShot": 3,
            "candidateMinGain": 0.012,
        },
        "prestige": {
            "planningAttempts": 3,
            "visualTarget": 0.76,
            "semanticTarget": 0.78,
            "transitionTarget": 0.74,
            "projectTarget": 0.78,
            "maxRetakes": 6,
            "maxRetakesPerShot": 2,
            "candidateMinGain": 0.015,
        },
        "efficient": {
            "planningAttempts": 2,
            "visualTarget": 0.70,
            "semanticTarget": 0.72,
            "transitionTarget": 0.68,
            "projectTarget": 0.72,
            "maxRetakes": 2,
            "maxRetakesPerShot": 1,
            "candidateMinGain": 0.025,
        },
        "custom": {},
    }[profile]

    def bounded_float(key: str, default: float, low: float, high: float) -> float:
        return max(low, min(high, _float(source.get(key), default)))

    def bounded_int(key: str, default: int, low: int, high: int) -> int:
        return max(low, min(high, _int(source.get(key), default)))

    config = {
        **source,
        "schemaVersion": 1,
        "profile": profile,
        "planningAttempts": bounded_int(
            "planningAttempts", defaults.get("planningAttempts", 3), 1, 8
        ),
        "visualTarget": bounded_float(
            "visualTarget", defaults.get("visualTarget", 0.76), 0.45, 0.98
        ),
        "semanticTarget": bounded_float(
            "semanticTarget", defaults.get("semanticTarget", 0.78), 0.45, 0.98
        ),
        "transitionTarget": bounded_float(
            "transitionTarget", defaults.get("transitionTarget", 0.74), 0.40, 0.98
        ),
        "projectTarget": bounded_float(
            "projectTarget", defaults.get("projectTarget", 0.78), 0.45, 0.98
        ),
        "maxRetakes": bounded_int(
            "maxRetakes", defaults.get("maxRetakes", 6), 0, 30
        ),
        "maxRetakesPerShot": bounded_int(
            "maxRetakesPerShot", defaults.get("maxRetakesPerShot", 2), 0, 8
        ),
        "candidateMinGain": bounded_float(
            "candidateMinGain", defaults.get("candidateMinGain", 0.015), 0.0, 0.25
        ),
        "maxCycles": bounded_int("maxCycles", 10, 1, 20),
        "energyBudget": bounded_int("energyBudget", 80, 3, 500),
        "maxProviderCalls": bounded_int("maxProviderCalls", 0, 0, 1000),
        "maxSpendUsd": bounded_float("maxSpendUsd", 0.0, 0.0, 1_000_000.0),
        "costPerSecondUsd": bounded_float(
            "costPerSecondUsd", 0.0, 0.0, 10_000.0
        ),
        "continuous": bool(source.get("continuous", True)),
        "autoRetakes": bool(source.get("autoRetakes", True)),
        "semanticReview": bool(source.get("semanticReview", True)),
        "voiceEnabled": bool(source.get("voiceEnabled", False)),
        "voiceProvider": str(source.get("voiceProvider") or "openai").strip().casefold(),
        "voiceMode": str(source.get("voiceMode") or "dialogue+narration").strip(),
        "deliveryMaster": str(source.get("deliveryMaster") or "1080p").strip().casefold(),
        "authorized": bool(source.get("authorized", False)),
        "authorizationText": _clean(source.get("authorizationText"), 600),
        "projectId": _clean(source.get("projectId"), 120),
        "projectNotes": _clean(source.get("projectNotes"), 4000),
    }
    return config


def _planning_score(preview: dict[str, Any]) -> dict[str, Any]:
    screenplay = preview.get("screenplayAudit") or {}
    prompt = preview.get("promptGate") or {}
    coverage = preview.get("promptSetAudit") or {}
    metrics = (preview.get("tgrm") or {}).get("metrics") or {}
    screenplay_score = _float(screenplay.get("score"), 0.0) / 100.0
    prompt_score = _float(prompt.get("averageScore"), prompt.get("score", 0.0)) / 100.0
    coverage_score = _float(coverage.get("score"), 100.0 if coverage.get("passed") else 55.0) / 100.0
    narrative_score = _float(metrics.get("finalScore"), 0.75)
    if narrative_score > 1.0:
        narrative_score /= 100.0
    score = (
        screenplay_score * 0.30
        + prompt_score * 0.28
        + coverage_score * 0.22
        + narrative_score * 0.20
    )
    blocking = bool(
        screenplay.get("blocking")
        or prompt.get("blocking")
        or coverage.get("blocking")
    )
    if blocking:
        score *= 0.72
    return {
        "score": round(score, 6),
        "scorePercent": round(score * 100, 1),
        "screenplayScore": round(screenplay_score, 6),
        "promptScore": round(prompt_score, 6),
        "coverageScore": round(coverage_score, 6),
        "narrativeScore": round(narrative_score, 6),
        "blocking": blocking,
    }


def _routing_plan(state: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    try:
        from . import model_routing

        for name in (
            "build_model_routing_plan",
            "build_routing_plan",
            "route_plan",
        ):
            function = getattr(model_routing, name, None)
            if not callable(function):
                continue
            for args in (
                (state, queue),
                (state, queue.get("shots") or []),
                (queue, state),
            ):
                try:
                    value = function(*args)
                    if isinstance(value, dict):
                        return value
                except Exception:
                    continue
    except Exception:
        pass
    active = os.getenv("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3.1-fast")
    routes = []
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        routes.append(
            {
                "shotId": shot.get("id"),
                "order": shot.get("order"),
                "category": "general",
                "model": active,
                "reason": "Use the configured general video model.",
                "fallbackModel": active,
            }
        )
    return {
        "schemaVersion": 1,
        "createdAt": utc_now(),
        "routes": routes,
        "models": sorted({active}),
        "providerCallsMade": 0,
    }


def prepare_autonomous_project(
    brief: dict[str, Any],
    *,
    target_runtime_seconds: int,
    clip_duration_seconds: int = 8,
    max_shots: int = 128,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _normalize_config(config)
    attempts: list[dict[str, Any]] = []
    base_seed = str(brief.get("seed") or brief.get("title") or brief.get("premise") or "film")
    for attempt in range(cfg["planningAttempts"]):
        candidate_brief = copy.deepcopy(brief)
        candidate_brief["seed"] = f"{base_seed}:autonomous-plan:{attempt + 1}"
        candidate_brief["projectId"] = cfg.get("projectId") or brief.get("projectId")
        memory_seed = copy.deepcopy(candidate_brief.get("productionMemory") or {})
        if cfg.get("projectNotes"):
            memory_seed["projectNotes"] = cfg["projectNotes"]
        candidate_brief["productionMemory"] = memory_seed
        preview = build_preproduction_preview(
            candidate_brief,
            target_runtime_seconds=int(target_runtime_seconds),
            clip_duration_seconds=int(clip_duration_seconds),
            max_shots=int(max_shots),
            max_prompt_previews=max(30, min(128, int(max_shots))),
            max_cycles=cfg["maxCycles"],
            energy_budget=cfg["energyBudget"],
        )
        score = _planning_score(preview)
        attempts.append(
            {
                "attempt": attempt + 1,
                "seed": candidate_brief["seed"],
                "score": score,
                "preview": preview,
            }
        )
    selected = max(
        attempts,
        key=lambda item: (
            not item["score"]["blocking"],
            item["score"]["score"],
            -item["attempt"],
        ),
    )
    preview = selected["preview"]
    routing = _routing_plan(
        preview.get("state") or {}, preview.get("queuePreview") or {}
    )
    animatic = build_animatic_manifest(preview)
    return {
        "schemaVersion": 1,
        "createdAt": utc_now(),
        "config": cfg,
        "selectedAttempt": selected["attempt"],
        "selectedSeed": selected["seed"],
        "planningScore": selected["score"],
        "planningAttempts": [
            {
                "attempt": item["attempt"],
                "seed": item["seed"],
                "score": item["score"],
            }
            for item in attempts
        ],
        "preview": preview,
        "routingPlan": routing,
        "animatic": animatic,
        "providerCallsMade": 0,
    }


def _approved_brief(plan: dict[str, Any]) -> dict[str, Any]:
    preview = plan["preview"]
    brief = copy.deepcopy(preview.get("brief") or {})
    creative = copy.deepcopy(preview.get("creativeDirection") or {})
    creative["enforceApprovalGates"] = False
    creative["approvals"] = {
        "scriptApproved": True,
        "promptsApproved": True,
        "budgetApproved": True,
        "approvedAt": utc_now(),
        "approvalSource": "autonomous_director_single_budget_authorization",
    }
    shot_direction = copy.deepcopy(preview.get("shotDirection") or {})
    ledger = copy.deepcopy(preview.get("promptLedger") or {})
    shot_direction.update(
        {
            "enforcePromptLedger": True,
            "approvedPromptLedger": ledger,
            "approvedLedgerHash": ledger.get("ledgerHash"),
        }
    )
    state = preview.get("state") or {}
    brief.update(
        {
            "seed": plan["selectedSeed"],
            "creativeDirection": creative,
            "shotDirection": shot_direction,
            "productionMemory": copy.deepcopy(state.get("productionMemory") or {}),
            "projectId": state.get("projectId") or plan["config"].get("projectId"),
            "autonomousDirector": copy.deepcopy(plan["config"]),
        }
    )
    return brief


def _autonomous_path(workspace: RunWorkspace) -> Path:
    return workspace.path / "autonomous_director.json"


def _state_template(plan: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "runId": run_id,
        "status": "planned" if not run_id else "generating",
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "config": plan["config"],
        "planning": {
            "selectedAttempt": plan["selectedAttempt"],
            "selectedSeed": plan["selectedSeed"],
            "score": plan["planningScore"],
            "attempts": plan["planningAttempts"],
            "previewFingerprint": plan["preview"].get("fingerprint"),
            "ledgerHash": (plan["preview"].get("promptLedger") or {}).get("ledgerHash"),
        },
        "routingPlan": plan["routingPlan"],
        "retakes": [],
        "stages": {
            "planning": "complete",
            "animatic": "planned",
            "generation": "pending",
            "visualReview": "pending",
            "semanticReview": "pending",
            "transitions": "pending",
            "candidateSelection": "pending",
            "voice": "pending" if plan["config"].get("voiceEnabled") else "skipped",
            "deliveryMaster": "pending",
            "evidence": "pending",
        },
        "events": [
            {
                "at": utc_now(),
                "type": "autonomous_plan_selected",
                "detail": f"Planning attempt {plan['selectedAttempt']} selected",
            }
        ],
    }


def _save_autonomous(workspace: RunWorkspace, state: dict[str, Any]) -> str:
    state["updatedAt"] = utc_now()
    path = _autonomous_path(workspace)
    atomic_write_json(path, state)
    workspace.register_artifact("autonomousDirector", path)
    return str(path)


def load_autonomous_state(
    run_id: str, *, output_root: str = "runs"
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    path = _autonomous_path(workspace)
    if not path.exists():
        raise AutonomousDirectorError(
            "The selected production has no Autonomous Director state"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutonomousDirectorError(
            "The Autonomous Director state is unreadable"
        ) from exc
    if not isinstance(payload, dict):
        raise AutonomousDirectorError(
            "The Autonomous Director state has an invalid format"
        )
    return payload


def _register_plan_artifacts(
    workspace: RunWorkspace,
    plan: dict[str, Any],
) -> dict[str, Any]:
    root = workspace.path / "preproduction"
    root.mkdir(parents=True, exist_ok=True)
    preview_path = root / "autonomous_preproduction.json"
    routing_path = root / "model_routing_plan.json"
    atomic_write_json(preview_path, plan)
    atomic_write_json(routing_path, plan["routingPlan"])
    animatic = render_animatic(plan["animatic"], root / "animatic")
    workspace.register_artifact("autonomousPreproduction", preview_path)
    workspace.register_artifact("modelRoutingPlan", routing_path)
    workspace.register_artifact("animaticManifest", animatic["manifestPath"])
    workspace.register_artifact("storyboard", animatic["storyboardPath"])
    if animatic.get("videoPath"):
        workspace.register_artifact("directorAnimatic", animatic["videoPath"])
    return {
        "previewPath": str(preview_path),
        "routingPath": str(routing_path),
        **animatic,
    }


def start_autonomous_production(
    brief: dict[str, Any],
    *,
    images: list[Any] | None = None,
    output_root: str = "runs",
    target_runtime_seconds: int,
    clip_duration_seconds: int = 8,
    max_shots: int = 128,
    batch_size: int = 1,
    config: dict[str, Any] | None = None,
    progress: Callable[[str, int, str], None] | None = None,
) -> dict[str, Any]:
    from .pipeline import run_pipeline

    plan = prepare_autonomous_project(
        brief,
        target_runtime_seconds=target_runtime_seconds,
        clip_duration_seconds=clip_duration_seconds,
        max_shots=max_shots,
        config=config,
    )
    cfg = plan["config"]
    if not cfg.get("authorized"):
        raise AutonomousDirectorError(
            "Autonomous paid production requires one explicit provider-budget authorization"
        )
    render_plan = plan["preview"].get("renderPlan") or {}
    planned_shots = int(render_plan.get("plannedShots", max_shots) or max_shots)
    provider_calls = int(cfg.get("maxProviderCalls", 0) or 0)
    if provider_calls <= 0:
        provider_calls = planned_shots * (1 + int(cfg["maxRetakesPerShot"]))
    approved = _approved_brief(plan)
    result = run_pipeline(
        approved,
        images=list(images or []),
        output_root=output_root,
        persist=True,
        render_media=True,
        video_mode="ai-video",
        max_chapters=12,
        target_runtime_seconds=int(render_plan.get("targetRuntimeSeconds", target_runtime_seconds)),
        video_max_shots=planned_shots,
        video_batch_size=max(1, min(16, int(batch_size))),
        video_max_retries=max(1, int(cfg["maxRetakesPerShot"])),
        video_max_provider_calls=provider_calls,
        video_max_spend_usd=float(cfg.get("maxSpendUsd", 0) or 0),
        video_cost_per_second_usd=float(cfg.get("costPerSecondUsd", 0) or 0),
        video_continuous=bool(cfg.get("continuous")),
        video_use_continuity=True,
        max_cycles=int(cfg["maxCycles"]),
        energy_budget=int(cfg["energyBudget"]),
        progress=progress,
    )
    run_id = str((result.get("run") or {}).get("id") or "")
    if not run_id:
        raise AutonomousDirectorError(
            "The production started without a durable run identifier"
        )
    workspace = RunWorkspace.open_existing(output_root, run_id)
    state = _state_template(plan, run_id)
    state["stages"]["generation"] = str((result.get("media") or {}).get("status") or result.get("status") or "unknown")
    artifacts = _register_plan_artifacts(workspace, plan)
    state["animatic"] = artifacts
    state["stages"]["animatic"] = "complete"
    _save_autonomous(workspace, state)
    return advance_autonomous_production(
        run_id,
        output_root=output_root,
        resume_generation=False,
        progress=progress,
    )


def _transition_map(queue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    plan = queue.get("transitionPlan") or {}
    return {
        str(item.get("toShot") or ""): item
        for item in plan.get("transitions") or []
        if isinstance(item, dict)
    }


def _refresh_quality(
    run_id: str,
    *,
    output_root: str,
    semantic: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    visual = inspect_visual_run(run_id, output_root=output_root)
    semantic_result = (
        inspect_semantic_run(run_id, output_root=output_root)
        if semantic
        else None
    )
    workspace = RunWorkspace.open_existing(output_root, run_id)
    queue = load_video_queue(workspace.media_dir) or {}
    transitions = _transition_map(queue)
    for shot in queue.get("shots") or []:
        if isinstance(shot, dict):
            shot["transitionIn"] = transitions.get(str(shot.get("id") or ""), {})
    save_video_queue(workspace.media_dir, queue)
    return visual, semantic_result


def _weakest_candidate(
    queue: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict) or shot.get("status") != "verified":
            continue
        history = shot.get("candidateHistory") or []
        if len(history) >= int(config["maxRetakesPerShot"]):
            continue
        quality = shot_quality_score(shot)
        visual = shot.get("visualQuality") or {}
        semantic = shot.get("semanticQuality") or {}
        transition = shot.get("transitionIn") or {}
        visual_score = _float(visual.get("score"), 0.70)
        semantic_score = _float(semantic.get("score"), 0.70)
        transition_score = _float(transition.get("effectiveScore"), 0.72)
        deficits = {
            "visual": max(0.0, float(config["visualTarget"]) - visual_score),
            "semantic": max(0.0, float(config["semanticTarget"]) - semantic_score),
            "transition": max(0.0, float(config["transitionTarget"]) - transition_score),
        }
        priority = max(deficits.values()) + sum(deficits.values()) * 0.30
        if priority <= 0:
            continue
        findings = [
            str(item.get("repair") or item.get("message") or "")
            for report in (visual, semantic)
            for item in report.get("findings") or []
            if isinstance(item, dict)
        ]
        transition_directive = str(transition.get("promptDirective") or "")
        directive = " ".join(
            part
            for part in [
                *findings,
                transition_directive if deficits["transition"] > 0 else "",
            ]
            if part
        )[:2600]
        candidates.append(
            {
                "shot": shot,
                "quality": quality,
                "deficits": deficits,
                "priority": round(priority, 6),
                "directive": directive
                or "Improve the shot to match the approved visual and semantic contract.",
            }
        )
    candidates.sort(
        key=lambda item: (
            -float(item["priority"]),
            int((item["shot"] or {}).get("order", 0) or 0),
        )
    )
    return candidates[0] if candidates else None


def _maybe_voice(run_id: str, config: dict[str, Any], output_root: str) -> dict[str, Any]:
    if not config.get("voiceEnabled"):
        return {"status": "skipped", "reason": "Voice finishing was not requested"}
    try:
        from .voice_pipeline import attach_voice_to_run
        from .voice_production import default_voice_request
        from .voice_providers import provider_capabilities

        caps = provider_capabilities()
        provider = str(config.get("voiceProvider") or "openai")
        if not caps.get(provider):
            return {
                "status": "blocked",
                "reason": f"{provider} voice provider is not configured",
            }
        request = default_voice_request()
        request.update(
            {
                "provider": provider,
                "mode": config.get("voiceMode") or "dialogue+narration",
                "includeCaptions": True,
                "burnCaptions": False,
            }
        )
        result = attach_voice_to_run(
            run_id,
            request,
            output_root=output_root,
            manual_tracks=None,
        )
        return {"status": "complete", "result": result}
    except Exception as exc:
        return {"status": "blocked", "reason": str(exc)}


def _project_quality(
    result: dict[str, Any],
    queue: dict[str, Any],
    autonomous: dict[str, Any],
) -> dict[str, Any]:
    shots = [
        item
        for item in queue.get("shots") or []
        if isinstance(item, dict) and item.get("status") == "verified"
    ]
    planned = max(1, int((queue.get("metrics") or {}).get("plannedShots", len(shots)) or len(shots) or 1))
    completion = min(1.0, len(shots) / planned)
    visual_scores = [
        _float((item.get("visualQuality") or {}).get("score"), 0.65)
        for item in shots
    ]
    semantic_scores = [
        _float((item.get("semanticQuality") or {}).get("score"), 0.65)
        for item in shots
    ]
    semantic_provider = sum(
        (item.get("semanticQuality") or {}).get("evidenceQuality") == "provider"
        for item in shots
    )
    transition_scores = [
        _float((item.get("transitionIn") or {}).get("effectiveScore"), 0.72)
        for item in shots[1:]
    ]
    planning = autonomous.get("planning") or {}
    planning_score = _float((planning.get("score") or {}).get("score"), 0.70)
    visual = sum(visual_scores) / len(visual_scores) if visual_scores else 0.0
    semantic = sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0
    transitions = (
        sum(transition_scores) / len(transition_scores)
        if transition_scores
        else (1.0 if len(shots) <= 1 else 0.0)
    )
    score = (
        completion * 0.20
        + visual * 0.25
        + semantic * 0.22
        + transitions * 0.18
        + planning_score * 0.15
    )
    confidence = (
        0.94
        if shots and semantic_provider == len(shots)
        else 0.78
        if semantic_provider
        else 0.62
    )
    return {
        "score": round(score, 6),
        "scorePercent": round(score * 100, 1),
        "confidence": confidence,
        "completion": round(completion, 6),
        "visual": round(visual, 6),
        "semantic": round(semantic, 6),
        "transitions": round(transitions, 6),
        "planning": round(planning_score, 6),
        "semanticProviderReviewed": semantic_provider,
        "verifiedShots": len(shots),
        "plannedShots": planned,
        "target": autonomous["config"].get("projectTarget"),
        "passed": score >= float(autonomous["config"].get("projectTarget", 0.78)),
    }


def _evidence_html(report: dict[str, Any]) -> str:
    rows = []
    for shot in report.get("shots") or []:
        rows.append(
            "<tr><td>{order}</td><td>{shot}</td><td>{visual:.1f}</td>"
            "<td>{semantic:.1f}</td><td>{transition:.1f}</td><td>{model}</td>"
            "<td>{selection}</td></tr>".format(
                order=html.escape(str(shot.get("order"))),
                shot=html.escape(str(shot.get("shotId"))),
                visual=float(shot.get("visualScore", 0) or 0) * 100,
                semantic=float(shot.get("semanticScore", 0) or 0) * 100,
                transition=float(shot.get("transitionScore", 0) or 0) * 100,
                model=html.escape(str(shot.get("model") or "configured default")),
                selection=html.escape(str(shot.get("candidateSelection") or "original")),
            )
        )
    quality = report.get("projectQuality") or {}
    return """<!doctype html><html><head><meta charset='utf-8'><title>Silver-Screen Autonomous Director Evidence</title><style>
body{{font:15px system-ui;background:#11131a;color:#edf0f5;margin:0;padding:36px}}main{{max-width:1180px;margin:auto}}.score{{font-size:54px;font-weight:700;color:#dbb56e}}table{{width:100%;border-collapse:collapse;margin-top:24px}}th,td{{border-bottom:1px solid #303544;padding:10px;text-align:left}}th{{color:#a9c8ea}}pre{{white-space:pre-wrap;background:#1b1e28;padding:16px;border-radius:12px}}</style></head><body><main>
<h1>Autonomous Director Evidence Report</h1><p>{title}</p><div class='score'>{score:.1f}/100</div><p>Confidence {confidence:.0%} · {verified}/{planned} verified shots</p>
<table><thead><tr><th>#</th><th>Shot</th><th>Visual</th><th>Semantic</th><th>Transition</th><th>Model</th><th>Candidate</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Production memory</h2><pre>{memory}</pre><h2>Cost and provider evidence</h2><pre>{cost}</pre></main></body></html>""".format(
        title=html.escape(str(report.get("title") or "Untitled")),
        score=float(quality.get("scorePercent", 0) or 0),
        confidence=float(quality.get("confidence", 0) or 0),
        verified=quality.get("verifiedShots", 0),
        planned=quality.get("plannedShots", 0),
        rows="".join(rows),
        memory=html.escape(json.dumps(report.get("productionMemory") or {}, indent=2)),
        cost=html.escape(json.dumps(report.get("providerEvidence") or {}, indent=2)),
    )


def write_evidence_report(
    run_id: str,
    *,
    output_root: str = "runs",
    autonomous_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    queue = load_video_queue(workspace.media_dir) or {}
    autonomous = autonomous_state or load_autonomous_state(run_id, output_root=output_root)
    quality = _project_quality(result, queue, autonomous)
    shots = []
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict) or shot.get("status") != "verified":
            continue
        score = shot_quality_score(shot)
        shots.append(
            {
                "shotId": shot.get("id"),
                "order": shot.get("order"),
                "scene": (shot.get("sourceScene") or {}).get("number"),
                "qualityScore": score.get("score"),
                "visualScore": score.get("visualScore"),
                "semanticScore": score.get("semanticScore"),
                "transitionScore": score.get("transitionScore"),
                "semanticEvidence": score.get("semanticEvidence"),
                "model": (shot.get("modelRoute") or {}).get("model") or shot.get("providerModel"),
                "modelRoute": shot.get("modelRoute") or {},
                "candidateSelection": (shot.get("candidateSelection") or {}).get("selection"),
                "candidateHistory": shot.get("candidateHistory") or [],
                "visualFindings": (shot.get("visualQuality") or {}).get("findings") or [],
                "semanticFindings": (shot.get("semanticQuality") or {}).get("findings") or [],
                "promptLedgerVerified": shot.get("promptLedgerVerified"),
            }
        )
    metrics = queue.get("metrics") or {}
    report = {
        "schemaVersion": 1,
        "runId": run_id,
        "createdAt": utc_now(),
        "title": (result.get("state") or {}).get("title"),
        "status": result.get("status"),
        "projectQuality": quality,
        "productionMemory": (result.get("state") or {}).get("productionMemory") or result.get("productionMemory") or {},
        "planning": autonomous.get("planning") or {},
        "routingPlan": autonomous.get("routingPlan") or {},
        "retakes": autonomous.get("retakes") or [],
        "shots": shots,
        "transitionMetrics": ((queue.get("transitionPlan") or {}).get("metrics") or {}),
        "providerEvidence": {
            "providerCalls": metrics.get("providerCalls"),
            "estimatedSpendUsd": metrics.get("estimatedSpendUsd"),
            "authorizedCallCeiling": (queue.get("config") or {}).get("max_provider_calls"),
            "authorizedSpendCeilingUsd": (queue.get("config") or {}).get("max_spend_usd"),
            "verifiedRuntimeSeconds": metrics.get("verifiedRuntimeSeconds"),
        },
        "artifacts": result.get("artifacts") or {},
    }
    json_path = workspace.path / "autonomous_evidence_report.json"
    html_path = workspace.path / "autonomous_evidence_report.html"
    atomic_write_json(json_path, report)
    atomic_write_text(html_path, _evidence_html(report))
    workspace.register_artifact("autonomousEvidenceJson", json_path)
    workspace.register_artifact("autonomousEvidenceHtml", html_path)
    return {
        "report": report,
        "jsonPath": str(json_path),
        "htmlPath": str(html_path),
    }


def advance_autonomous_production(
    run_id: str,
    *,
    output_root: str = "runs",
    resume_generation: bool = True,
    progress: Callable[[str, int, str], None] | None = None,
) -> dict[str, Any]:
    from .pipeline import resume_video_run

    workspace = RunWorkspace.open_existing(output_root, run_id)
    autonomous = load_autonomous_state(run_id, output_root=output_root)
    config = autonomous["config"]
    result = load_run(run_id, output_root)
    media = result.get("media") or {}
    media_status = str(media.get("status") or result.get("status") or "unknown")
    if resume_generation and media_status in {"partial", "blocked", "running"}:
        if media.get("stopReason") not in {
            "provider_call_budget_exhausted",
            "estimated_spend_budget_exhausted",
            "retry_budget_exhausted",
        }:
            result = resume_video_run(
                run_id,
                output_root=output_root,
                batch_size=1,
                continuous=bool(config.get("continuous")),
                max_retries=int(config["maxRetakesPerShot"]),
                max_provider_calls=int(config.get("maxProviderCalls", 0) or 0) or None,
                use_continuity=True,
                progress=progress,
            )
            media = result.get("media") or {}
            media_status = str(media.get("status") or result.get("status") or "unknown")
    autonomous["stages"]["generation"] = media_status
    autonomous["events"].append(
        {
            "at": utc_now(),
            "type": "generation_checkpoint",
            "detail": media_status,
        }
    )
    if media_status not in {"complete", "partial"} or not (media.get("video_paths") or media.get("final_video_path") or media.get("partial_video_path")):
        autonomous["status"] = media_status
        _save_autonomous(workspace, autonomous)
        return {
            "runId": run_id,
            "status": media_status,
            "result": result,
            "autonomous": autonomous,
            "resumeRequired": True,
        }

    rebuilt = rebuild_run_transitions(run_id, output_root=output_root, mode="auto")
    autonomous["stages"]["transitions"] = "complete"
    visual, semantic_result = _refresh_quality(
        run_id,
        output_root=output_root,
        semantic=bool(config.get("semanticReview")),
    )
    autonomous["stages"]["visualReview"] = "complete"
    autonomous["stages"]["semanticReview"] = (
        "complete" if semantic_result else "skipped"
    )

    queue = load_video_queue(workspace.media_dir) or {}
    retake_count = len(autonomous.get("retakes") or [])
    while (
        media_status == "complete"
        and config.get("autoRetakes")
        and retake_count < int(config["maxRetakes"])
    ):
        weakest = _weakest_candidate(queue, config)
        if weakest is None:
            break
        shot = weakest["shot"]
        shot_id = str(shot.get("id") or "")
        try:
            scheduled = schedule_candidate_retake(
                run_id,
                shot_id,
                directive=weakest["directive"],
                output_root=output_root,
                reason=json.dumps(weakest["deficits"], sort_keys=True),
                source="autonomous_director",
            )
            before_calls = int((queue.get("metrics") or {}).get("providerCalls", 0) or 0)
            ceiling = int(config.get("maxProviderCalls", 0) or 0)
            if ceiling and before_calls >= ceiling:
                break
            resumed = resume_video_run(
                run_id,
                output_root=output_root,
                batch_size=1,
                continuous=False,
                max_retries=int(config["maxRetakesPerShot"]),
                max_provider_calls=ceiling or int((scheduled["queue"].get("config") or {}).get("max_provider_calls", 0) or 0),
                use_continuity=True,
                progress=progress,
            )
            media_status = str((resumed.get("media") or {}).get("status") or resumed.get("status") or "unknown")
            if media_status not in {"complete", "partial"}:
                break
            rebuild_run_transitions(run_id, output_root=output_root, mode="auto")
            _refresh_quality(
                run_id,
                output_root=output_root,
                semantic=bool(config.get("semanticReview")),
            )
            resolution = resolve_candidate_retake(
                run_id,
                shot_id,
                output_root=output_root,
                minimum_gain=float(config["candidateMinGain"]),
            )
            rebuild_run_transitions(run_id, output_root=output_root, mode="auto")
            autonomous.setdefault("retakes", []).append(
                {
                    "at": utc_now(),
                    "shotId": shot_id,
                    "order": shot.get("order"),
                    "deficits": weakest["deficits"],
                    "previousQuality": weakest["quality"],
                    "selection": resolution["selection"],
                    "gain": resolution["gain"],
                }
            )
            retake_count += 1
            queue = load_video_queue(workspace.media_dir) or {}
        except (CandidateSelectionError, Exception) as exc:
            autonomous.setdefault("warnings", []).append(str(exc))
            break
    autonomous["stages"]["candidateSelection"] = "complete"

    voice = _maybe_voice(run_id, config, output_root)
    autonomous["voice"] = voice
    autonomous["stages"]["voice"] = voice["status"]

    master: dict[str, Any]
    try:
        master = create_delivery_master(
            run_id,
            output_root=output_root,
            profile=str(config.get("deliveryMaster") or "1080p"),
        )
        autonomous["stages"]["deliveryMaster"] = "complete"
    except DeliveryMasterError as exc:
        master = {"status": "blocked", "reason": str(exc)}
        autonomous["stages"]["deliveryMaster"] = "blocked"
    autonomous["deliveryMaster"] = master

    try:
        from .production_memory import persist_run_memory

        autonomous["productionMemory"] = persist_run_memory(
            run_id, output_root=output_root
        )
    except Exception as exc:
        autonomous.setdefault("warnings", []).append(
            f"Production memory finalization failed: {exc}"
        )

    evidence = write_evidence_report(
        run_id,
        output_root=output_root,
        autonomous_state=autonomous,
    )
    autonomous["evidence"] = {
        "jsonPath": evidence["jsonPath"],
        "htmlPath": evidence["htmlPath"],
        "projectQuality": evidence["report"]["projectQuality"],
    }
    autonomous["stages"]["evidence"] = "complete"
    quality = evidence["report"]["projectQuality"]
    autonomous["status"] = (
        "complete"
        if quality.get("passed") and media_status == "complete"
        else "attention"
        if media_status == "complete"
        else media_status
    )
    path = _save_autonomous(workspace, autonomous)
    return {
        "runId": run_id,
        "status": autonomous["status"],
        "result": load_run(run_id, output_root),
        "autonomous": autonomous,
        "autonomousPath": path,
        "projectQuality": quality,
        "evidence": evidence,
        "deliveryMaster": master,
        "resumeRequired": media_status != "complete",
    }


__all__ = [
    "AutonomousDirectorError",
    "advance_autonomous_production",
    "load_autonomous_state",
    "prepare_autonomous_project",
    "start_autonomous_production",
    "write_evidence_report",
]
