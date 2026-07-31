"""Install creative-control extensions without breaking the public pipeline API."""

from __future__ import annotations

import contextvars
from typing import Any

from .creative_direction import (
    approval_gate_errors,
    apply_creative_direction,
    finalize_creative_state,
    normalize_creative_direction,
    prompt_contract,
)

_CREATIVE_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "silver_screen_creative_context",
    default={},
)


def _authored_script(raw: Any) -> str | None:
    text = str(raw or "").replace("\x00", " ").strip()
    if not text:
        return None
    if len(text) > 120_000:
        raise ValueError("authoredScript must not exceed 120,000 characters")
    return text


def _direction_from_brief(brief: dict[str, Any]) -> dict[str, Any]:
    raw = brief.get("creativeDirection") or brief.get("creative_direction")
    direction = normalize_creative_direction(raw)
    if not isinstance(raw, dict):
        direction["medium"] = (
            "cinematic image-to-video that preserves the authorized reference medium; "
            "photorealistic for photographic references and premium animation for illustrated references"
        )
    return direction


def _mature_logline(state: dict[str, Any]) -> None:
    if state.get("scriptSource") == "authored":
        return
    characters = [item for item in state.get("characters") or [] if isinstance(item, dict)]
    lead = str(characters[0].get("name") or "The lead") if characters else "The lead"
    other = str(characters[1].get("name") or "the only useful witness") if len(characters) > 1 else "the only useful witness"
    premise = str(state.get("premise") or "").strip().rstrip(".")
    if premise:
        premise = premise[0].lower() + premise[1:]
    state["logline"] = (
        f"{lead} realizes that {premise}. The only useful lead points to {other}, "
        "whose silence may be protection, leverage, or control."
    )


def install_creative_controls() -> None:
    """Patch extension points once after core package imports complete."""

    from . import ai_video, pipeline, script_engine, tgrm

    if getattr(pipeline, "_creative_controls_installed", False):
        return

    original_validate = pipeline.validate_brief
    original_run_pipeline = pipeline.run_pipeline
    original_build = script_engine.build_film_from_brief
    original_run_tgrm = tgrm.run_tgrm
    original_prompt = ai_video.scene_prompt

    def validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
        normalized = original_validate(brief)
        direction = _direction_from_brief(brief)
        authored = _authored_script(
            brief.get("authoredScript")
            or brief.get("authored_script")
        )
        if direction.get("scriptSource") == "authored" and not authored:
            raise pipeline.BriefValidationError(
                "Authored-script mode requires an exact script before preview or rendering"
            )
        normalized["creativeDirection"] = direction
        normalized["authoredScript"] = authored
        _CREATIVE_CONTEXT.set(
            {
                "creativeDirection": direction,
                "authoredScript": authored,
            }
        )
        return normalized

    def build_film_from_brief(
        *args: Any,
        creative_direction: dict[str, Any] | None = None,
        authored_script: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        context = _CREATIVE_CONTEXT.get() or {}
        direction = normalize_creative_direction(
            creative_direction
            if creative_direction is not None
            else context.get("creativeDirection")
        )
        authored = (
            _authored_script(authored_script)
            if authored_script is not None
            else _authored_script(context.get("authoredScript"))
        )
        state = original_build(*args, **kwargs)
        state = apply_creative_direction(
            state,
            direction,
            authored_script=authored,
            render_screenplay_fn=script_engine.render_screenplay,
        )
        _mature_logline(state)
        return finalize_creative_state(
            state,
            render_screenplay_fn=script_engine.render_screenplay,
        )

    def run_tgrm(
        state: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = original_run_tgrm(state, *args, **kwargs)
        result["state"] = finalize_creative_state(
            result.get("state") or state,
            render_screenplay_fn=script_engine.render_screenplay,
        )
        return result

    def scene_prompt(
        state: dict[str, Any],
        scene: dict[str, Any],
        shot: dict[str, Any] | None = None,
        repair: dict[str, Any] | None = None,
    ) -> str:
        base = original_prompt(state, scene, shot, repair)
        direction = normalize_creative_direction(state.get("creativeDirection"))
        medium = str(direction.get("medium") or "").casefold()
        if "animation" in medium or "illustrated" in medium:
            base = base.replace(
                "Cinematic live-action film footage",
                "High-end animated feature-film footage",
                1,
            )
        elif "reference medium" in medium or "image-to-video" in medium:
            base = base.replace(
                "Cinematic live-action film footage",
                "Cinematic film footage matching the supplied reference medium",
                1,
            )
        contract = prompt_contract(
            direction,
            scene=scene,
            shot=shot,
        )[:2000]
        if not contract:
            return base
        return (base[: max(0, 3500 - len(contract) - 1)] + " " + contract)[:3500]

    def run_pipeline(
        brief: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        direction = _direction_from_brief(brief)
        render_media = bool(kwargs.get("render_media", True))
        video_mode = str(kwargs.get("video_mode", "cards"))
        if render_media and video_mode == "ai-video":
            errors = approval_gate_errors(direction)
            if errors:
                raise pipeline.BriefValidationError(
                    "Paid production is blocked by preproduction approval gates: "
                    + "; ".join(errors)
                )
        _CREATIVE_CONTEXT.set(
            {
                "creativeDirection": direction,
                "authoredScript": _authored_script(
                    brief.get("authoredScript")
                    or brief.get("authored_script")
                ),
            }
        )
        return original_run_pipeline(brief, *args, **kwargs)

    pipeline.validate_brief = validate_brief
    pipeline.build_film_from_brief = build_film_from_brief
    pipeline.run_tgrm = run_tgrm
    pipeline.run_pipeline = run_pipeline
    pipeline._creative_controls_installed = True

    script_engine.build_film_from_brief = build_film_from_brief
    tgrm.run_tgrm = run_tgrm
    ai_video.scene_prompt = scene_prompt

    try:
        from . import media

        media.generate_ai_video = ai_video.generate_ai_video
    except Exception:
        pass
