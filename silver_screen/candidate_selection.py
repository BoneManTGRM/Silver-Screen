"""Preserve, compare, and select generated shot candidates.

A retake never destroys the current accepted clip. Silver-Screen archives the
accepted candidate, generates one targeted replacement, scores both versions,
and restores the previous version unless the new candidate produces a measured
quality improvement.
"""

from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path
from typing import Any

from .runtime import RunWorkspace, utc_now
from .video_runtime import (
    load_video_queue,
    record_video_event,
    save_video_queue,
    update_video_metrics,
)


class CandidateSelectionError(RuntimeError):
    """Raised when a candidate cannot be preserved or compared safely."""


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _shot_path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = shot.get("path")
    if not value:
        return None
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise CandidateSelectionError("Candidate path escaped the production workspace")
    return resolved


def _relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise CandidateSelectionError("Candidate path escaped the production workspace")
    return resolved.relative_to(root).as_posix()


def shot_quality_score(shot: dict[str, Any]) -> dict[str, Any]:
    visual = shot.get("visualQuality") or {}
    semantic = shot.get("semanticQuality") or {}
    transition = shot.get("transitionIn") or {}
    visual_score = _float(visual.get("score"), 0.70)
    semantic_score = _float(semantic.get("score"), 0.70)
    semantic_weight = 0.40 if semantic.get("evidenceQuality") == "provider" else 0.22
    visual_weight = 0.52
    transition_weight = max(0.0, 1.0 - visual_weight - semantic_weight)
    transition_score = _float(transition.get("effectiveScore"), 0.72)
    total = (
        visual_score * visual_weight
        + semantic_score * semantic_weight
        + transition_score * transition_weight
    )
    return {
        "score": round(total, 6),
        "scorePercent": round(total * 100, 1),
        "visualScore": round(visual_score, 6),
        "semanticScore": round(semantic_score, 6),
        "transitionScore": round(transition_score, 6),
        "semanticEvidence": semantic.get("evidenceQuality") or "none",
    }


def _snapshot(shot: dict[str, Any], candidate_path: str) -> dict[str, Any]:
    keys = (
        "path",
        "verification",
        "verifiedDurationSeconds",
        "visualQuality",
        "semanticQuality",
        "transitionIn",
        "continuityFrame",
        "completedAt",
        "providerPredictionId",
        "providerPredictionUrl",
        "providerStatus",
        "provider",
        "attemptSeed",
        "prompt",
        "promptRevision",
    )
    payload = {key: copy.deepcopy(shot.get(key)) for key in keys}
    payload.update(
        {
            "candidatePath": candidate_path,
            "quality": shot_quality_score(shot),
            "capturedAt": utc_now(),
        }
    )
    return payload


def schedule_candidate_retake(
    run_id: str,
    shot_id: str,
    *,
    directive: str,
    output_root: str = "runs",
    reason: str = "",
    source: str = "autonomous_director",
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    root = workspace.media_dir.resolve()
    queue = load_video_queue(root)
    if queue is None:
        raise CandidateSelectionError("The selected run has no durable video queue")
    shot = next(
        (
            item
            for item in queue.get("shots") or []
            if isinstance(item, dict) and str(item.get("id") or "") == shot_id
        ),
        None,
    )
    if shot is None or shot.get("status") != "verified":
        raise CandidateSelectionError("The selected shot is not currently verified")
    if isinstance(shot.get("candidateRetake"), dict):
        raise CandidateSelectionError("The selected shot already has an active candidate retake")
    current = _shot_path(root, shot)
    if current is None or not current.exists():
        raise CandidateSelectionError("The accepted candidate file is missing")
    history = shot.setdefault("candidateHistory", [])
    maximum = _int_env("SILVER_SCREEN_CANDIDATE_MAX_RETAKES_PER_SHOT", 3, 1, 8)
    if len(history) >= maximum:
        raise CandidateSelectionError(
            f"The {maximum}-retake candidate limit has been reached for this shot"
        )
    number = len(history) + 1
    directory = root / "candidates" / shot_id
    directory.mkdir(parents=True, exist_ok=True)
    archived = directory / f"accepted_before_candidate_{number:02d}.mp4"
    shutil.copy2(current, archived)
    record = {
        "candidateNumber": number,
        "source": str(source)[:120],
        "status": "preserved",
        "reason": str(reason)[:1600],
        "previous": _snapshot(shot, _relative(root, archived)),
        "scheduledAt": utc_now(),
    }
    history.append(record)
    shot["candidateRetake"] = {
        "status": "scheduled",
        "candidateNumber": number,
        "source": str(source)[:120],
        "directive": " ".join(
            part
            for part in [
                "TARGETED CANDIDATE RETAKE: preserve every accepted property and change only the failed dimensions.",
                str(directive).strip(),
                "Preserve identity, wardrobe, props, scene geography, screen direction, story beat, lens language, and duration unless the repair explicitly requires a minimal change.",
            ]
            if part
        )[:3000],
        "previousScore": record["previous"]["quality"]["score"],
        "scheduledAt": utc_now(),
    }
    shot["status"] = "pending"
    shot["path"] = None
    shot["verification"] = {}
    shot["verifiedDurationSeconds"] = 0.0
    shot["providerPredictionId"] = None
    shot["providerPredictionUrl"] = None
    shot["providerStatus"] = None
    shot["provider"] = {}
    shot["completedAt"] = None
    shot["startedAt"] = None
    shot["lastError"] = "Candidate retake scheduled"
    config = queue.setdefault("config", {})
    current_calls = int((queue.get("metrics") or {}).get("providerCalls", 0) or 0)
    configured_calls = int(config.get("max_provider_calls", 0) or 0)
    config["max_provider_calls"] = max(
        current_calls + 1,
        configured_calls + 1 if configured_calls > 0 else current_calls + 1,
    )
    attempts = int(shot.get("attempts", 0) or 0)
    config["max_retries_per_shot"] = max(
        int(config.get("max_retries_per_shot", 0) or 0), attempts
    )
    queue["status"] = "partial"
    queue["stopReason"] = "candidate_retake_scheduled"
    queue["completedAt"] = None
    update_video_metrics(queue)
    record_video_event(
        queue,
        "candidate_retake_scheduled",
        shot_id=shot_id,
        detail=str(reason)[:1200],
        data={
            "candidateNumber": number,
            "previousScore": record["previous"]["quality"]["score"],
            "source": source,
        },
    )
    save_video_queue(root, queue)
    return {
        "runId": run_id,
        "shotId": shot_id,
        "queue": queue,
        "shot": shot,
        "candidateNumber": number,
        "archivedCandidate": str(archived),
    }


def _restore_snapshot(
    root: Path,
    shot: dict[str, Any],
    snapshot: dict[str, Any],
) -> Path:
    archived_value = snapshot.get("candidatePath")
    if not archived_value:
        raise CandidateSelectionError("Preserved candidate has no archive path")
    archived = (root / str(archived_value)).resolve()
    if root not in archived.parents or not archived.exists():
        raise CandidateSelectionError("Preserved candidate archive is missing")
    original_value = snapshot.get("path")
    if not original_value:
        original = root / "clips" / f"{shot.get('id')}.mp4"
    else:
        candidate = Path(str(original_value))
        original = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if root not in original.parents:
        raise CandidateSelectionError("Restored candidate path escaped the workspace")
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archived, original)
    for key, value in snapshot.items():
        if key in {"candidatePath", "quality", "capturedAt"}:
            continue
        shot[key] = copy.deepcopy(value)
    shot["path"] = _relative(root, original)
    shot["status"] = "verified"
    shot["lastError"] = None
    return original


def resolve_candidate_retake(
    run_id: str,
    shot_id: str,
    *,
    output_root: str = "runs",
    minimum_gain: float | None = None,
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    root = workspace.media_dir.resolve()
    queue = load_video_queue(root)
    if queue is None:
        raise CandidateSelectionError("The selected run has no durable video queue")
    shot = next(
        (
            item
            for item in queue.get("shots") or []
            if isinstance(item, dict) and str(item.get("id") or "") == shot_id
        ),
        None,
    )
    if shot is None or shot.get("status") != "verified":
        raise CandidateSelectionError("The replacement candidate is not verified")
    history = shot.get("candidateHistory") or []
    record = next(
        (
            item
            for item in reversed(history)
            if isinstance(item, dict) and item.get("status") == "preserved"
        ),
        None,
    )
    if record is None:
        raise CandidateSelectionError("No preserved candidate is waiting for comparison")
    gain_required = (
        _float_env("SILVER_SCREEN_CANDIDATE_MIN_GAIN", 0.015, 0.0, 0.25)
        if minimum_gain is None
        else max(0.0, min(0.25, float(minimum_gain)))
    )
    current_score = shot_quality_score(shot)
    previous = record.get("previous") or {}
    previous_score = previous.get("quality") or {}
    gain = current_score["score"] - _float(previous_score.get("score"), 0.0)
    new_path = _shot_path(root, shot)
    if new_path is None or not new_path.exists():
        raise CandidateSelectionError("The replacement candidate file is missing")
    directory = root / "candidates" / shot_id
    directory.mkdir(parents=True, exist_ok=True)
    number = int(record.get("candidateNumber", len(history)) or len(history))
    selected_new = gain >= gain_required
    if selected_new:
        accepted_copy = directory / f"accepted_candidate_{number:02d}.mp4"
        shutil.copy2(new_path, accepted_copy)
        record.update(
            {
                "status": "selected_new",
                "newCandidatePath": _relative(root, accepted_copy),
                "newQuality": current_score,
                "gain": round(gain, 6),
                "resolvedAt": utc_now(),
            }
        )
        selection = "new_candidate"
    else:
        rejected_copy = directory / f"rejected_candidate_{number:02d}.mp4"
        shutil.copy2(new_path, rejected_copy)
        restored = _restore_snapshot(root, shot, previous)
        record.update(
            {
                "status": "preserved_original",
                "newCandidatePath": _relative(root, rejected_copy),
                "newQuality": current_score,
                "gain": round(gain, 6),
                "restoredPath": _relative(root, restored),
                "resolvedAt": utc_now(),
            }
        )
        selection = "preserved_original"
    shot.pop("candidateRetake", None)
    shot["candidateSelection"] = {
        "selection": selection,
        "gain": round(gain, 6),
        "requiredGain": gain_required,
        "previousQuality": previous_score,
        "newQuality": current_score,
        "resolvedAt": utc_now(),
    }
    queue["status"] = (
        "complete"
        if all(
            not isinstance(item, dict) or item.get("status") == "verified"
            for item in queue.get("shots") or []
        )
        else "partial"
    )
    queue["stopReason"] = (
        "target_runtime_reached"
        if queue["status"] == "complete"
        else "candidate_comparison_complete"
    )
    update_video_metrics(queue)
    record_video_event(
        queue,
        "candidate_comparison_complete",
        shot_id=shot_id,
        detail=selection,
        data={
            "gain": round(gain, 6),
            "requiredGain": gain_required,
            "previousScore": previous_score.get("score"),
            "newScore": current_score.get("score"),
        },
    )
    save_video_queue(root, queue)
    return {
        "runId": run_id,
        "shotId": shot_id,
        "selection": selection,
        "gain": round(gain, 6),
        "requiredGain": gain_required,
        "previousQuality": previous_score,
        "newQuality": current_score,
        "queue": queue,
        "shot": shot,
    }


__all__ = [
    "CandidateSelectionError",
    "resolve_candidate_retake",
    "schedule_candidate_retake",
    "shot_quality_score",
]
