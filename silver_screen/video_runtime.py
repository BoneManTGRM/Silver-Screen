"""Durable long-form AI video queue with Reparodynamics and TGRM metrics."""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runtime import atomic_write_json, utc_now

VIDEO_QUEUE_FILENAME = "video_queue.json"
VIDEO_SCARS_FILENAME = "video_scar_memory.json"
VIDEO_RUNTIME_FILENAME = "video_runtime.json"
VIDEO_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class VideoProductionConfig:
    """Bounded settings for a resumable video production."""

    target_runtime_seconds: int = 60
    clip_duration_seconds: int = 8
    max_shots: int = 128
    batch_size: int = 4
    max_retries_per_shot: int = 2
    max_provider_calls: int = 0
    max_spend_usd: float = 0.0
    cost_per_second_usd: float = 0.0
    use_continuity_frames: bool = True
    chapter_size: int = 12

    @property
    def requested_shots(self) -> int:
        return max(1, math.ceil(self.target_runtime_seconds / self.clip_duration_seconds))

    @property
    def planned_shots(self) -> int:
        return min(self.max_shots, self.requested_shots)

    @property
    def planned_runtime_seconds(self) -> int:
        return self.planned_shots * self.clip_duration_seconds

    @property
    def provider_call_budget(self) -> int:
        if self.max_provider_calls > 0:
            return self.max_provider_calls
        return self.planned_shots * (self.max_retries_per_shot + 1)


def normalize_video_config(
    *,
    target_runtime_seconds: int | None = None,
    clip_duration_seconds: int | None = None,
    max_shots: int | None = None,
    batch_size: int | None = None,
    max_retries_per_shot: int | None = None,
    max_provider_calls: int | None = None,
    max_spend_usd: float | None = None,
    cost_per_second_usd: float | None = None,
    use_continuity_frames: bool | None = None,
    chapter_size: int | None = None,
) -> VideoProductionConfig:
    """Normalize operator input into a safe, explicit production contract."""

    duration = int(
        clip_duration_seconds
        if clip_duration_seconds is not None
        else os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8")
    )
    allowed_durations = (4, 6, 8)
    duration = min(allowed_durations, key=lambda value: abs(value - duration))
    target = int(
        target_runtime_seconds
        if target_runtime_seconds is not None
        else os.getenv("SILVER_SCREEN_TARGET_RUNTIME_SECONDS", "60")
    )
    shots = int(
        max_shots
        if max_shots is not None
        else os.getenv("SILVER_SCREEN_VIDEO_MAX_SHOTS", "128")
    )
    batch = int(
        batch_size
        if batch_size is not None
        else os.getenv("SILVER_SCREEN_VIDEO_BATCH_SIZE", "4")
    )
    retries = int(
        max_retries_per_shot
        if max_retries_per_shot is not None
        else os.getenv("SILVER_SCREEN_VIDEO_MAX_RETRIES", "2")
    )
    calls = int(
        max_provider_calls
        if max_provider_calls is not None
        else os.getenv("SILVER_SCREEN_VIDEO_MAX_PROVIDER_CALLS", "0")
    )
    spend = float(
        max_spend_usd
        if max_spend_usd is not None
        else os.getenv("SILVER_SCREEN_VIDEO_MAX_SPEND_USD", "0")
    )
    rate = float(
        cost_per_second_usd
        if cost_per_second_usd is not None
        else os.getenv("SILVER_SCREEN_VIDEO_COST_PER_SECOND_USD", "0")
    )
    continuity = (
        bool(use_continuity_frames)
        if use_continuity_frames is not None
        else os.getenv("SILVER_SCREEN_VIDEO_CONTINUITY", "1") != "0"
    )
    chapter = int(
        chapter_size
        if chapter_size is not None
        else os.getenv("SILVER_SCREEN_VIDEO_CHAPTER_SIZE", "12")
    )
    return VideoProductionConfig(
        target_runtime_seconds=max(4, min(5400, target)),
        clip_duration_seconds=duration,
        max_shots=max(1, min(900, shots)),
        batch_size=max(0, min(128, batch)),
        max_retries_per_shot=max(0, min(8, retries)),
        max_provider_calls=max(0, min(10000, calls)),
        max_spend_usd=max(0.0, spend),
        cost_per_second_usd=max(0.0, rate),
        use_continuity_frames=continuity,
        chapter_size=max(1, min(100, chapter)),
    )


def state_fingerprint(state: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(state.get("id") or ""),
            str(state.get("title") or ""),
            str(state.get("premise") or ""),
            str(state.get("seed") or 0),
            str(len(state.get("scenes") or [])),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _production_id(state: dict[str, Any], config: VideoProductionConfig) -> str:
    digest = hashlib.sha256(
        f"{state_fingerprint(state)}|{config.clip_duration_seconds}".encode("utf-8")
    ).hexdigest()[:12]
    return f"video_{digest}"


def _source_scene(state: dict[str, Any], shot_index: int, shot_count: int) -> dict[str, Any]:
    scenes = [scene for scene in state.get("scenes") or [] if isinstance(scene, dict)]
    if not scenes:
        raise ValueError("Film state contains no scenes")
    scene_index = min(len(scenes) - 1, (shot_index * len(scenes)) // max(1, shot_count))
    return scenes[scene_index]


def _segment_number(shots: list[dict[str, Any]], scene_number: int) -> int:
    return 1 + sum(
        1
        for shot in shots
        if int((shot.get("sourceScene") or {}).get("number", -1)) == scene_number
    )


def _new_shot(
    state: dict[str, Any],
    config: VideoProductionConfig,
    shot_index: int,
    shot_count: int,
    existing: list[dict[str, Any]],
) -> dict[str, Any]:
    scene = _source_scene(state, shot_index, shot_count)
    scene_number = int(scene.get("number", shot_index + 1) or shot_index + 1)
    chapter = int(scene.get("chapter", 1) or 1)
    return {
        "id": f"shot_{shot_index + 1:04d}",
        "order": shot_index + 1,
        "status": "pending",
        "sourceScene": {
            "number": scene_number,
            "chapter": chapter,
            "act": int(scene.get("act", 1) or 1),
            "slugline": str(scene.get("slugline") or ""),
            "summary": str(scene.get("summary") or ""),
        },
        "segment": _segment_number(existing, scene_number),
        "plannedDurationSeconds": config.clip_duration_seconds,
        "verifiedDurationSeconds": 0.0,
        "seed": (int(state.get("seed") or 0) + shot_index + 1) & 0x7FFFFFFF,
        "attempts": 0,
        "providerPredictionId": None,
        "providerPredictionUrl": None,
        "providerStatus": None,
        "prompt": None,
        "promptRevision": 0,
        "path": None,
        "continuityFrame": None,
        "continuityUsed": False,
        "lastError": None,
        "repairHistory": [],
        "verification": {},
        "startedAt": None,
        "completedAt": None,
    }


def create_video_queue(
    state: dict[str, Any], config: VideoProductionConfig
) -> dict[str, Any]:
    shot_count = config.planned_shots
    shots: list[dict[str, Any]] = []
    for index in range(shot_count):
        shots.append(_new_shot(state, config, index, shot_count, shots))
    queue: dict[str, Any] = {
        "schemaVersion": VIDEO_SCHEMA_VERSION,
        "productionId": _production_id(state, config),
        "stateFingerprint": state_fingerprint(state),
        "status": "planned",
        "stopReason": None,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "completedAt": None,
        "config": asdict(config),
        "requestedRuntimeSeconds": config.target_runtime_seconds,
        "plannedRuntimeSeconds": config.planned_runtime_seconds,
        "shots": shots,
        "events": [],
        "scars": [],
        "metrics": {},
        "msil": {},
        "artifacts": {},
    }
    update_video_metrics(queue)
    return queue


def extend_video_queue(
    queue: dict[str, Any],
    state: dict[str, Any],
    config: VideoProductionConfig,
) -> dict[str, Any]:
    """Extend an existing production without deleting accepted footage."""

    if queue.get("stateFingerprint") != state_fingerprint(state):
        raise ValueError("Existing video queue belongs to a different film state")
    shots = [shot for shot in queue.get("shots") or [] if isinstance(shot, dict)]
    target_count = max(len(shots), config.planned_shots)
    for index in range(len(shots), target_count):
        shots.append(_new_shot(state, config, index, target_count, shots))
    queue["shots"] = shots
    queue["config"] = asdict(config)
    queue["requestedRuntimeSeconds"] = config.target_runtime_seconds
    queue["plannedRuntimeSeconds"] = len(shots) * config.clip_duration_seconds
    queue["status"] = "running" if any(
        shot.get("status") != "verified" for shot in shots
    ) else "complete"
    queue["updatedAt"] = utc_now()
    update_video_metrics(queue)
    return queue


def queue_paths(out_dir: str | os.PathLike[str]) -> dict[str, Path]:
    root = Path(out_dir)
    return {
        "queue": root / VIDEO_QUEUE_FILENAME,
        "scars": root / VIDEO_SCARS_FILENAME,
        "runtime": root / VIDEO_RUNTIME_FILENAME,
    }


def load_video_queue(out_dir: str | os.PathLike[str]) -> dict[str, Any] | None:
    path = queue_paths(out_dir)["queue"]
    if not path.exists():
        return None
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Video queue must be a JSON object")
    return payload


def save_video_queue(out_dir: str | os.PathLike[str], queue: dict[str, Any]) -> None:
    paths = queue_paths(out_dir)
    queue["updatedAt"] = utc_now()
    update_video_metrics(queue)
    atomic_write_json(paths["queue"], queue)
    atomic_write_json(paths["scars"], queue.get("scars") or [])
    atomic_write_json(
        paths["runtime"],
        {
            "schemaVersion": VIDEO_SCHEMA_VERSION,
            "productionId": queue.get("productionId"),
            "status": queue.get("status"),
            "stopReason": queue.get("stopReason"),
            "updatedAt": queue.get("updatedAt"),
            "completedAt": queue.get("completedAt"),
            "metrics": queue.get("metrics") or {},
            "msil": queue.get("msil") or {},
            "artifacts": queue.get("artifacts") or {},
        },
    )


def record_video_event(
    queue: dict[str, Any],
    event: str,
    *,
    shot_id: str | None = None,
    detail: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    events = queue.setdefault("events", [])
    events.append(
        {
            "at": utc_now(),
            "event": event,
            "shotId": shot_id,
            "detail": detail,
            "data": data or {},
        }
    )
    if len(events) > 2000:
        del events[:-2000]


def _retry_oscillation(shots: list[dict[str, Any]]) -> float:
    attempts = [int(shot.get("attempts", 0) or 0) for shot in shots]
    if not attempts:
        return 0.0
    return min(1.0, sum(max(0, value - 1) for value in attempts) / len(attempts))


def update_video_metrics(queue: dict[str, Any]) -> None:
    shots = [shot for shot in queue.get("shots") or [] if isinstance(shot, dict)]
    total = len(shots)
    verified = [shot for shot in shots if shot.get("status") == "verified"]
    failed = [
        shot
        for shot in shots
        if shot.get("status") in {"failed", "blocked", "corrupt"}
    ]
    provider_calls = sum(int(shot.get("attempts", 0) or 0) for shot in shots)
    repairs = sum(len(shot.get("repairHistory") or []) for shot in shots)
    verified_seconds = round(
        sum(float(shot.get("verifiedDurationSeconds", 0) or 0) for shot in verified),
        3,
    )
    if verified and verified_seconds <= 0:
        verified_seconds = round(
            sum(float(shot.get("plannedDurationSeconds", 0) or 0) for shot in verified),
            3,
        )
    completion = len(verified) / total if total else 0.0
    continuity_eligible = max(0, len(verified) - 1)
    continuity_used = sum(1 for shot in verified[1:] if shot.get("continuityUsed"))
    continuity = (
        continuity_used / continuity_eligible if continuity_eligible else (1.0 if verified else 0.0)
    )
    failure_rate = len(failed) / total if total else 0.0
    oscillation = _retry_oscillation(shots)
    energy = max(1, provider_calls * 5 + repairs * 2 + len(verified))
    rye = verified_seconds / energy
    config = queue.get("config") or {}
    cost_rate = float(config.get("cost_per_second_usd", 0) or 0)
    estimated_spend = provider_calls * float(
        config.get("clip_duration_seconds", 8) or 8
    ) * cost_rate
    stability = max(
        0.0,
        min(
            1.0,
            completion * 0.55
            + continuity * 0.2
            + (1.0 - failure_rate) * 0.15
            + (1.0 - oscillation) * 0.1,
        ),
    )
    if completion >= 1.0 and not failed:
        verdict = "stable"
    elif failed:
        verdict = "blocked"
    elif stability >= 0.55:
        verdict = "repairing"
    else:
        verdict = "unstable"
    queue["metrics"] = {
        "plannedShots": total,
        "verifiedShots": len(verified),
        "failedShots": len(failed),
        "providerCalls": provider_calls,
        "repairs": repairs,
        "verifiedSeconds": verified_seconds,
        "completionRatio": round(completion, 6),
        "continuityCoverage": round(continuity, 6),
        "estimatedSpendUsd": round(estimated_spend, 4),
        "energy": energy,
        "deltaR": verified_seconds,
        "rye": round(rye, 6),
    }
    queue["msil"] = {
        "stabilityIndex": round(stability, 6),
        "continuity": round(continuity, 6),
        "failureRate": round(failure_rate, 6),
        "repairOscillation": round(oscillation, 6),
        "collapseRisk": round(max(0.0, min(1.0, 1.0 - stability + failure_rate)), 6),
        "verdict": verdict,
    }


def detect_video_fractures(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect operational video fractures in priority order."""

    fractures: list[dict[str, Any]] = []
    shots = [shot for shot in queue.get("shots") or [] if isinstance(shot, dict)]
    for shot in shots:
        status = str(shot.get("status") or "pending")
        if status == "submitted" and not shot.get("providerPredictionId"):
            fractures.append(
                {
                    "class": "orphaned_prediction",
                    "severity": 0.95,
                    "shotId": shot.get("id"),
                    "description": "Shot is submitted without a persisted prediction ID.",
                }
            )
        if status == "verified" and not shot.get("path"):
            fractures.append(
                {
                    "class": "missing_artifact",
                    "severity": 0.95,
                    "shotId": shot.get("id"),
                    "description": "Verified shot has no persisted MP4 path.",
                }
            )
        if status in {"failed", "blocked", "corrupt"}:
            fractures.append(
                {
                    "class": "shot_failure",
                    "severity": 0.9,
                    "shotId": shot.get("id"),
                    "description": str(shot.get("lastError") or "Shot failed"),
                }
            )
    verified_orders = {
        int(shot.get("order", 0) or 0)
        for shot in shots
        if shot.get("status") == "verified"
    }
    for order in sorted(verified_orders):
        if order > 1 and order - 1 not in verified_orders:
            fractures.append(
                {
                    "class": "continuity_gap",
                    "severity": 0.75,
                    "shotId": f"shot_{order:04d}",
                    "description": "A later shot was accepted before its predecessor.",
                }
            )
    fractures.sort(key=lambda item: (-float(item["severity"]), str(item.get("shotId") or "")))
    return fractures


def choose_tgrm_repair(error: str, attempt: int) -> dict[str, Any]:
    """Choose the smallest bounded correction for a failed video shot."""

    lowered = (error or "").lower()
    if "download" in lowered or "output" in lowered:
        strategy = "redownload_or_regenerate"
        suffix = "Return a clean, complete video file with stable motion."
        seed_delta = 17
    elif "timeout" in lowered or "timed out" in lowered:
        strategy = "retry_with_simplified_motion"
        suffix = "Use one clear camera move and one clear subject action."
        seed_delta = 29
    elif "429" in lowered or "rate" in lowered:
        strategy = "backoff_and_retry"
        suffix = "Preserve the same scene and continuity state."
        seed_delta = 0
    elif "moderation" in lowered or "safety" in lowered or "policy" in lowered:
        strategy = "sanitize_prompt"
        suffix = "Keep the scene non-graphic, non-sexual, and suitable for a general audience."
        seed_delta = 43
    elif "audio" in lowered:
        strategy = "disable_audio"
        suffix = "Generate visually coherent silent footage; audio will be handled separately."
        seed_delta = 61
    elif "mp4" in lowered or "ffprobe" in lowered or "container" in lowered:
        strategy = "regenerate_verified_container"
        suffix = "Produce a standard MP4 with steady frame pacing and no corruption."
        seed_delta = 71
    else:
        strategy = "minimal_prompt_repair"
        suffix = "Reduce visual complexity while preserving character, setting, and narrative purpose."
        seed_delta = 89
    return {
        "strategy": strategy,
        "attempt": attempt,
        "energy": 2 if strategy in {"redownload_or_regenerate", "backoff_and_retry"} else 5,
        "seedDelta": seed_delta + max(0, attempt - 1) * 13,
        "promptSuffix": suffix,
        "disableAudio": strategy == "disable_audio",
        "at": utc_now(),
    }


def reinforce_video_scar(
    queue: dict[str, Any],
    *,
    shot: dict[str, Any],
    repair: dict[str, Any],
) -> None:
    scars = queue.setdefault("scars", [])
    key = f"{repair.get('strategy')}:{(shot.get('sourceScene') or {}).get('number')}"
    for scar in scars:
        if isinstance(scar, dict) and scar.get("key") == key:
            scar["uses"] = int(scar.get("uses", 1) or 1) + 1
            scar["lastUsedAt"] = utc_now()
            scar["successfulSeed"] = shot.get("seed")
            return
    scars.append(
        {
            "key": key,
            "strategy": repair.get("strategy"),
            "sourceScene": (shot.get("sourceScene") or {}).get("number"),
            "successfulSeed": shot.get("seed"),
            "promptSuffix": repair.get("promptSuffix"),
            "uses": 1,
            "createdAt": utc_now(),
            "lastUsedAt": utc_now(),
        }
    )


def budget_stop_reason(queue: dict[str, Any], config: VideoProductionConfig) -> str | None:
    metrics = queue.get("metrics") or {}
    if int(metrics.get("providerCalls", 0) or 0) >= config.provider_call_budget:
        return "provider_call_budget_exhausted"
    if (
        config.max_spend_usd > 0
        and config.cost_per_second_usd > 0
        and float(metrics.get("estimatedSpendUsd", 0) or 0)
        + config.clip_duration_seconds * config.cost_per_second_usd
        > config.max_spend_usd
    ):
        return "spend_budget_exhausted"
    return None
