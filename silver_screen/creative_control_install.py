"""Install creative-control extensions without breaking the public pipeline API."""

from __future__ import annotations

import contextvars
import re
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


def _lead_names(state: dict[str, Any]) -> tuple[str, str]:
    characters = [item for item in state.get("characters") or [] if isinstance(item, dict)]
    lead = str(characters[0].get("name") or "The lead") if characters else "The lead"
    other = str(characters[1].get("name") or "the other person") if len(characters) > 1 else "the other person"
    return lead, other


def _mature_logline(state: dict[str, Any]) -> None:
    if state.get("scriptSource") == "authored":
        return
    lead, other = _lead_names(state)
    premise = str(state.get("premise") or "").strip().rstrip(".")
    if premise:
        premise = premise[0].lower() + premise[1:]
    state["logline"] = (
        f"{lead} realizes that {premise}. The only useful lead points to {other}, "
        "whose silence may be protection, leverage, or control."
    )


def _ground_story_bible(state: dict[str, Any], direction: dict[str, Any]) -> None:
    if state.get("scriptSource") == "authored":
        return
    lead, other = _lead_names(state)
    profile = str(direction.get("profile") or "grounded_prestige")
    bible = state.setdefault("storyBible", {})
    if profile == "modern_spy_thriller":
        theme = "Competence becomes dangerous when someone else controls what counts as known."
        world_rule = "Information has value only while the other side does not know you have it."
    elif profile == "naturalistic_drama":
        theme = "People reveal what they protect by refusing to say the obvious."
        world_rule = "Small choices accumulate until silence is no longer neutral."
    elif profile == "dark_psychological":
        theme = "Control depends on whose version of events is allowed to feel ordinary."
        world_rule = "Every attempt to verify the past changes the present relationship."
    elif profile == "premium_animation":
        theme = "Status is fragile; loyalty becomes visible through behavior under pressure."
        world_rule = "Public mistakes create private choices that cannot be hidden for long."
    else:
        theme = "Pressure reveals what people value more clearly than explanation does."
        world_rule = "Every concrete action changes what the other side believes is known."
    bible.update(
        {
            "theme": theme,
            "protagonistNeed": (
                f"{lead} needs to identify who controls the timing without revealing how much has already been noticed."
            ),
            "opposingPressure": (
                f"{other} controls access, timing, or interpretation and can force a premature decision."
            ),
            "worldRule": world_rule,
        }
    )


def _clean_generated_actions(state: dict[str, Any]) -> None:
    if state.get("scriptSource") == "authored":
        return
    replacements = (
        (
            "Nobody states the theme, summarizes the premise, or performs for the camera.",
            "The exchange stays quiet and specific.",
        ),
        (
            "The camera stays motivated by behavior and holds long enough to catch the change in their decisions.",
            "A small change in behavior reveals the next decision.",
        ),
        (
            "Performances remain restrained and the scene ends on a decision that can be carried into the next shot.",
            "The scene ends on a decision that carries into the next room.",
        ),
    )
    for scene in state.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        action = str(scene.get("action") or "")
        action = re.sub(
            r"The tension comes from what both characters avoid saying about this situation:\s*.*?\.\s*The camera",
            "The tension comes from what both characters avoid saying. The camera",
            action,
            flags=re.S,
        )
        for before, after in replacements:
            action = action.replace(before, after)
        scene["action"] = " ".join(action.split())


def _prepare_generated_state(state: dict[str, Any], direction: dict[str, Any]) -> None:
    _mature_logline(state)
    _ground_story_bible(state, direction)
    _clean_generated_actions(state)


def _compose_prompt(base: str, contract: str, limit: int = 3500) -> str:
    """Keep late continuity and Director Review directives when space is tight."""

    contract = contract[:2000].strip()
    if not contract:
        return base[:limit]
    markers = (
        "CINEMATIC CONTINUITY:",
        "CINEMATIC TRANSITION:",
        "CINEMATIC OPENING:",
        "DIRECTOR REVIEW RETAKE:",
    )
    positions = [base.find(marker) for marker in markers if base.find(marker) >= 0]
    tail = ""
    head = base
    if positions:
        start = min(positions)
        head, tail = base[:start].rstrip(), base[start:].strip()
    reserved = len(contract) + len(tail) + 2
    head_budget = max(0, limit - reserved)
    parts = [head[:head_budget].rstrip(), tail, contract]
    return " ".join(part for part in parts if part)[:limit]


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
        _prepare_generated_state(state, direction)
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
        current = result.get("state") or state
        direction = normalize_creative_direction(current.get("creativeDirection"))
        if current.get("scriptSource") == "authored" and current.get("authoredScript"):
            current = apply_creative_direction(
                current,
                direction,
                authored_script=str(current.get("authoredScript") or ""),
                render_screenplay_fn=script_engine.render_screenplay,
            )
        else:
            _prepare_generated_state(current, direction)
        result["state"] = finalize_creative_state(
            current,
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
        )
        return _compose_prompt(base, contract)

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
