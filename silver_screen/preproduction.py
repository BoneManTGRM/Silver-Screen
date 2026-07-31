"""Offline screenplay, shot-prompt, and approval-gate preview.

This module performs no provider request. It builds the same deterministic story
state used by the paid pipeline, runs narrative TGRM, applies creative-direction
polish, expands the requested runtime into a shot queue, and exposes the exact
prompt text that will be sent for the first planned shots.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .ai_video import scene_prompt
from .creative_direction import (
    audit_prompt,
    audit_screenplay,
    finalize_creative_state,
    normalize_creative_direction,
)
from .pipeline import brief_fingerprint, validate_brief
from .script_engine import build_film_from_brief, render_screenplay
from .tgrm import run_tgrm
from .video_runtime import create_video_queue, normalize_video_config


def request_fingerprint(
    brief: dict[str, Any],
    *,
    target_runtime_seconds: int,
    clip_duration_seconds: int,
    max_shots: int,
) -> str:
    normalized = validate_brief(brief)
    payload = {
        "brief": normalized,
        "render": {
            "targetRuntimeSeconds": int(target_runtime_seconds),
            "clipDurationSeconds": int(clip_duration_seconds),
            "maxShots": int(max_shots),
        },
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scene_by_number(state: dict[str, Any], number: int) -> dict[str, Any]:
    scenes = [item for item in state.get("scenes") or [] if isinstance(item, dict)]
    for scene in scenes:
        if int(scene.get("number", -1) or -1) == int(number):
            return scene
    if not scenes:
        raise ValueError("The screenplay preview contains no scenes")
    return scenes[min(len(scenes) - 1, max(0, int(number) - 1))]


def build_preproduction_preview(
    brief: dict[str, Any],
    *,
    target_runtime_seconds: int,
    clip_duration_seconds: int = 8,
    max_shots: int = 128,
    max_prompt_previews: int = 12,
    max_cycles: int = 8,
    energy_budget: int = 40,
) -> dict[str, Any]:
    """Build a provider-free draft that must be approved before paid rendering."""

    normalized = validate_brief(brief)
    direction = normalize_creative_direction(normalized.get("creativeDirection"))
    film = build_film_from_brief(
        premise=normalized["premise"],
        genre=normalized["genre"],
        tone=normalized["tone"],
        title=normalized["title"],
        fmt=normalized["format"],
        scars=normalized["scars"],
        seed=normalized["seed"],
        cast=normalized["cast"],
        creative_direction=direction,
        authored_script=normalized.get("authoredScript"),
    )
    repaired = run_tgrm(
        film,
        max_cycles=max(1, min(20, int(max_cycles))),
        energy_budget=max(3, min(500, int(energy_budget))),
    )
    state = finalize_creative_state(
        repaired.get("state") or film,
        render_screenplay_fn=render_screenplay,
    )
    config = normalize_video_config(
        target_runtime_seconds=int(target_runtime_seconds),
        clip_duration_seconds=int(clip_duration_seconds),
        max_shots=int(max_shots),
        batch_size=0,
        max_retries_per_shot=0,
        max_provider_calls=0,
        use_continuity_frames=True,
    )
    queue = create_video_queue(state, config)
    state["_videoShots"] = queue.get("shots") or []

    prompts: list[dict[str, Any]] = []
    prompt_scores: list[float] = []
    for shot in (queue.get("shots") or [])[: max(1, min(30, int(max_prompt_previews)))]:
        if not isinstance(shot, dict):
            continue
        source = shot.get("sourceScene") or {}
        scene = _scene_by_number(state, int(source.get("number", 1) or 1))
        prompt = scene_prompt(state, scene, shot)
        audit = audit_prompt(prompt, direction)
        prompt_scores.append(float(audit["score"]))
        prompts.append(
            {
                "shotId": shot.get("id"),
                "order": shot.get("order"),
                "scene": source.get("number"),
                "chapter": source.get("chapter"),
                "segment": shot.get("segment"),
                "prompt": prompt,
                "audit": audit,
            }
        )

    screenplay_audit = audit_screenplay(
        str(state.get("script") or ""),
        direction=direction,
        state=state,
    )
    minimum_prompt = int(direction.get("minimumPromptScore", 80) or 80)
    lowest_prompt = min(prompt_scores) if prompt_scores else 0.0
    prompt_gate = {
        "score": round(lowest_prompt, 1),
        "averageScore": round(sum(prompt_scores) / len(prompt_scores), 1)
        if prompt_scores
        else 0.0,
        "minimumScore": minimum_prompt,
        "passed": bool(prompt_scores) and lowest_prompt >= minimum_prompt,
        "blocking": bool(direction.get("strictGate"))
        and (not prompt_scores or lowest_prompt < minimum_prompt),
    }
    strict_passed = not screenplay_audit["blocking"] and not prompt_gate["blocking"]
    fingerprint = request_fingerprint(
        normalized,
        target_runtime_seconds=config.target_runtime_seconds,
        clip_duration_seconds=config.clip_duration_seconds,
        max_shots=config.max_shots,
    )
    return {
        "schemaVersion": 1,
        "fingerprint": fingerprint,
        "briefFingerprint": brief_fingerprint(normalized),
        "brief": normalized,
        "creativeDirection": direction,
        "state": state,
        "screenplay": str(state.get("script") or ""),
        "screenplayAudit": screenplay_audit,
        "promptGate": prompt_gate,
        "strictGatePassed": strict_passed,
        "prompts": prompts,
        "queuePreview": queue,
        "renderPlan": {
            "targetRuntimeSeconds": config.target_runtime_seconds,
            "plannedRuntimeSeconds": config.planned_runtime_seconds,
            "clipDurationSeconds": config.clip_duration_seconds,
            "plannedShots": config.planned_shots,
        },
        "tgrm": {
            "metrics": repaired.get("metrics") or {},
            "msil": repaired.get("msil") or {},
            "remainingFractures": repaired.get("remainingFractures") or [],
        },
        "providerCallsMade": 0,
    }
