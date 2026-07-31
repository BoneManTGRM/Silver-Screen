"""Production resilience and consent-gated transition retakes.

This layer adds two operational safeguards without changing the public video API:

* bounded automatic backoff for Replicate HTTP 429 responses; and
* a Director Review workflow that can reopen only the incoming clip of a weak
  boundary, preserve the accepted original, generate one targeted retake, and
  automatically keep whichever candidate scores better.

No retake is submitted merely by reviewing a run. A caller must explicitly
schedule the retake and then resume the saved production.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, TypeVar

from .runtime import RunWorkspace, load_run, utc_now
from .transition_engine import (
    analyze,
    build_plan,
    load_plan,
    relative,
    save_plan,
    settings,
    shot_path,
    verified_shots,
)
from .video_runtime import (
    load_video_queue,
    record_video_event,
    save_video_queue,
    update_video_metrics,
)

T = TypeVar("T")
RATE_LIMIT_MARKERS = ("http 429", "rate limit", "rate-limited", "too many requests", "throttled")


class DirectorReviewError(RuntimeError):
    """Raised when a review or targeted retake cannot be completed safely."""


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


def is_rate_limit_error(error: object) -> bool:
    text = str(error or "").lower()
    return any(marker in text for marker in RATE_LIMIT_MARKERS)


def retry_after_seconds(error: object, default: int = 10) -> int:
    """Extract a bounded Retry-After value from provider text or JSON."""

    text = str(error or "")
    candidates: list[float] = []
    for pattern in (
        r'"retry_after"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r"retry[-_ ]after\s*[:=]?\s*~?([0-9]+(?:\.[0-9]+)?)",
        r"resets?\s+in\s+~?([0-9]+(?:\.[0-9]+)?)\s*s",
        r"wait\s+~?([0-9]+(?:\.[0-9]+)?)\s*(?:seconds?|s)\b",
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                candidates.append(float(match.group(1)))
            except (TypeError, ValueError):
                continue
    if not candidates:
        # Some provider bodies are embedded after a prefix. Parse any JSON object
        # opportunistically without requiring the whole exception to be JSON.
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
                value = payload.get("retry_after") if isinstance(payload, dict) else None
                if value is not None:
                    candidates.append(float(value))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
    selected = max(1.0, candidates[0] if candidates else float(default))
    return max(1, min(300, int(round(selected))))


def call_with_rate_limit_backoff(
    operation: Callable[[], T],
    *,
    max_retries: int | None = None,
    max_wait_seconds: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry only explicit provider throttles, never ambiguous POST failures."""

    retries = (
        _int_env("SILVER_SCREEN_PROVIDER_429_RETRIES", 3, 0, 8)
        if max_retries is None
        else max(0, min(8, int(max_retries)))
    )
    wait_cap = (
        _int_env("SILVER_SCREEN_PROVIDER_429_MAX_WAIT_SECONDS", 60, 1, 300)
        if max_wait_seconds is None
        else max(1, min(300, int(max_wait_seconds)))
    )
    for attempt in range(retries + 1):
        try:
            return operation()
        except Exception as exc:
            if not is_rate_limit_error(exc) or attempt >= retries:
                raise
            requested = retry_after_seconds(exc, default=min(wait_cap, 2 ** (attempt + 1)))
            sleep(float(min(wait_cap, requested)))
    raise RuntimeError("Rate-limit retry loop ended unexpectedly")


def transition_retake_threshold() -> float:
    return _float_env("SILVER_SCREEN_TRANSITION_RETAKE_THRESHOLD", 0.64, 0.35, 0.95)


def transition_retake_candidates(
    plan: dict[str, Any], *, threshold: float | None = None
) -> list[dict[str, Any]]:
    """Return weak boundaries ordered by urgency and story relationship."""

    target = transition_retake_threshold() if threshold is None else float(threshold)
    candidates: list[dict[str, Any]] = []
    for item in plan.get("transitions") or []:
        if not isinstance(item, dict):
            continue
        score = float(item.get("effectiveScore", 0) or 0)
        relation = str(item.get("relation") or "scene_change")
        active = str(item.get("retakeStatus") or "") in {"scheduled", "rendering"}
        same_take_floor = target + 0.08 if relation == "continuation" else target
        if score >= same_take_floor and str(item.get("rating") or "") != "attention":
            continue
        severity = max(0.0, same_take_floor - score)
        candidates.append(
            {
                **item,
                "score": score,
                "scorePercent": round(score * 100, 1),
                "recommended": not active,
                "active": active,
                "severity": round(severity, 6),
                "reason": (
                    "Same-scene motion or identity continuity is below the director threshold."
                    if relation == "continuation"
                    else "The scene boundary still reads as an abrupt visual change."
                ),
            }
        )
    candidates.sort(
        key=lambda item: (
            1 if item.get("active") else 0,
            -float(item.get("severity", 0) or 0),
            int(item.get("toOrder", 0) or 0),
        )
    )
    return candidates


def _transition_by_id(plan: dict[str, Any], transition_id: str) -> dict[str, Any]:
    for item in plan.get("transitions") or []:
        if isinstance(item, dict) and str(item.get("transitionId") or "") == transition_id:
            return item
    raise DirectorReviewError(f"Transition {transition_id!r} was not found")


def _shot_by_id(queue: dict[str, Any], shot_id: str) -> dict[str, Any]:
    for shot in queue.get("shots") or []:
        if isinstance(shot, dict) and str(shot.get("id") or "") == shot_id:
            return shot
    raise DirectorReviewError(f"Shot {shot_id!r} was not found")


def _previous_verified_shot(
    queue: dict[str, Any], shot: dict[str, Any]
) -> dict[str, Any] | None:
    order = int(shot.get("order", 0) or 0)
    return next(
        (
            item
            for item in queue.get("shots") or []
            if isinstance(item, dict)
            and int(item.get("order", 0) or 0) == order - 1
            and item.get("status") == "verified"
        ),
        None,
    )


def prepare_director_review(
    run_id: str,
    *,
    output_root: str = "runs",
    mode: str = "auto",
) -> dict[str, Any]:
    """Load or refresh a local transition review without a provider call."""

    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    root = workspace.media_dir.resolve()
    queue = load_video_queue(root)
    if queue is None or len(verified_shots(queue)) < 2:
        raise DirectorReviewError(
            "Director Review requires at least two verified source clips"
        )
    plan = build_plan(queue, root, settings(mode))
    save_video_queue(root, queue)
    result.setdefault("media", {})["queue"] = queue
    result["media"]["transitionPlan"] = plan
    return {
        "runId": run_id,
        "workspace": str(workspace.path),
        "result": result,
        "queue": queue,
        "plan": plan,
        "candidates": transition_retake_candidates(plan),
    }


def schedule_transition_retake(
    run_id: str,
    transition_id: str,
    *,
    output_root: str = "runs",
    reason: str | None = None,
) -> dict[str, Any]:
    """Preserve a verified clip and reopen only the weak incoming boundary."""

    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    root = workspace.media_dir.resolve()
    queue = load_video_queue(root)
    if queue is None:
        raise DirectorReviewError("The selected run has no durable video queue")
    plan = queue.get("transitionPlan") or load_plan(root) or build_plan(
        queue, root, settings("auto")
    )
    transition = _transition_by_id(plan, transition_id)
    shot = _shot_by_id(queue, str(transition.get("toShot") or ""))
    if shot.get("status") != "verified":
        raise DirectorReviewError("The incoming shot is not currently verified")
    if shot.get("transitionRetake"):
        raise DirectorReviewError("This transition already has an active retake")
    source = shot_path(root, shot)
    if source is None or not source.exists():
        raise DirectorReviewError("The accepted incoming clip is missing")

    history = shot.setdefault("transitionRetakeHistory", [])
    maximum = _int_env("SILVER_SCREEN_TRANSITION_MAX_RETAKES", 2, 1, 6)
    if len(history) >= maximum:
        raise DirectorReviewError(
            f"The {maximum}-retake safety limit has been reached for this clip"
        )

    retake_number = len(history) + 1
    archive_dir = root / "retakes" / str(shot.get("id") or "shot")
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = archive_dir / f"accepted_before_retake_{retake_number:02d}.mp4"
    shutil.copy2(source, archived)
    record = {
        "retakeNumber": retake_number,
        "transitionId": transition_id,
        "candidatePath": relative(root, archived),
        "sourcePath": relative(root, source),
        "verification": deepcopy(shot.get("verification") or {}),
        "verifiedDurationSeconds": float(
            shot.get("verifiedDurationSeconds")
            or shot.get("plannedDurationSeconds")
            or 0
        ),
        "previousScore": float(transition.get("effectiveScore", 0) or 0),
        "previousRating": str(transition.get("rating") or ""),
        "attemptsBeforeRetake": int(shot.get("attempts", 0) or 0),
        "scheduledAt": utc_now(),
        "status": "preserved",
    }
    history.append(record)

    directive = " ".join(
        part
        for part in [
            str(transition.get("promptDirective") or ""),
            "TARGETED DIRECTOR RETAKE: improve only the opening continuity of this clip.",
            "Match the previous final frame before introducing any new pose, composition, or camera move.",
            str(reason or transition.get("reason") or "").strip(),
        ]
        if part
    )[:2200]
    shot["transitionRetake"] = {
        "status": "scheduled",
        "transitionId": transition_id,
        "directive": directive,
        "previousScore": record["previousScore"],
        "retakeNumber": retake_number,
        "scheduledAt": utc_now(),
    }
    shot["status"] = "pending"
    shot["path"] = None
    shot["verifiedDurationSeconds"] = 0.0
    shot["verification"] = {}
    shot["providerPredictionId"] = None
    shot["providerPredictionUrl"] = None
    shot["providerStatus"] = None
    shot["completedAt"] = None
    shot["startedAt"] = None
    shot["lastError"] = (
        "Director transition retake requested for continuity mismatch at "
        f"{transition_id}"
    )

    attempts = int(shot.get("attempts", 0) or 0)
    config = queue.setdefault("config", {})
    current_retries = int(config.get("max_retries_per_shot", 0) or 0)
    required_retries = max(current_retries, attempts)
    config["max_retries_per_shot"] = required_retries

    update_video_metrics(queue)
    current_calls = int((queue.get("metrics") or {}).get("providerCalls", 0) or 0)
    configured_calls = int(config.get("max_provider_calls", 0) or 0)
    if configured_calls > 0:
        authorized_calls = max(current_calls + 1, configured_calls + 1)
    else:
        planned = max(1, int((queue.get("metrics") or {}).get("plannedShots", 1) or 1))
        authorized_calls = max(current_calls + 1, planned * (required_retries + 1) + 1)
    config["max_provider_calls"] = authorized_calls

    options = result.setdefault("options", {})
    options["videoMaxRetries"] = required_retries
    options["videoMaxProviderCalls"] = authorized_calls
    queue["status"] = "partial"
    queue["stopReason"] = "transition_retake_scheduled"
    queue["completedAt"] = None
    transition["retakeStatus"] = "scheduled"
    transition["retakeShot"] = shot.get("id")
    plan["status"] = "retake_scheduled"
    plan["updatedAt"] = utc_now()
    queue["transitionPlan"] = plan
    record_video_event(
        queue,
        "transition_retake_scheduled",
        shot_id=str(shot.get("id") or ""),
        detail=transition_id,
        data={
            "archivedCandidate": record["candidatePath"],
            "previousScore": record["previousScore"],
            "authorizedProviderCalls": authorized_calls,
        },
    )
    save_plan(queue, root)
    save_video_queue(root, queue)

    media = result.setdefault("media", {})
    media.update(
        {
            "status": "partial",
            "resumeRequired": True,
            "stopReason": "transition_retake_scheduled",
            "queue": queue,
            "metrics": queue.get("metrics") or {},
            "msil": queue.get("msil") or {},
            "transitionPlan": plan,
            "transitionMetrics": plan.get("metrics") or {},
            "note": (
                "A single incoming clip was reopened for a targeted transition retake. "
                "The previously accepted candidate remains preserved."
            ),
        }
    )
    result["status"] = "partial"
    result["videoMetrics"] = media["metrics"]
    result["videoMsil"] = media["msil"]
    progress = min(
        99,
        round(
            100
            * float((queue.get("metrics") or {}).get("completionRatio", 0) or 0)
        ),
    )
    workspace.checkpoint(
        status="partial",
        stage="transition_retake_scheduled",
        progress=progress,
        extra={
            "videoMetrics": media["metrics"],
            "videoMsil": media["msil"],
            "videoStopReason": "transition_retake_scheduled",
            "transitionRetake": {
                "transitionId": transition_id,
                "shotId": shot.get("id"),
                "retakeNumber": retake_number,
            },
        },
    )
    workspace.write_json("result.json", result)
    title = str((result.get("state") or {}).get("title") or run_id)
    bundle = workspace.build_bundle(title)
    result.setdefault("artifacts", {})["bundle"] = str(bundle)
    workspace.write_json("result.json", result)
    return {
        "runId": run_id,
        "transitionId": transition_id,
        "shotId": shot.get("id"),
        "archivedCandidatePath": str(archived),
        "requiredMaxRetries": required_retries,
        "authorizedProviderCalls": authorized_calls,
        "result": result,
    }


def reconcile_transition_retakes(
    queue: dict[str, Any],
    root: str | Path,
) -> list[dict[str, Any]]:
    """Compare preserved and new candidates, keeping the stronger boundary."""

    base = Path(root).resolve()
    cfg = settings("auto")
    minimum_gain = _float_env(
        "SILVER_SCREEN_TRANSITION_RETAKE_MIN_GAIN", 0.015, 0.0, 0.25
    )
    outcomes: list[dict[str, Any]] = []
    for shot in verified_shots(queue):
        active = shot.get("transitionRetake")
        history = shot.get("transitionRetakeHistory") or []
        if not isinstance(active, dict) or not history:
            continue
        if str(active.get("status") or "") not in {"scheduled", "rendering"}:
            continue
        previous = _previous_verified_shot(queue, shot)
        current_path = shot_path(base, shot)
        preserved_rel = str(history[-1].get("candidatePath") or "")
        preserved_path = (base / preserved_rel).resolve() if preserved_rel else None
        if previous is None or current_path is None or not current_path.exists():
            continue
        if preserved_path is None or not preserved_path.exists():
            continue

        current_analysis = analyze(previous, shot, base, cfg)
        preserved_shot = deepcopy(shot)
        preserved_shot["path"] = relative(base, preserved_path)
        preserved_shot["transitionRetake"] = None
        preserved_analysis = analyze(previous, preserved_shot, base, cfg)
        current_score = float(current_analysis.get("effectiveScore", 0) or 0)
        preserved_score = float(preserved_analysis.get("effectiveScore", 0) or 0)
        keep_new = current_score >= preserved_score + minimum_gain

        rejected_path: Path | None = None
        if keep_new:
            selected_path = current_path
            selected = "new_retake"
            history[-1]["newCandidatePath"] = relative(base, current_path)
        else:
            rejected_path = (
                base
                / "retakes"
                / str(shot.get("id") or "shot")
                / f"rejected_retake_{int(active.get('retakeNumber', 1) or 1):02d}.mp4"
            )
            rejected_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current_path, rejected_path)
            selected_path = preserved_path
            selected = "preserved_original"
            shot["path"] = relative(base, preserved_path)
            shot["verification"] = deepcopy(history[-1].get("verification") or {})
            shot["verifiedDurationSeconds"] = float(
                history[-1].get("verifiedDurationSeconds")
                or shot.get("plannedDurationSeconds")
                or 0
            )

        outcome = {
            "transitionId": active.get("transitionId"),
            "shotId": shot.get("id"),
            "selected": selected,
            "selectedPath": relative(base, selected_path),
            "newScore": round(current_score, 6),
            "preservedScore": round(preserved_score, 6),
            "minimumGain": minimum_gain,
            "rejectedPath": relative(base, rejected_path) if rejected_path else None,
            "evaluatedAt": utc_now(),
        }
        history[-1].update(
            {
                "status": "evaluated",
                "outcome": outcome,
                "completedAt": utc_now(),
            }
        )
        shot["transitionRetakeOutcome"] = outcome
        shot["transitionRetake"] = None
        shot["lastError"] = None
        record_video_event(
            queue,
            "transition_retake_evaluated",
            shot_id=str(shot.get("id") or ""),
            detail=selected,
            data=outcome,
        )
        outcomes.append(outcome)
    if outcomes:
        save_video_queue(base, queue)
    return outcomes


def install_production_resilience() -> None:
    """Patch rate-limit, prompt, and assembly extension points exactly once."""

    from . import ai_video

    if getattr(ai_video, "_production_resilience_installed", False):
        return

    original_request = ai_video.ReplicateVideoClient._request_json
    original_prompt = ai_video.scene_prompt
    original_assemble = ai_video.assemble_verified_production

    def resilient_request(
        self: Any,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        prefer_wait: bool = False,
    ) -> dict[str, Any]:
        return call_with_rate_limit_backoff(
            lambda: original_request(
                self,
                method,
                url,
                payload,
                prefer_wait=prefer_wait,
            )
        )

    def resilient_prompt(
        state: dict[str, Any],
        scene: dict[str, Any],
        shot: dict[str, Any] | None = None,
        repair: dict[str, Any] | None = None,
    ) -> str:
        base = original_prompt(state, scene, shot, repair)
        active = (shot or {}).get("transitionRetake") if isinstance(shot, dict) else None
        directive = (
            str(active.get("directive") or "").strip()
            if isinstance(active, dict)
            else ""
        )
        if not directive:
            return base
        extra = "DIRECTOR REVIEW RETAKE: " + directive
        return base[: max(0, 3500 - len(extra) - 1)] + " " + extra

    def resilient_assemble(
        queue: dict[str, Any],
        root: Path,
        *,
        complete: bool,
    ) -> dict[str, Any]:
        reconcile_transition_retakes(queue, root)
        return original_assemble(queue, root, complete=complete)

    ai_video.ReplicateVideoClient._request_json = resilient_request
    ai_video.scene_prompt = resilient_prompt
    ai_video.assemble_verified_production = resilient_assemble
    ai_video._production_resilience_installed = True


__all__ = [
    "DirectorReviewError",
    "call_with_rate_limit_backoff",
    "install_production_resilience",
    "is_rate_limit_error",
    "prepare_director_review",
    "reconcile_transition_retakes",
    "retry_after_seconds",
    "schedule_transition_retake",
    "transition_retake_candidates",
    "transition_retake_threshold",
]
