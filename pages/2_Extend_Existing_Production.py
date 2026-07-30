"""Extend an existing completed clip into a longer blueprint production."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.pipeline import PipelineError
from silver_screen.production_dashboard import production_view, queue_rows
from silver_screen.render_planning import (
    build_render_plan,
    recommended_provider_call_budget,
    requires_continuous_confirmation,
)
from silver_screen.runtime import list_runs, load_run
from silver_screen.science import FORMATS, SCIENCE
from silver_screen.video_extension import extend_video_run

st.set_page_config(
    page_title="Silver-Screen | Extend Existing Production",
    page_icon="↗️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _runtime(seconds: object) -> str:
    value = max(0, int(float(seconds or 0)))
    minutes, remainder = divmod(value, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {remainder:02d}s"
    if minutes:
        return f"{minutes}m {remainder:02d}s"
    return f"{remainder}s"


def _label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    return (
        f"{record.get('runId')} | {record.get('status')} | "
        f"{brief.get('title') or 'Untitled'}"
    )


def _read(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


st.title("↗️ Extend Existing Production")
st.caption(SCIENCE["credit"])
st.write(
    "Use this page when a film was completed as a single 8-second clip but should "
    "have been a longer blueprint. Silver-Screen keeps every verified clip, expands "
    "the same durable queue, and generates only the missing footage."
)

replicate_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
clip_duration = int(os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8") or 8)
if clip_duration not in {4, 6, 8}:
    clip_duration = 8

runs = list_runs("runs", 100)
run_map = {_label(record): record for record in runs}

with st.sidebar:
    st.header("Saved film")
    selected = st.selectbox(
        "Existing production",
        list(run_map) or ["No saved productions"],
        disabled=not run_map,
    )

    st.header("New target")
    format_key = st.selectbox(
        "Target story format",
        list(FORMATS),
        index=list(FORMATS).index("trailer"),
        format_func=lambda key: (
            f"{FORMATS[key]['label']} | {FORMATS[key]['minutes']} min blueprint"
        ),
        disabled=not run_map,
    )
    target_mode_label = st.radio(
        "Target length",
        ["Match target blueprint", "Custom runtime"],
        index=0,
        disabled=not run_map,
    )
    target_mode = (
        "match_blueprint"
        if target_mode_label == "Match target blueprint"
        else "custom"
    )
    custom_runtime = None
    if target_mode == "custom":
        custom_runtime = st.number_input(
            "Custom target runtime (seconds)",
            min_value=4,
            max_value=5400,
            value=120,
            step=clip_duration,
            disabled=not run_map,
        )

    plan = build_render_plan(
        format_key,
        mode=target_mode,
        custom_runtime_seconds=custom_runtime,
        clip_duration_seconds=clip_duration,
    )
    st.info(
        f"Target: **{_runtime(plan.runtime_seconds)}** across "
        f"**{plan.planned_clips} clips**."
    )

    batch_size = int(
        st.number_input(
            "New clips this checkpoint",
            min_value=1,
            max_value=max(1, min(16, plan.planned_clips)),
            value=1,
            step=1,
            disabled=not run_map,
        )
    )
    retries = int(
        st.slider(
            "TGRM retries per clip",
            min_value=0,
            max_value=6,
            value=1,
            disabled=not run_map,
        )
    )
    call_budget = recommended_provider_call_budget(
        plan, retries_per_clip=retries, include_retry_capacity=True
    )
    st.caption(
        f"Whole-production safety ceiling: **{call_budget} calls**. "
        f"This request creates at most **{batch_size} new clips**."
    )
    continuity = st.checkbox(
        "Continue from verified final frames", value=True, disabled=not run_map
    )
    continuous = st.checkbox(
        "Continue in this browser request until complete",
        value=False,
        disabled=not run_map,
    )
    confirmation_required = requires_continuous_confirmation(
        plan, continuous=continuous
    )
    continuous_confirmed = False
    if confirmation_required:
        continuous_confirmed = st.checkbox(
            f"I authorize continuous work up to the {call_budget}-call ceiling.",
            value=False,
        )

    if not replicate_ready:
        st.error("REPLICATE_API_TOKEN is missing from deployment secrets.")

    extend_clicked = st.button(
        "Extend this production",
        type="primary",
        use_container_width=True,
        disabled=(
            not run_map
            or not replicate_ready
            or (confirmation_required and not continuous_confirmed)
        ),
    )

current_result: dict[str, Any] | None = None
if run_map:
    record = run_map[selected]
    try:
        current_result = load_run(str(record.get("runId")), "runs")
    except (FileNotFoundError, ValueError) as exc:
        st.error(str(exc))

if current_result:
    current_media = current_result.get("media") or {}
    current_metrics = current_media.get("metrics") or {}
    current_verified = int(current_metrics.get("verifiedShots", 0) or 0)
    current_planned = int(current_metrics.get("plannedShots", 0) or 0)
    current_seconds = float(current_metrics.get("verifiedSeconds", 0) or 0)
    st.subheader("Current production")
    columns = st.columns(4)
    columns[0].metric("Verified clips", f"{current_verified}/{current_planned}")
    columns[1].metric("Verified runtime", _runtime(current_seconds))
    columns[2].metric("New target", _runtime(plan.runtime_seconds))
    columns[3].metric(
        "Missing clips", max(0, plan.planned_clips - current_verified)
    )
    if plan.planned_clips <= current_planned:
        st.info(
            "The selected target is not longer than the existing production plan. "
            "Choose a longer blueprint or custom runtime to extend it."
        )
    elif current_verified:
        st.success(
            f"The existing {current_verified} verified clip(s) will be preserved. "
            "Only missing clips will be generated."
        )

if extend_clicked and current_result and run_map:
    record = run_map[selected]
    current_media = current_result.get("media") or {}
    current_planned = int(
        (current_media.get("metrics") or {}).get("plannedShots", 0) or 0
    )
    if plan.planned_clips <= current_planned:
        st.error("Select a target longer than the existing production plan.")
    else:
        progress = st.progress(0, text="Opening existing video queue")
        try:
            result = extend_video_run(
                str(record.get("runId")),
                target_runtime_seconds=plan.runtime_seconds,
                max_shots=plan.planned_clips,
                output_root="runs",
                batch_size=batch_size,
                continuous=continuous,
                max_retries=retries,
                max_provider_calls=call_budget,
                use_continuity=continuity,
            )
            st.session_state["extended_production_result"] = result
            view = production_view(result.get("media") or {})
            progress.progress(view.progress_percent, text=view.headline)
        except PipelineError as exc:
            st.error(str(exc))

result = st.session_state.get("extended_production_result")
if not result:
    st.info(
        "Your existing 8-second Moonie Moo film can be extended to a 2-minute "
        "Trailer plan without throwing away the first verified clip."
    )
    st.stop()

media = result.get("media") or {}
metrics = media.get("metrics") or {}
view = production_view(media)
if view.severity == "success":
    st.success(f"{view.headline}. {view.detail}")
elif view.severity == "warning":
    st.warning(f"{view.headline}. {view.detail}")
else:
    st.info(f"{view.headline}. {view.detail}")

verified = int(metrics.get("verifiedShots", 0) or 0)
planned = int(metrics.get("plannedShots", 0) or 0)
st.progress(view.progress_percent, text=f"Verified {verified} of {planned} clips")

playable = (
    media.get("final_video_path")
    or media.get("partial_video_path")
    or media.get("hero_path")
)
if playable:
    st.video(playable)
    data = _read(playable)
    if data is not None:
        st.download_button(
            "Download extended checkpoint MP4",
            data,
            file_name=Path(playable).name,
            mime="video/mp4",
            use_container_width=True,
        )
rows = queue_rows(media)
if rows:
    st.dataframe(rows, use_container_width=True, hide_index=True)
