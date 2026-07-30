"""Pure runtime planning for blueprint-matched and preview film renders."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from .science import FORMATS

RENDER_MODES = {"match_blueprint", "preview", "custom"}
MIN_RUNTIME_SECONDS = 4
MAX_RUNTIME_SECONDS = 5400
ALLOWED_CLIP_DURATIONS = (4, 6, 8)


@dataclass(frozen=True)
class RenderPlan:
    """A transparent contract between the story blueprint and paid video work."""

    format_key: str
    format_label: str
    blueprint_minutes: float
    mode: str
    runtime_seconds: int
    clip_duration_seconds: int
    planned_clips: int
    matches_blueprint: bool
    mismatch_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_clip_duration(value: int | float | str | None) -> int:
    """Return the nearest provider-supported clip duration."""

    try:
        requested = int(value or 8)
    except (TypeError, ValueError):
        requested = 8
    return min(ALLOWED_CLIP_DURATIONS, key=lambda item: abs(item - requested))


def blueprint_runtime_seconds(format_key: str) -> int:
    """Return the complete target runtime represented by a story format."""

    key = str(format_key or "short")
    meta = FORMATS.get(key, FORMATS["short"])
    seconds = round(float(meta.get("minutes", 0) or 0) * 60)
    return max(MIN_RUNTIME_SECONDS, min(MAX_RUNTIME_SECONDS, int(seconds)))


def build_render_plan(
    format_key: str,
    *,
    mode: str = "match_blueprint",
    custom_runtime_seconds: int | float | str | None = None,
    clip_duration_seconds: int | float | str | None = 8,
) -> RenderPlan:
    """Build a render plan without silently treating a blueprint as one clip."""

    key = str(format_key or "short")
    if key not in FORMATS:
        key = "short"
    meta = FORMATS[key]
    selected_mode = str(mode or "match_blueprint").strip().lower()
    if selected_mode not in RENDER_MODES:
        selected_mode = "match_blueprint"
    clip_duration = normalize_clip_duration(clip_duration_seconds)
    blueprint_seconds = blueprint_runtime_seconds(key)

    if selected_mode == "preview":
        runtime = clip_duration
    elif selected_mode == "custom":
        try:
            runtime = int(custom_runtime_seconds or clip_duration)
        except (TypeError, ValueError):
            runtime = clip_duration
        runtime = max(MIN_RUNTIME_SECONDS, min(MAX_RUNTIME_SECONDS, runtime))
    else:
        runtime = blueprint_seconds

    clips = max(1, math.ceil(runtime / clip_duration))
    return RenderPlan(
        format_key=key,
        format_label=str(meta.get("label") or key.title()),
        blueprint_minutes=float(meta.get("minutes", 0) or 0),
        mode=selected_mode,
        runtime_seconds=runtime,
        clip_duration_seconds=clip_duration,
        planned_clips=clips,
        matches_blueprint=runtime == blueprint_seconds,
        mismatch_seconds=runtime - blueprint_seconds,
    )


def recommended_provider_call_budget(
    plan: RenderPlan,
    *,
    retries_per_clip: int = 0,
    include_retry_capacity: bool = True,
) -> int:
    """Return an explicit whole-production safety ceiling for provider calls."""

    retries = max(0, min(8, int(retries_per_clip or 0)))
    multiplier = retries + 1 if include_retry_capacity else 1
    return max(1, plan.planned_clips * multiplier)


def requires_continuous_confirmation(plan: RenderPlan, *, continuous: bool) -> bool:
    """Continuous multi-clip work requires a separate explicit confirmation."""

    return bool(continuous and plan.planned_clips > 1)
