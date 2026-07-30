from silver_screen.production_dashboard import (
    dashboard_metrics,
    display_msil,
    production_view,
    queue_rows,
)


def _media(
    *,
    status="partial",
    reason="batch_checkpoint",
    verified=1,
    planned=8,
    failed=0,
    repairs=0,
    error=None,
    voice=None,
):
    shots = []
    for index in range(planned):
        shots.append(
            {
                "id": f"shot_{index + 1:04d}",
                "order": index + 1,
                "status": (
                    "verified" if index < verified else "pending"
                ),
                "attempts": 1 if index < verified else 0,
                "continuityUsed": index > 0 and index < verified,
                "verifiedDurationSeconds": (
                    8 if index < verified else 0
                ),
                "sourceScene": {"number": index + 1},
                "lastError": None,
            }
        )
    return {
        "status": status,
        "stopReason": reason,
        "error": error,
        "metrics": {
            "plannedShots": planned,
            "verifiedShots": verified,
            "failedShots": failed,
            "repairs": repairs,
            "verifiedSeconds": verified * 8,
            "providerCalls": verified + repairs,
            "continuityCoverage": 1.0 if verified else 0.0,
        },
        "config": {"clip_duration_seconds": 8},
        "queue": {"shots": shots},
        "voice": voice
        or {"enabled": False, "status": "disabled"},
    }


def _voice(
    *,
    status="partial",
    generated=1,
    planned=8,
    error=None,
):
    return {
        "enabled": True,
        "status": status,
        "error": error,
        "metrics": {
            "generatedLines": generated,
            "plannedLines": planned,
            "completionRatio": (
                generated / planned if planned else 1.0
            ),
            "providerCalls": generated,
            "repairs": 0,
        },
        "plan": {
            "lines": [
                {
                    "shotId": f"shot_{index + 1:04d}",
                    "status": (
                        "verified" if index < generated else "pending"
                    ),
                    "speaker": (
                        "Moonie Moo"
                        if index % 2 == 0
                        else "Biscuit"
                    ),
                    "lastError": None,
                }
                for index in range(planned)
            ]
        },
    }


def test_provider_budget_after_success_is_checkpoint_not_blocked() -> None:
    media = _media(
        status="blocked",
        reason="provider_call_budget_exhausted",
    )
    view = production_view(media)
    assert view.stage == "checkpoint"
    assert view.can_resume is True
    assert view.is_hard_block is False
    assert view.progress_percent == 12
    assert display_msil(media) == "CHECKPOINT"


def test_real_error_remains_attention_state() -> None:
    media = _media(
        status="blocked",
        reason="shot_retry_budget_exhausted",
        failed=1,
        error="provider rejected input",
    )
    view = production_view(media)
    assert view.stage == "blocked"
    assert view.is_hard_block is True
    assert display_msil(media) == "ATTENTION"


def test_complete_silent_production_is_stable() -> None:
    media = _media(
        status="complete",
        reason="target_runtime_reached",
        verified=8,
        planned=8,
    )
    view = production_view(media)
    assert view.stage == "complete"
    assert view.progress_percent == 100
    assert display_msil(media) == "STABLE"


def test_complete_video_with_incomplete_voices_is_voice_checkpoint() -> None:
    media = _media(
        status="partial",
        reason="voice_checkpoint",
        verified=8,
        planned=8,
        voice=_voice(
            status="partial",
            generated=3,
            planned=8,
        ),
    )
    view = production_view(media)
    assert view.stage == "voice_checkpoint"
    assert view.progress_percent == 93
    assert view.can_resume is True
    assert display_msil(media) == "CHECKPOINT"


def test_complete_video_and_voice_is_stable() -> None:
    media = _media(
        status="complete",
        reason="target_runtime_and_voice_reached",
        verified=8,
        planned=8,
        voice=_voice(
            status="complete",
            generated=8,
            planned=8,
        ),
    )
    view = production_view(media)
    assert view.stage == "complete"
    assert view.progress_percent == 100
    assert "voice layer" in view.detail
    assert display_msil(media) == "STABLE"


def test_voice_failure_is_attention_without_losing_video_progress() -> None:
    media = _media(
        status="blocked",
        reason="voice_production_blocked",
        verified=4,
        planned=8,
        voice=_voice(
            status="blocked",
            generated=2,
            planned=4,
            error="voice provider rejected the key",
        ),
    )
    view = production_view(media)
    assert view.stage == "voice_blocked"
    assert view.is_hard_block is True
    assert view.progress_percent == 45
    assert display_msil(media) == "ATTENTION"


def test_dashboard_metrics_and_queue_rows_include_voice_state() -> None:
    media = _media(
        verified=2,
        planned=8,
        voice=_voice(
            status="partial",
            generated=1,
            planned=2,
        ),
    )
    metrics = dashboard_metrics(media)
    assert metrics["completionPercent"] == 25.0
    assert metrics["remainingClips"] == 6
    assert metrics["remainingSeconds"] == 48
    assert metrics["voiceLines"] == 1
    rows = queue_rows(media)
    assert rows[0]["State"] == "✓"
    assert rows[0]["Voice"] == "Verified"
    assert rows[0]["Speaker"] == "Moonie Moo"
    assert rows[1]["Continuity"] == "Chained"
    assert rows[2]["State"] == "○"
