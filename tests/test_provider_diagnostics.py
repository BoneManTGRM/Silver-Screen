from silver_screen.provider_diagnostics import diagnose_provider_error, latest_video_error


def test_billing_error_is_non_retryable_and_actionable() -> None:
    diagnosis = diagnose_provider_error("Replicate returned HTTP 402: Payment required")
    assert diagnosis.code == "billing_required"
    assert diagnosis.retryable is False
    assert "billing" in diagnosis.title.lower()


def test_rate_limit_is_retryable() -> None:
    diagnosis = diagnose_provider_error("HTTP 429: Too many requests")
    assert diagnosis.code == "rate_limited"
    assert diagnosis.retryable is True


def test_latest_error_reads_blocked_shot() -> None:
    media = {
        "queue": {
            "shots": [
                {"order": 1, "lastError": "HTTP 402 billing required"},
                {"order": 2, "lastError": None},
            ]
        }
    }
    assert latest_video_error(media) == "HTTP 402 billing required"


def test_direct_media_error_wins() -> None:
    media = {"error": "direct failure", "queue": {"shots": [{"order": 1, "lastError": "old"}]}}
    assert latest_video_error(media) == "direct failure"
