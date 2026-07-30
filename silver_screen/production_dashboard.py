"""Pure presentation semantics for long-running AI-film production."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProductionView:
    stage: str
    severity: str
    headline: str
    detail: str
    progress_percent: int
    can_resume: bool
    is_hard_block: bool


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _checkpoint_reason(reason: str) -> bool:
    lowered = reason.lower()
    return (
        reason in {"batch_checkpoint", "provider_call_budget", "provider_call_budget_exhausted"}
        or ("provider" in lowered and "budget" in lowered)
        or ("batch" in lowered and "checkpoint" in lowered)
    )


def production_view(media: dict[str, Any]) -> ProductionView:
    metrics = media.get("metrics") or {}
    planned = max(0, _int(metrics.get("plannedShots")))
    verified = max(0, _int(metrics.get("verifiedShots")))
    failed = max(0, _int(metrics.get("failedShots")))
    repairs = max(0, _int(metrics.get("repairs")))
    status = str(media.get("status") or "unknown")
    reason = str(media.get("stopReason") or "")
    error = str(media.get("error") or "").strip()
    ratio = verified / planned if planned else (1.0 if verified else 0.0)
    progress = max(0, min(100, round(ratio * 100)))

    if planned and verified >= planned:
        return ProductionView(
            "complete", "success", "Production complete",
            f"All {verified} planned clips are verified and assembled.", 100, False, False,
        )

    if verified > 0 and (_checkpoint_reason(reason) or status == "partial"):
        return ProductionView(
            "checkpoint", "info", "Checkpoint complete",
            f"{verified} of {planned} clips are verified. The next batch can continue from saved footage.",
            progress, True, False,
        )

    hard_block = bool(error) or failed > 0 or reason in {
        "shot_retry_budget_exhausted", "assembly_failed", "provider_access_denied",
        "billing_required", "invalid_token", "model_unavailable",
    }
    if status == "blocked" and not hard_block and verified > 0:
        return ProductionView(
            "checkpoint", "info", "Checkpoint complete",
            f"{verified} of {planned} clips are verified. The configured call limit was reached safely.",
            progress, True, False,
        )
    if hard_block or status == "blocked":
        detail = error or reason or "A production gate requires attention before the next clip can run."
        return ProductionView(
            "blocked", "warning", "Production needs attention", detail, progress, verified > 0, True,
        )
    if repairs > 0:
        return ProductionView(
            "repairing", "warning", "TGRM repair in progress",
            f"{verified} of {planned} clips are verified while targeted repairs are being applied.",
            progress, True, False,
        )
    if verified > 0:
        return ProductionView(
            "generating", "info", "Production in progress",
            f"{verified} of {planned} clips are verified.", progress, True, False,
        )
    return ProductionView(
        "planning", "info", "Production ready", "No clip has been verified yet.", 0, False, False,
    )


def display_msil(media: dict[str, Any]) -> str:
    view = production_view(media)
    if view.stage == "complete":
        return "STABLE"
    if view.stage == "checkpoint":
        return "CHECKPOINT"
    if view.stage == "repairing":
        return "REPAIRING"
    if view.stage == "blocked":
        return "ATTENTION"
    if view.stage == "generating":
        return "GENERATING"
    return "PLANNING"


def queue_rows(media: dict[str, Any]) -> list[dict[str, Any]]:
    queue = media.get("queue") or {}
    rows: list[dict[str, Any]] = []
    icons = {
        "verified": "✓",
        "submitted": "◉",
        "processing": "◉",
        "running": "◉",
        "pending": "○",
        "blocked": "!",
        "failed": "!",
        "corrupt": "!",
    }
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        scene = shot.get("sourceScene") or {}
        status = str(shot.get("status") or "pending")
        rows.append({
            "State": icons.get(status, "·"),
            "Clip": _int(shot.get("order")),
            "Scene": _int(scene.get("number")),
            "Status": status.replace("_", " ").title(),
            "Attempts": _int(shot.get("attempts")),
            "Continuity": "Chained" if shot.get("continuityUsed") else "Pending",
            "Runtime": _float(shot.get("verifiedDurationSeconds")),
            "Error": str(shot.get("lastError") or ""),
        })
    return rows


def dashboard_metrics(media: dict[str, Any]) -> dict[str, Any]:
    metrics = media.get("metrics") or {}
    planned = max(0, _int(metrics.get("plannedShots")))
    verified = max(0, _int(metrics.get("verifiedShots")))
    seconds = max(0.0, _float(metrics.get("verifiedSeconds")))
    config = media.get("config") or {}
    clip_seconds = max(1, _int(config.get("clip_duration_seconds")) or 8)
    remaining = max(0, planned - verified)
    continuity = max(0.0, min(1.0, _float(metrics.get("continuityCoverage"))))
    return {
        "planned": planned,
        "verified": verified,
        "verifiedSeconds": seconds,
        "remainingClips": remaining,
        "remainingSeconds": remaining * clip_seconds,
        "completionPercent": round((verified / planned) * 100, 1) if planned else 0.0,
        "continuityPercent": round(continuity * 100, 1),
        "estimatedSpendUsd": _float(metrics.get("estimatedSpendUsd")),
        "providerCalls": _int(metrics.get("providerCalls")),
        "repairs": _int(metrics.get("repairs")),
    }
