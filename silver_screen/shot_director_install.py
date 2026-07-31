"""Install shot-specific prompting, negative prompts, and approved prompt ledgers."""

from __future__ import annotations

import contextvars
import copy
from typing import Any

from .shot_director import (
    ShotDirectorError,
    enforce_prompt_ledger,
    normalize_shot_direction,
    render_directed_prompt,
    render_negative_prompt,
    verify_ledger_hash,
)

_SHOT_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "silver_screen_shot_direction",
    default={},
)
_REQUEST_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "silver_screen_provider_shot_context",
    default={},
)


def _shot_direction_from_brief(brief: dict[str, Any]) -> dict[str, Any]:
    return normalize_shot_direction(
        brief.get("shotDirection") or brief.get("shot_direction")
    )


def install_shot_director() -> None:
    """Patch public pipeline extension points after continuity and creative controls."""

    from . import ai_video, pipeline, script_engine

    if getattr(pipeline, "_shot_director_installed", False):
        return

    original_validate = pipeline.validate_brief
    original_run_pipeline = pipeline.run_pipeline
    original_build = pipeline.build_film_from_brief
    original_request = ai_video.ReplicateVideoClient._request_json

    def validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
        normalized = original_validate(brief)
        direction = _shot_direction_from_brief(brief)
        normalized["shotDirection"] = direction
        _SHOT_CONTEXT.set(direction)
        return normalized

    def build_film_from_brief(
        *args: Any,
        shot_direction: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        direction = normalize_shot_direction(
            shot_direction
            if shot_direction is not None
            else _SHOT_CONTEXT.get()
        )
        state = original_build(*args, **kwargs)
        state["shotDirection"] = direction
        return state

    def scene_prompt(
        state: dict[str, Any],
        scene: dict[str, Any],
        shot: dict[str, Any] | None = None,
        repair: dict[str, Any] | None = None,
    ) -> str:
        direction = normalize_shot_direction(
            state.get("shotDirection") or _SHOT_CONTEXT.get()
        )
        state["shotDirection"] = direction
        runtime_shot = shot if isinstance(shot, dict) else {}
        current_prompt = render_directed_prompt(
            state, scene, runtime_shot, repair
        )
        current_negative = render_negative_prompt(state, runtime_shot)
        if runtime_shot:
            current_prompt, current_negative = enforce_prompt_ledger(
                state,
                scene,
                runtime_shot,
                repair,
                current_prompt,
                current_negative,
            )
            runtime_shot["promptLedgerVerified"] = bool(
                direction.get("enforcePromptLedger")
            )
            runtime_shot["negativePrompt"] = current_negative
        generate_audio = direction["audioStrategy"] != "silent"
        _REQUEST_CONTEXT.set(
            {
                "shotId": str(runtime_shot.get("id") or ""),
                "negativePrompt": current_negative,
                "generateAudio": generate_audio,
            }
        )
        return current_prompt

    def request_json(
        self: Any,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        prefer_wait: bool = False,
    ) -> dict[str, Any]:
        context = _REQUEST_CONTEXT.get()
        outgoing = payload
        is_prediction = (
            method.upper() == "POST"
            and "/predictions" in str(url)
            and isinstance(payload, dict)
            and isinstance(payload.get("input"), dict)
        )
        if is_prediction and context:
            outgoing = copy.deepcopy(payload)
            inputs = outgoing["input"]
            negative = str(context.get("negativePrompt") or "").strip()
            if negative:
                inputs["negative_prompt"] = negative
            planned_audio = context.get("generateAudio")
            # A TGRM repair may already have disabled audio. Never re-enable it.
            if inputs.get("generate_audio") is not False and planned_audio is not None:
                inputs["generate_audio"] = bool(planned_audio)
        try:
            return original_request(
                self,
                method,
                url,
                outgoing,
                prefer_wait=prefer_wait,
            )
        finally:
            if is_prediction:
                _REQUEST_CONTEXT.set({})

    def run_pipeline(
        brief: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        direction = _shot_direction_from_brief(brief)
        _SHOT_CONTEXT.set(direction)
        render_media = bool(kwargs.get("render_media", True))
        video_mode = str(kwargs.get("video_mode", "cards"))
        if (
            render_media
            and video_mode == "ai-video"
            and direction.get("enforcePromptLedger")
        ):
            ledger = direction.get("approvedPromptLedger") or {}
            if not verify_ledger_hash(ledger):
                raise pipeline.BriefValidationError(
                    "Paid production is blocked because the approved prompt ledger is missing or invalid"
                )
            if (
                direction.get("approvedLedgerHash")
                and direction["approvedLedgerHash"] != ledger.get("ledgerHash")
            ):
                raise pipeline.BriefValidationError(
                    "Paid production is blocked because the approved prompt-ledger hash changed"
                )
            planned = kwargs.get("video_max_shots")
            entries = [
                item
                for item in ledger.get("entries") or []
                if isinstance(item, dict)
            ]
            if planned is not None and len(entries) < max(1, int(planned)):
                raise pipeline.BriefValidationError(
                    "Paid production is blocked because the approved prompt ledger does not cover every planned clip"
                )
        return original_run_pipeline(brief, *args, **kwargs)

    pipeline.validate_brief = validate_brief
    pipeline.build_film_from_brief = build_film_from_brief
    pipeline.run_pipeline = run_pipeline
    pipeline._shot_director_installed = True

    script_engine.build_film_from_brief = build_film_from_brief
    ai_video.scene_prompt = scene_prompt
    ai_video.ReplicateVideoClient._request_json = request_json

    try:
        from . import media

        media.generate_ai_video = ai_video.generate_ai_video
    except Exception:
        pass


__all__ = ["install_shot_director", "ShotDirectorError"]
