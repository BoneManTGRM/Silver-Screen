from silver_screen.production_dashboard import dashboard_metrics, display_msil, production_view, queue_rows


def _media(*, status="partial", reason="batch_checkpoint", verified=1, planned=8, failed=0, repairs=0, error=None):
    shots = []
    for index in range(planned):
        shots.append(
            {
                "id": f"shot_{index + 1:04d}",
                "order": index + 1,
                "status": "verified" if index < verified else "pending",
                "attempts": 1 if index < verified else 0,
                "continuityUsed": index > 0 and index < verified,
                "verifiedDurationSeconds": 8 if index < verified else 0,
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
            "continuityCoverage": 1.0 if verified <= 1 else 1.0,
        },
        "config": {"clip_duration_seconds": 8},
        "queue": {"shots": shots},
    }


def test_provider_budget_after_success_is_checkpoint_not_blocked() -> None:
    media = _media(status="blocked", reason="provider_call_budget_exhausted")
    view = production_view(media)
    assert view.stage == "checkpoint"
    assert view.can_resume is True
    assert view.is_hard_block is False
    assert view.progress_percent == 12
    assert display_msil(media) == "CHECKPOINT"


def test_real_error_remains_attention_state() -> None:
    media = _media(status="blocked", reason="shot_retry_budget_exhausted", failed=1, error="provider rejected input")
    view = production_view(media)
    assert view.stage == "blocked"
    assert view.is_hard_block is True
    assert display_msil(media) == "ATTENTION"


def test_complete_production_is_stable() -> None:
    media = _media(status="complete", reason="target_runtime_reached", verified=8, planned=8)
    view = production_view(media)
    assert view.stage == "complete"
    assert view.progress_percent == 100
    assert display_msil(media) == "STABLE"


def test_dashboard_metrics_and_queue_rows() -> None:
    media = _media(verified=2, planned=8)
    metrics = dashboard_metrics(media)
    assert metrics["completionPercent"] == 25.0
    assert metrics["remainingClips"] == 6
    assert metrics["remainingSeconds"] == 48
    rows = queue_rows(media)
    assert rows[0]["State"] == "✓"
    assert rows[1]["Continuity"] == "Chained"
    assert rows[2]["State"] == "○"
