"""Translate provider failures into actionable operator guidance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDiagnosis:
    code: str
    title: str
    detail: str
    retryable: bool


def diagnose_provider_error(error: object) -> ProviderDiagnosis:
    text = str(error or "").strip()
    lower = text.lower()

    if not text:
        return ProviderDiagnosis(
            "unknown",
            "No provider detail was returned",
            "Open the video queue or runtime JSON and inspect the latest event.",
            False,
        )
    if "http 402" in lower or "payment required" in lower or "billing" in lower or "credit" in lower:
        return ProviderDiagnosis(
            "billing_required",
            "Replicate billing or credits are required",
            "The website may allow selected free playground runs while API access to Veo still requires billing or usable credits. Enable billing or choose a model your account can run through the API.",
            False,
        )
    if "http 401" in lower or "unauthorized" in lower or "invalid token" in lower:
        return ProviderDiagnosis(
            "invalid_token",
            "The Replicate token was rejected",
            "Create a new token, replace REPLICATE_API_TOKEN in Streamlit secrets, then reboot the app.",
            False,
        )
    if "http 403" in lower or "forbidden" in lower or "permission" in lower:
        return ProviderDiagnosis(
            "permission_denied",
            "This account cannot run the selected model through the API",
            "Confirm account access, billing, and the SILVER_SCREEN_VIDEO_MODEL value.",
            False,
        )
    if "http 404" in lower or "model not found" in lower:
        return ProviderDiagnosis(
            "model_not_found",
            "The configured model was not found",
            "Set SILVER_SCREEN_VIDEO_MODEL to google/veo-3.1-fast and reboot the app.",
            False,
        )
    if "http 422" in lower or "validation" in lower or "input" in lower and "invalid" in lower:
        return ProviderDiagnosis(
            "invalid_input",
            "Replicate rejected the model input",
            "Retry once without a reference image. If that works, upload a JPEG or PNG with a 16:9 or 9:16 composition.",
            False,
        )
    if "429" in lower or "rate limit" in lower or "too many requests" in lower:
        return ProviderDiagnosis(
            "rate_limited",
            "Replicate temporarily rate-limited the request",
            "Wait briefly and continue the saved production. Do not start a new production.",
            True,
        )
    if "timeout" in lower or "timed out" in lower or "could not reach" in lower or "temporar" in lower:
        return ProviderDiagnosis(
            "temporary_network",
            "The provider request was interrupted",
            "Continue the same saved production so Silver-Screen can recover the existing prediction ID.",
            True,
        )
    if "safety" in lower or "policy" in lower or "moderation" in lower:
        return ProviderDiagnosis(
            "safety_rejection",
            "The model rejected the prompt",
            "Revise the premise or scene to remove the rejected content, then start a new production.",
            False,
        )
    if "download" in lower or "output" in lower or "mp4" in lower or "ffprobe" in lower:
        return ProviderDiagnosis(
            "output_failure",
            "The prediction completed but no verified MP4 was retained",
            "Continue the same production promptly. Replicate output URLs expire, so do not create a separate run.",
            True,
        )
    return ProviderDiagnosis(
        "provider_failure",
        "Replicate did not produce a verified clip",
        text[:1200],
        False,
    )


def latest_video_error(media: dict) -> str | None:
    direct = str(media.get("error") or "").strip()
    if direct:
        return direct
    queue = media.get("queue") or {}
    shots = [item for item in queue.get("shots") or [] if isinstance(item, dict)]
    for shot in sorted(shots, key=lambda item: int(item.get("order", 0) or 0), reverse=True):
        value = str(shot.get("lastError") or "").strip()
        if value:
            return value
    for event in reversed(queue.get("events") or []):
        if isinstance(event, dict):
            value = str(event.get("detail") or "").strip()
            if value and ("error" in value.lower() or "http" in value.lower() or "failed" in value.lower()):
                return value
    return None
