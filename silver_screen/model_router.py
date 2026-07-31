"""Capability-aware, model-independent shot routing.

Routing is conservative: specialist models can be recommended immediately, but
Silver-Screen executes only models with an enabled adapter. This prevents a one-
click job from silently sending incompatible payloads to a model endpoint.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from .runtime import utc_now


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    label: str
    adapter: str
    execution_ready: bool
    tasks: tuple[str, ...]
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    quality: float
    speed: float
    cost_efficiency: float
    reference_images: bool = True
    native_audio: bool = False


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "google/veo-3.1-fast": ModelSpec(
        "google/veo-3.1-fast", "Google Veo 3.1 Fast", "veo", True,
        ("general", "performance", "action", "establishing", "animation", "dialogue"),
        ("strong general image-to-video", "continuity frames", "native audio option"),
        ("short clip duration", "probabilistic identity and action"),
        0.86, 0.86, 0.72, True, True,
    ),
    "bytedance/seedance-1.5-pro": ModelSpec(
        "bytedance/seedance-1.5-pro", "Seedance 1.5 Pro", "replicate_specialist", False,
        ("dialogue", "performance", "narrative", "general"),
        ("audiovisual narrative", "character performance", "synchronized sound"),
        ("adapter not enabled in this release",),
        0.91, 0.67, 0.55, True, True,
    ),
    "wan-video/wan-2.6-i2v": ModelSpec(
        "wan-video/wan-2.6-i2v", "Wan 2.6 Image-to-Video", "replicate_specialist", False,
        ("reference", "dialogue", "performance", "animation"),
        ("reference-driven video", "dialogue-capable workflow"),
        ("adapter not enabled in this release",),
        0.87, 0.70, 0.68, True, True,
    ),
    "sync/lipsync-2-pro": ModelSpec(
        "sync/lipsync-2-pro", "Sync Lipsync 2 Pro", "post_lipsync", False,
        ("lipsync",),
        ("specialized lip synchronization", "speaker-focused finishing"),
        ("post-process only", "requires a source video and speech track"),
        0.93, 0.60, 0.52, False, True,
    ),
}


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(0.0, min(1.0, result))


def normalize_routing_config(value: Any = None) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    q = _float(raw.get("qualityBias", 0.75), 0.75)
    c = _float(raw.get("costBias", 0.15), 0.15)
    s = _float(raw.get("speedBias", 0.10), 0.10)
    total = q + c + s or 1.0
    return {
        "schemaVersion": 1,
        "enabled": _bool(raw.get("enabled"), os.getenv("SILVER_SCREEN_AUTO_MODEL_ROUTING", "1") not in {"0", "false", "off"}),
        "executeSpecialists": _bool(raw.get("executeSpecialists"), os.getenv("SILVER_SCREEN_EXECUTE_SPECIALIST_MODELS", "0") not in {"0", "false", "off"}),
        "primaryModel": str(raw.get("primaryModel") or os.getenv("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3.1-fast")).strip(),
        "qualityBias": round(q / total, 6),
        "costBias": round(c / total, 6),
        "speedBias": round(s / total, 6),
        "qualityTier": str(raw.get("qualityTier") or "cinematic"),
        "allowNativeDialogue": _bool(raw.get("allowNativeDialogue"), False),
        "availableModels": [str(item).strip() for item in (raw.get("availableModels") or []) if str(item).strip()],
    }


def _blueprint(shot: dict[str, Any]) -> dict[str, Any]:
    value = shot.get("blueprint") or shot.get("shotBlueprint") or {}
    return value if isinstance(value, dict) else {}


def classify_shot(shot: dict[str, Any], scene: dict[str, Any] | None = None, state: dict[str, Any] | None = None) -> str:
    blueprint = _blueprint(shot)
    text = " ".join(str(value or "") for value in (
        blueprint.get("type"), blueprint.get("description"), blueprint.get("dialogue"),
        shot.get("prompt"), (scene or {}).get("action"), (scene or {}).get("summary")
    )).casefold()
    if blueprint.get("dialogue") or "speaks" in text or "dialogue" in text:
        return "dialogue"
    if any(word in text for word in ("fight", "chase", "runs", "explosion", "stunt", "fast action")):
        return "action"
    if any(word in text for word in ("close-up", "close up", "reaction", "performance", "emotion")):
        return "performance"
    if any(word in text for word in ("establishing", "wide shot", "location", "landscape")):
        return "establishing"
    medium = str(((state or {}).get("creativeDirection") or {}).get("medium") or "").casefold()
    if "animation" in medium or "illustrated" in medium:
        return "animation"
    if shot.get("continuityUsed"):
        return "reference"
    return "general"


def _history(memory: dict[str, Any] | None, model: str) -> float:
    record = ((memory or {}).get("modelMemory") or {}).get(model) or {}
    quality = float(record.get("averageQuality", 0) or 0)
    samples = int(record.get("qualitySamples", 0) or 0)
    return min(0.12, quality * min(1.0, samples / 8) * 0.12)


def _rank(spec: ModelSpec, task: str, cfg: dict[str, Any], *, reference: bool, native_audio: bool, memory: dict[str, Any] | None) -> float:
    task_fit = 1.0 if task in spec.tasks else 0.66 if "general" in spec.tasks else 0.35
    compatibility = 1.0
    if reference and not spec.reference_images:
        compatibility -= 0.45
    if native_audio and not spec.native_audio:
        compatibility -= 0.35
    score = (
        cfg["qualityBias"] * spec.quality
        + cfg["costBias"] * spec.cost_efficiency
        + cfg["speedBias"] * spec.speed
        + 0.24 * task_fit
        + 0.12 * compatibility
        + _history(memory, spec.model_id)
    )
    return round(score, 6)


def route_shot(shot: dict[str, Any], *, scene: dict[str, Any] | None = None, state: dict[str, Any] | None = None, config: dict[str, Any] | None = None, memory: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = normalize_routing_config(config)
    task = classify_shot(shot, scene, state)
    strategy = str(((state or {}).get("shotDirection") or {}).get("audioStrategy", "dub_later"))
    reference = bool(shot.get("continuityUsed") or int(shot.get("order", 0) or 0) == 1)
    native_audio = strategy == "native_dialogue" and cfg["allowNativeDialogue"]
    allowed = set(cfg.get("availableModels") or [])
    candidates = []
    for model_id, spec in MODEL_REGISTRY.items():
        if spec.adapter.startswith("post_"):
            continue
        if allowed and model_id not in allowed and model_id != cfg["primaryModel"]:
            continue
        candidates.append({
            "model": model_id, "label": spec.label,
            "score": _rank(spec, task, cfg, reference=reference, native_audio=native_audio, memory=memory),
            "executionReady": spec.execution_ready, "adapter": spec.adapter,
            "strengths": list(spec.strengths), "weaknesses": list(spec.weaknesses),
        })
    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    recommended = candidates[0]["model"] if candidates else cfg["primaryModel"]
    execution = cfg["primaryModel"]
    reason = "The configured primary model is used while specialist adapters remain advisory."
    if cfg["executeSpecialists"]:
        selected = MODEL_REGISTRY.get(recommended)
        if selected and selected.execution_ready:
            execution = recommended
            reason = "The recommended specialist has an execution-ready adapter."
    fallback = next((item["model"] for item in candidates if item["model"] != recommended), cfg["primaryModel"])
    return {
        "schemaVersion": 1, "routedAt": utc_now(), "shotId": str(shot.get("id") or ""),
        "order": int(shot.get("order", 0) or 0), "task": task,
        "recommendedModel": recommended, "executionModel": execution, "fallbackModel": fallback,
        "reason": f"Task={task}; reference={reference}; nativeAudio={native_audio}. {reason}",
        "candidates": candidates[:5], "qualityTier": cfg["qualityTier"], "modelIndependent": True,
    }


def route_queue(queue: dict[str, Any], *, state: dict[str, Any], config: dict[str, Any] | None = None, memory: dict[str, Any] | None = None) -> dict[str, Any]:
    scenes = {int(item.get("number", 0) or 0): item for item in state.get("scenes") or [] if isinstance(item, dict)}
    routes = []
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        scene = scenes.get(int((shot.get("sourceScene") or {}).get("number", 0) or 0))
        route = route_shot(shot, scene=scene, state=state, config=config, memory=memory)
        shot["modelRoute"] = route
        routes.append(route)
    return {
        "schemaVersion": 1, "createdAt": utc_now(), "shots": len(routes), "routes": routes,
        "executionModels": sorted({str(item.get("executionModel") or "") for item in routes}),
        "recommendedModels": sorted({str(item.get("recommendedModel") or "") for item in routes}),
        "specialistExecutionEnabled": normalize_routing_config(config)["executeSpecialists"],
    }


def lipsync_recommendation() -> dict[str, Any]:
    spec = MODEL_REGISTRY["sync/lipsync-2-pro"]
    return {"model": spec.model_id, "label": spec.label, "executionReady": spec.execution_ready, "strengths": list(spec.strengths), "weaknesses": list(spec.weaknesses)}


def model_catalog() -> list[dict[str, Any]]:
    return [asdict(item) for item in MODEL_REGISTRY.values()]


__all__ = ["MODEL_REGISTRY", "ModelSpec", "classify_shot", "lipsync_recommendation", "model_catalog", "normalize_routing_config", "route_queue", "route_shot"]
