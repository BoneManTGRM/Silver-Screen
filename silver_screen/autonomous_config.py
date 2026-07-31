"""Shared configuration for one-click autonomous production."""

from __future__ import annotations

import copy
import os
from typing import Any


AUTONOMOUS_PROFILES: dict[str, dict[str, Any]] = {
    "blockbuster": {
        "label": "Blockbuster target",
        "description": (
            "Maximum available planning, memory, semantic review, candidate comparison, "
            "visual QA, continuity repair, voice finishing, and delivery mastering."
        ),
        "visualTarget": 0.82,
        "semanticTarget": 0.82,
        "transitionTarget": 0.80,
        "projectTarget": 0.82,
        "maxRetakes": 8,
        "maxRetakesPerShot": 2,
        "planningAttempts": 3,
        "maxCycles": 14,
        "energyBudget": 160,
        "candidateMinGain": 0.015,
    },
    "prestige": {
        "label": "Prestige production",
        "description": "High-quality planning and selective repair with a smaller retry reserve.",
        "visualTarget": 0.76,
        "semanticTarget": 0.76,
        "transitionTarget": 0.74,
        "projectTarget": 0.76,
        "maxRetakes": 4,
        "maxRetakesPerShot": 1,
        "planningAttempts": 2,
        "maxCycles": 12,
        "energyBudget": 110,
        "candidateMinGain": 0.02,
    },
    "efficient": {
        "label": "Efficient autonomous",
        "description": "One-click orchestration with conservative cost and minimal retakes.",
        "visualTarget": 0.70,
        "semanticTarget": 0.70,
        "transitionTarget": 0.68,
        "projectTarget": 0.70,
        "maxRetakes": 2,
        "maxRetakesPerShot": 1,
        "planningAttempts": 1,
        "maxCycles": 8,
        "energyBudget": 70,
        "candidateMinGain": 0.03,
    },
}


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _integer(value: Any, default: int, low: int, high: int) -> int:
    try:
        selected = int(value)
    except (TypeError, ValueError):
        selected = default
    return max(low, min(high, selected))


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        selected = float(value)
    except (TypeError, ValueError):
        selected = default
    return max(low, min(high, selected))


def normalize_autonomous_config(value: Any) -> dict[str, Any]:
    raw = copy.deepcopy(value) if isinstance(value, dict) else {}
    profile = str(raw.get("profile") or "blockbuster").strip().casefold()
    if profile not in AUTONOMOUS_PROFILES:
        profile = "blockbuster"
    preset = AUTONOMOUS_PROFILES[profile]
    openai_ready = bool(os.getenv("OPENAI_API_KEY"))
    voice_provider = str(raw.get("voiceProvider") or "openai").strip().casefold()
    if voice_provider not in {"openai", "elevenlabs", "manual", "none"}:
        voice_provider = "openai"
    return {
        "schemaVersion": 1,
        "enabled": _bool(raw.get("enabled"), bool(raw)),
        "profile": profile,
        "profileLabel": preset["label"],
        "profileDescription": preset["description"],
        "projectId": str(raw.get("projectId") or "").strip()[:80],
        "approvedMemoryHash": str(raw.get("approvedMemoryHash") or "").strip()[:128],
        "enforceMemoryContract": _bool(raw.get("enforceMemoryContract"), True),
        "visualTarget": _number(raw.get("visualTarget"), preset["visualTarget"], 0.35, 0.98),
        "semanticTarget": _number(raw.get("semanticTarget"), preset["semanticTarget"], 0.35, 0.98),
        "transitionTarget": _number(raw.get("transitionTarget"), preset["transitionTarget"], 0.35, 0.98),
        "projectTarget": _number(raw.get("projectTarget"), preset["projectTarget"], 0.35, 0.98),
        "maxRetakes": _integer(raw.get("maxRetakes"), preset["maxRetakes"], 0, 40),
        "maxRetakesPerShot": _integer(
            raw.get("maxRetakesPerShot"), preset["maxRetakesPerShot"], 0, 6
        ),
        "planningAttempts": _integer(
            raw.get("planningAttempts"), preset["planningAttempts"], 1, 8
        ),
        "maxCycles": _integer(raw.get("maxCycles"), preset["maxCycles"], 1, 20),
        "energyBudget": _integer(
            raw.get("energyBudget"), preset["energyBudget"], 3, 500
        ),
        "candidateMinGain": _number(
            raw.get("candidateMinGain"), preset["candidateMinGain"], 0.0, 0.25
        ),
        "semanticReview": _bool(raw.get("semanticReview"), openai_ready) and openai_ready,
        "semanticModel": str(
            raw.get("semanticModel")
            or os.getenv("SILVER_SCREEN_SEMANTIC_MODEL")
            or "gpt-5-mini"
        ).strip()[:120],
        "semanticMaxCalls": _integer(raw.get("semanticMaxCalls"), 40, 0, 200),
        "voiceEnabled": _bool(raw.get("voiceEnabled"), openai_ready)
        and voice_provider != "none",
        "voiceProvider": voice_provider,
        "leadVoice": str(raw.get("leadVoice") or "coral").strip()[:120],
        "supportingVoice": str(raw.get("supportingVoice") or "onyx").strip()[:120],
        "narratorVoice": str(raw.get("narratorVoice") or "cedar").strip()[:120],
        "voiceInstructions": str(
            raw.get("voiceInstructions")
            or "Natural cinematic performance, restrained delivery, emotional continuity, clear diction, and timing that fits the picture."
        ).strip()[:1800],
        "preserveSourceAudio": _bool(raw.get("preserveSourceAudio"), True),
        "subtitles": _bool(raw.get("subtitles"), True),
        "deliveryMaster": _bool(raw.get("deliveryMaster"), True),
        "continuous": _bool(raw.get("continuous"), True),
        "autoApprovePreview": _bool(raw.get("autoApprovePreview"), True),
        "maxProviderCalls": _integer(raw.get("maxProviderCalls"), 0, 0, 5000),
        "maxSpendUsd": _number(raw.get("maxSpendUsd"), 0.0, 0.0, 1_000_000.0),
        "costPerSecondUsd": _number(
            raw.get("costPerSecondUsd"), 0.0, 0.0, 10_000.0
        ),
        "authorized": _bool(raw.get("authorized"), False),
        "oneClickAuthorizationHash": str(
            raw.get("oneClickAuthorizationHash") or ""
        ).strip()[:128],
    }


__all__ = ["AUTONOMOUS_PROFILES", "normalize_autonomous_config"]
