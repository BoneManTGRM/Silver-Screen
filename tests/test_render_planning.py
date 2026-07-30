from __future__ import annotations

from pathlib import Path

from silver_screen.render_planning import (
    blueprint_runtime_seconds,
    build_render_plan,
    recommended_provider_call_budget,
    requires_continuous_confirmation,
)


def test_two_minute_trailer_blueprint_plans_fifteen_eight_second_clips() -> None:
    plan = build_render_plan(
        "trailer", mode="match_blueprint", clip_duration_seconds=8
    )
    assert blueprint_runtime_seconds("trailer") == 120
    assert plan.runtime_seconds == 120
    assert plan.planned_clips == 15
    assert plan.matches_blueprint is True


def test_preview_mode_is_explicitly_one_provider_clip() -> None:
    plan = build_render_plan("trailer", mode="preview", clip_duration_seconds=8)
    assert plan.runtime_seconds == 8
    assert plan.planned_clips == 1
    assert plan.matches_blueprint is False
    assert plan.mismatch_seconds == -112


def test_custom_runtime_is_bounded_and_rounded_into_provider_clips() -> None:
    plan = build_render_plan(
        "short",
        mode="custom",
        custom_runtime_seconds=65,
        clip_duration_seconds=8,
    )
    assert plan.runtime_seconds == 65
    assert plan.planned_clips == 9


def test_call_ceiling_can_reserve_capacity_for_targeted_repairs() -> None:
    plan = build_render_plan("trailer", clip_duration_seconds=8)
    assert recommended_provider_call_budget(
        plan, retries_per_clip=0, include_retry_capacity=True
    ) == 15
    assert recommended_provider_call_budget(
        plan, retries_per_clip=1, include_retry_capacity=True
    ) == 30
    assert recommended_provider_call_budget(
        plan, retries_per_clip=3, include_retry_capacity=False
    ) == 15


def test_only_continuous_multi_clip_work_requires_extra_confirmation() -> None:
    full_plan = build_render_plan("trailer", clip_duration_seconds=8)
    preview_plan = build_render_plan(
        "trailer", mode="preview", clip_duration_seconds=8
    )
    assert requires_continuous_confirmation(full_plan, continuous=True) is True
    assert requires_continuous_confirmation(full_plan, continuous=False) is False
    assert requires_continuous_confirmation(preview_plan, continuous=True) is False


def test_full_blueprint_streamlit_page_compiles() -> None:
    page = Path("pages/1_Full_Blueprint_Production.py")
    compile(page.read_text(encoding="utf-8"), str(page), "exec")
