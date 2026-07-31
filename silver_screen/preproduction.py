"""Offline screenplay, shot-ledger, and approval-gate preview.

This module makes no provider request. It builds the same deterministic story,
TGRM state, shot queue, shot-specific prompts, negative prompts, and continuity
variants that the paid pipeline will use.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .creative_direction import (
    audit_prompt,
    audit_screenplay,
    finalize_creative_state,
    normalize_creative_direction,
)
from .pipeline import brief_fingerprint, validate_brief
from .script_engine import build_film_from_brief, render_screenplay
from .shot_director import (
    audit_prompt_set,
    build_prompt_ledger,
    normalize_shot_direction,
)
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


def _audit_positive_prompt(
    prompt: str, direction: dict[str, Any]
) -> dict[str, Any]:
    """Audit requested imagery without penalizing explicit exclusions."""

    positive, separator, avoid_contract = str(prompt or "").partition("Avoid:")
    audit = audit_prompt(positive, direction)
    audit["avoidContractPresent"] = bool(separator and avoid_contract.strip())
    audit["avoidContractItems"] = len(direction.get("avoid") or [])
    return audit


def build_preproduction_preview(
    brief: dict[str, Any],
    *,
    target_runtime_seconds: int,
    clip_duration_seconds: int = 8,
    max_shots: int = 128,
    max_prompt_previews: int = 30,
    max_cycles: int = 8,
    energy_budget: int = 40,
) -> dict[str, Any]:
    """Build a provider-free draft that must be approved before paid rendering."""

    normalized = validate_brief(brief)
    direction = normalize_creative_direction(
        normalized.get("creativeDirection")
    )
    shot_direction = normalize_shot_direction(
        normalized.get("shotDirection")
    )
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
        shot_direction=shot_direction,
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
    state["shotDirection"] = shot_direction
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

    ledger = build_prompt_ledger(state, queue)
    coverage_audit = audit_prompt_set(ledger, shot_direction)
    prompt_records: list[dict[str, Any]] = []
    prompt_scores: list[float] = []
    display_limit = max(1, min(60, int(max_prompt_previews)))
    for index, entry in enumerate(ledger.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        expected_continuity = int(entry.get("order", 0) or 0) > 1
        prompt = str(
            entry.get(
                "promptWithContinuity"
                if expected_continuity
                else "promptWithoutContinuity"
            )
            or ""
        )
        audit = _audit_positive_prompt(prompt, direction)
        prompt_scores.append(float(audit["score"]))
        if index < display_limit:
            prompt_records.append(
                {
                    "shotId": entry.get("shotId"),
                    "order": entry.get("order"),
                    "scene": entry.get("scene"),
                    "chapter": entry.get("chapter"),
                    "segment": entry.get("segment"),
                    "blueprint": entry.get("blueprint") or {},
                    "prompt": prompt,
                    "fallbackPrompt": entry.get("promptWithoutContinuity"),
                    "negativePrompt": entry.get("negativePrompt"),
                    "expectedContinuity": expected_continuity,
                    "promptHash": entry.get(
                        "promptHashWithContinuity"
                        if expected_continuity
                        else "promptHashWithoutContinuity"
                    ),
                    "negativePromptHash": entry.get("negativePromptHash"),
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
        "averageScore": (
            round(sum(prompt_scores) / len(prompt_scores), 1)
            if prompt_scores
            else 0.0
        ),
        "minimumScore": minimum_prompt,
        "evaluatedPrompts": len(prompt_scores),
        "passed": bool(prompt_scores) and lowest_prompt >= minimum_prompt,
        "blocking": bool(direction.get("strictGate"))
        and (not prompt_scores or lowest_prompt < minimum_prompt),
    }
    strict_passed = (
        not screenplay_audit["blocking"]
        and not prompt_gate["blocking"]
        and not coverage_audit["blocking"]
    )
    fingerprint = request_fingerprint(
        normalized,
        target_runtime_seconds=config.target_runtime_seconds,
        clip_duration_seconds=config.clip_duration_seconds,
        max_shots=config.max_shots,
    )
    return {
        "schemaVersion": 2,
        "fingerprint": fingerprint,
        "briefFingerprint": brief_fingerprint(normalized),
        "brief": normalized,
        "creativeDirection": direction,
        "shotDirection": shot_direction,
        "state": state,
        "screenplay": str(state.get("script") or ""),
        "screenplayAudit": screenplay_audit,
        "promptGate": prompt_gate,
        "promptSetAudit": coverage_audit,
        "promptLedger": ledger,
        "strictGatePassed": strict_passed,
        "prompts": prompt_records,
        "promptPreviewCount": len(prompt_records),
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
            "remainingFractures": repaired.get(
                "remainingFractures"
            )
            or [],
        },
        "providerCallsMade": 0,
    }
