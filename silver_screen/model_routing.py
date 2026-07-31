"""Cost-aware, model-independent shot routing recommendations.

Silver-Screen records a route for every shot even when all routes resolve to the
same configured provider model. Additional compatible models can be supplied by
environment variable without changing the approved story or shot contract.
"""

from __future__ import annotations

import os
from typing import Any


def _clean(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _env_model(name: str, fallback: str) -> str:
    return _clean(os.getenv(name) or fallback, 240)


def model_registry() -> dict[str, dict[str, Any]]:
    primary = _env_model("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3.1-fast")
    return {
        "general": {
            "model": primary,
            "fallback": primary,
            "strength": "balanced cinematic generation",
        },
        "performance": {
            "model": _env_model("SILVER_SCREEN_MODEL_PERFORMANCE", primary),
            "fallback": primary,
            "strength": "faces, acting, close-ups, and restrained performance",
        },
        "action": {
            "model": _env_model("SILVER_SCREEN_MODEL_ACTION", primary),
            "fallback": primary,
            "strength": "fast motion, physical action, and camera movement",
        },
        "environment": {
            "model": _env_model("SILVER_SCREEN_MODEL_ENVIRONMENT", primary),
            "fallback": primary,
            "strength": "establishing shots, production design, and geography",
        },
        "dialogue": {
            "model": _env_model("SILVER_SCREEN_MODEL_DIALOGUE", primary),
            "fallback": primary,
            "strength": "speaker performance and synchronized audiovisual delivery",
        },
        "animation": {
            "model": _env_model("SILVER_SCREEN_MODEL_ANIMATION", primary),
            "fallback": primary,
            "strength": "consistent premium character animation",
        },
        "repair": {
            "model": _env_model("SILVER_SCREEN_MODEL_REPAIR", primary),
            "fallback": primary,
            "strength": "localized correction or targeted regeneration",
        },
        "upscale": {
            "model": _env_model("SILVER_SCREEN_MODEL_UPSCALE", "local-ffmpeg-mastering"),
            "fallback": "local-ffmpeg-mastering",
            "strength": "restoration, delivery mastering, and upscale",
        },
        "lip_sync": {
            "model": _env_model("SILVER_SCREEN_MODEL_LIP_SYNC", "not-configured"),
            "fallback": "professional-dub-later",
            "strength": "phoneme and mouth-motion alignment",
        },
    }


def classify_shot(
    blueprint: dict[str, Any] | None,
    state: dict[str, Any] | None = None,
) -> str:
    shot = blueprint or {}
    film = state or {}
    medium = _clean((film.get("creativeDirection") or {}).get("medium"), 300).casefold()
    text = " ".join(
        [
            _clean(shot.get("type"), 100),
            _clean(shot.get("description"), 1200),
            _clean(shot.get("dialogue"), 800),
            _clean(shot.get("override"), 800),
        ]
    ).casefold()
    if "animation" in medium or "illustrated" in medium:
        return "animation"
    if shot.get("dialogue") or any(
        token in text for token in ("speaks", "dialogue", "conversation", "interview")
    ):
        return "dialogue"
    if any(
        token in text
        for token in (
            "close-up",
            "close up",
            "reaction",
            "performance",
            "expression",
            "portrait",
        )
    ):
        return "performance"
    if any(
        token in text
        for token in (
            "fight",
            "chase",
            "run",
            "explosion",
            "crash",
            "stunt",
            "whip pan",
            "fast motion",
            "action",
        )
    ):
        return "action"
    if any(
        token in text
        for token in (
            "establishing",
            "wide",
            "location",
            "exterior",
            "landscape",
            "city",
            "environment",
        )
    ):
        return "environment"
    return "general"


def route_shot(
    blueprint: dict[str, Any] | None,
    state: dict[str, Any] | None = None,
    *,
    critical: bool = False,
) -> dict[str, Any]:
    category = classify_shot(blueprint, state)
    registry = model_registry()
    selected = registry[category]
    description = _clean((blueprint or {}).get("description"), 500)
    reasons = [
        f"Classified as {category} coverage.",
        f"Selected route emphasizes {selected['strength']}.",
    ]
    if critical:
        reasons.append("The shot is story-critical and should receive candidate comparison.")
    if description:
        reasons.append(f"Objective: {description}")
    model = str(selected["model"])
    fallback = str(selected["fallback"])
    return {
        "category": category,
        "recommendedModel": model,
        "fallbackModel": fallback,
        "reason": " ".join(reasons),
        "critical": bool(critical),
        "configured": model not in {"", "not-configured"},
        "sameAsPrimary": model
        == _env_model("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3.1-fast"),
    }


def _critical_order(order: int, total: int, blueprint: dict[str, Any]) -> bool:
    text = " ".join(
        [
            _clean(blueprint.get("type"), 100),
            _clean(blueprint.get("description"), 800),
        ]
    ).casefold()
    return (
        order in {1, total}
        or any(
            token in text
            for token in (
                "close-up",
                "reaction",
                "reveal",
                "climax",
                "hero",
                "final",
                "turning point",
            )
        )
    )


def build_routing_plan(
    state: dict[str, Any],
    queue: dict[str, Any],
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entries_by_id = {
        str(item.get("shotId") or ""): item
        for item in (ledger or {}).get("entries") or []
        if isinstance(item, dict)
    }
    shots = [item for item in queue.get("shots") or [] if isinstance(item, dict)]
    total = len(shots)
    routes: list[dict[str, Any]] = []
    for shot in shots:
        shot_id = str(shot.get("id") or "")
        entry = entries_by_id.get(shot_id, {})
        blueprint = entry.get("blueprint") or shot.get("shotBlueprint") or {}
        order = int(shot.get("order", 0) or 0)
        route = route_shot(
            blueprint,
            state,
            critical=_critical_order(order, total, blueprint),
        )
        routes.append(
            {
                "shotId": shot_id,
                "order": order,
                "scene": int(((shot.get("sourceScene") or {}).get("number", 0)) or 0),
                "blueprint": blueprint,
                **route,
            }
        )
    categories: dict[str, int] = {}
    models: dict[str, int] = {}
    for route in routes:
        categories[route["category"]] = categories.get(route["category"], 0) + 1
        model = str(route["recommendedModel"])
        models[model] = models.get(model, 0) + 1
    return {
        "schemaVersion": 1,
        "routes": routes,
        "summary": {
            "shots": len(routes),
            "criticalShots": sum(bool(item.get("critical")) for item in routes),
            "categories": categories,
            "models": models,
            "multiModelConfigured": len(models) > 1,
        },
    }


__all__ = [
    "build_routing_plan",
    "classify_shot",
    "model_registry",
    "route_shot",
]
