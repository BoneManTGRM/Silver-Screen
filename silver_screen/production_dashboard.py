"""Pure presentation semantics for video and voice production."""

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
        reason
        in {
            "batch_checkpoint",
            "provider_call_budget",
            "provider_call_budget_exhausted",
            "voice_checkpoint",
        }
        or ("provider" in lowered and "budget" in lowered)
        or ("batch" in lowered and "checkpoint" in lowered)
        or ("voice" in lowered and "checkpoint" in lowered)
    )


def _voice_state(
    media: dict[str, Any],
) -> tuple[bool, str, dict[str, Any], str]:
    voice = media.get("voice") or {}
    enabled = bool(voice.get("enabled"))
    status = str(
        voice.get("status")
        or ("disabled" if not enabled else "planned")
    )
    metrics = voice.get("metrics") or {}
    error = str(voice.get("error") or "").strip()
    return enabled, status, metrics, error


def _progress(
    *,
    planned: int,
    verified: int,
    voice_enabled: bool,
    voice_status: str,
    voice_metrics: dict[str, Any],
) -> int:
    video_ratio = (
        verified / planned
        if planned
        else (1.0 if verified else 0.0)
    )
    if not voice_enabled:
        return max(0, min(100, round(video_ratio * 100)))
    if video_ratio < 1.0:
        return max(0, min(90, round(video_ratio * 90)))
    if voice_status == "complete":
        return 100
    voice_ratio = max(
        0.0,
        min(
            1.0,
            _float(voice_metrics.get("completionRatio")),
        ),
    )
    return max(90, min(99, 90 + round(voice_ratio * 9)))


def production_view(media: dict[str, Any]) -> ProductionView:
    metrics = media.get("metrics") or {}
    planned = max(0, _int(metrics.get("plannedShots")))
    verified = max(0, _int(metrics.get("verifiedShots")))
    failed = max(0, _int(metrics.get("failedShots")))
    repairs = max(0, _int(metrics.get("repairs")))
    status = str(media.get("status") or "unknown")
    reason = str(media.get("stopReason") or "")
    error = str(media.get("error") or "").strip()
    (
        voice_enabled,
        voice_status,
        voice_metrics,
        voice_error,
    ) = _voice_state(media)
    progress = _progress(
        planned=planned,
        verified=verified,
        voice_enabled=voice_enabled,
        voice_status=voice_status,
        voice_metrics=voice_metrics,
    )

    voice_blocked = voice_enabled and (
        voice_status == "blocked"
        or bool(voice_error)
        or reason == "voice_production_blocked"
    )
    if voice_blocked:
        return ProductionView(
            "voice_blocked",
            "warning",
            "Voice Studio needs attention",
            voice_error
            or (
                "Verified silent footage remains saved while the affected "
                "voice line is repaired."
            ),
            progress,
            verified > 0,
            True,
        )

    video_complete = bool(planned and verified >= planned)
    if video_complete:
        if voice_enabled and voice_status not in {
            "complete",
            "disabled",
        }:
            generated = _int(voice_metrics.get("generatedLines"))
            total = _int(voice_metrics.get("plannedLines"))
            return ProductionView(
                "voice_checkpoint",
                "info",
                "Video complete, voices checkpointed",
                (
                    f"All {verified} video clips are verified. "
                    f"Voice Studio has completed {generated} of "
                    f"{total} planned line(s)."
                ),
                progress,
                True,
                False,
            )
        return ProductionView(
            "complete",
            "success",
            "Production complete",
            (
                f"All {verified} planned clips are verified and "
                + (
                    "the authorized voice layer is assembled."
                    if voice_enabled
                    else "assembled."
                )
            ),
            100,
            False,
            False,
        )

    if verified > 0 and (
        _checkpoint_reason(reason) or status == "partial"
    ):
        voice_suffix = ""
        if voice_enabled:
            generated = _int(voice_metrics.get("generatedLines"))
            voice_suffix = (
                f" {generated} voice line(s) are also verified."
            )
        return ProductionView(
            "checkpoint",
            "info",
            "Checkpoint complete",
            (
                f"{verified} of {planned} clips are verified. "
                "The next batch can continue from saved footage."
                f"{voice_suffix}"
            ),
            progress,
            True,
            False,
        )

    hard_block = bool(error) or failed > 0 or reason in {
        "shot_retry_budget_exhausted",
        "assembly_failed",
        "provider_access_denied",
        "billing_required",
        "invalid_token",
        "model_unavailable",
    }
    if status == "blocked" and not hard_block and verified > 0:
        return ProductionView(
            "checkpoint",
            "info",
            "Checkpoint complete",
            (
                f"{verified} of {planned} clips are verified. "
                "The configured call limit was reached safely."
            ),
            progress,
            True,
            False,
        )
    if hard_block or status == "blocked":
        detail = (
            error
            or reason
            or (
                "A production gate requires attention before the next "
                "clip can run."
            )
        )
        return ProductionView(
            "blocked",
            "warning",
            "Production needs attention",
            detail,
            progress,
            verified > 0,
            True,
        )
    if repairs > 0:
        return ProductionView(
            "repairing",
            "warning",
            "TGRM repair in progress",
            (
                f"{verified} of {planned} clips are verified while "
                "targeted repairs are being applied."
            ),
            progress,
            True,
            False,
        )
    if verified > 0:
        return ProductionView(
            "generating",
            "info",
            "Production in progress",
            f"{verified} of {planned} clips are verified.",
            progress,
            True,
            False,
        )
    return ProductionView(
        "planning",
        "info",
        "Production ready",
        "No clip has been verified yet.",
        0,
        False,
        False,
    )


def display_msil(media: dict[str, Any]) -> str:
    view = production_view(media)
    if view.stage == "complete":
        return "STABLE"
    if view.stage in {"checkpoint", "voice_checkpoint"}:
        return "CHECKPOINT"
    if view.stage == "repairing":
        return "REPAIRING"
    if view.stage in {"blocked", "voice_blocked"}:
        return "ATTENTION"
    if view.stage == "generating":
        return "GENERATING"
    return "PLANNING"


def queue_rows(media: dict[str, Any]) -> list[dict[str, Any]]:
    queue = media.get("queue") or {}
    voice_plan = ((media.get("voice") or {}).get("plan") or {})
    voice_by_shot = {
        str(line.get("shotId") or ""): line
        for line in voice_plan.get("lines") or []
        if isinstance(line, dict)
    }
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
        voice_line = voice_by_shot.get(
            str(shot.get("id") or ""),
            {},
        )
        rows.append(
            {
                "State": icons.get(status, "·"),
                "Clip": _int(shot.get("order")),
                "Scene": _int(scene.get("number")),
                "Video": status.replace("_", " ").title(),
                "Voice": str(
                    voice_line.get("status") or "Not planned"
                )
                .replace("_", " ")
                .title(),
                "Speaker": str(voice_line.get("speaker") or ""),
                "Attempts": _int(shot.get("attempts")),
                "Continuity": (
                    "Chained"
                    if shot.get("continuityUsed")
                    else "Pending"
                ),
                "Runtime": _float(
                    shot.get("verifiedDurationSeconds")
                ),
                "Error": str(
                    voice_line.get("lastError")
                    or shot.get("lastError")
                    or ""
                ),
            }
        )
    return rows


def dashboard_metrics(media: dict[str, Any]) -> dict[str, Any]:
    metrics = media.get("metrics") or {}
    planned = max(0, _int(metrics.get("plannedShots")))
    verified = max(0, _int(metrics.get("verifiedShots")))
    seconds = max(0.0, _float(metrics.get("verifiedSeconds")))
    config = media.get("config") or {}
    clip_seconds = max(
        1,
        _int(config.get("clip_duration_seconds")) or 8,
    )
    remaining = max(0, planned - verified)
    continuity = max(
        0.0,
        min(
            1.0,
            _float(metrics.get("continuityCoverage")),
        ),
    )
    voice = media.get("voice") or {}
    voice_metrics = voice.get("metrics") or {}
    return {
        "planned": planned,
        "verified": verified,
        "verifiedSeconds": seconds,
        "remainingClips": remaining,
        "remainingSeconds": remaining * clip_seconds,
        "completionPercent": (
            round((verified / planned) * 100, 1)
            if planned
            else 0.0
        ),
        "continuityPercent": round(continuity * 100, 1),
        "estimatedSpendUsd": _float(
            metrics.get("estimatedSpendUsd")
        ),
        "providerCalls": _int(metrics.get("providerCalls")),
        "repairs": _int(metrics.get("repairs")),
        "voiceEnabled": bool(voice.get("enabled")),
        "voiceStatus": str(voice.get("status") or "disabled"),
        "voiceLines": _int(
            voice_metrics.get("generatedLines")
        ),
        "voicePlannedLines": _int(
            voice_metrics.get("plannedLines")
        ),
        "voiceProviderCalls": _int(
            voice_metrics.get("providerCalls")
        ),
        "voiceRepairs": _int(voice_metrics.get("repairs")),
    }
