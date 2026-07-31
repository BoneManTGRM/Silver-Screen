"""Director review, weak-boundary analysis, and targeted transition retakes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.cinematic_continuity import rebuild_run_transitions
from silver_screen.pipeline import PipelineError, resume_video_run
from silver_screen.production_resilience import (
    DirectorReviewError,
    prepare_director_review,
    schedule_transition_retake,
)
from silver_screen.runtime import list_runs
from silver_screen.science import SCIENCE
from silver_screen.transition_engine import rows as transition_rows

st.set_page_config(
    page_title="Silver-Screen | Director Review",
    page_icon="🎞️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    title = record.get("title") or brief.get("title") or "Untitled"
    return f"{record.get('runId')} | {record.get('status')} | {title}"


def _video(result: dict[str, Any]) -> str | None:
    media = result.get("media") or {}
    return (
        media.get("final_video_path")
        or media.get("partial_video_path")
        or media.get("hero_path")
    )


def _candidate_label(item: dict[str, Any]) -> str:
    relation = str(item.get("relation") or "scene_change").replace("_", " ")
    return (
        f"Clip {item.get('fromOrder')} → {item.get('toOrder')} | "
        f"{relation} | {float(item.get('scorePercent', 0) or 0):.1f}/100"
    )


st.title("🎞️ Director Review")
st.caption(SCIENCE["credit"])
st.write(
    "Inspect every clip boundary, identify the transitions that still look abrupt, "
    "and reopen only the incoming clip for a targeted retake. The accepted original "
    "is preserved and Silver-Screen automatically keeps the better candidate."
)
st.info(
    "Analyzing, scoring, and rebuilding transitions are local operations. A provider "
    "charge occurs only when you explicitly authorize and render a targeted retake."
)

records = [
    record
    for record in list_runs("runs", 100)
    if (
        Path(str(record.get("workspace") or ""))
        / "media"
        / "video_queue.json"
    ).exists()
]
run_map = {_label(record): record for record in records}

with st.sidebar:
    st.header("Review source")
    selected = st.selectbox(
        "Saved production",
        list(run_map) or ["No saved video productions"],
        disabled=not run_map,
    )
    mode_label = st.radio(
        "Transition analysis style",
        ["Automatic cinematic", "Subtle", "Strong masking"],
        index=0,
    )
    mode = {
        "Automatic cinematic": "auto",
        "Subtle": "subtle",
        "Strong masking": "strong",
    }[mode_label]
    analyze_clicked = st.button(
        "Analyze selected production",
        type="primary",
        use_container_width=True,
        disabled=not run_map,
    )
    st.divider()
    st.caption(
        "Replicate 429 throttles now use bounded automatic Retry-After waiting. "
        "Verified clips and prediction checkpoints remain preserved."
    )

if analyze_clicked and run_map:
    run_id = str(run_map[selected].get("runId") or "")
    try:
        with st.spinner("Scoring visual boundaries and refreshing the edit plan..."):
            st.session_state["director_review"] = prepare_director_review(
                run_id,
                output_root="runs",
                mode=mode,
            )
    except (DirectorReviewError, OSError, ValueError) as exc:
        st.error(str(exc))

review = st.session_state.get("director_review")
if not review:
    st.warning(
        "Select a saved production with at least two verified clips, then analyze it."
    )
    st.stop()

plan = review.get("plan") or {}
metrics = plan.get("metrics") or {}
result = review.get("result") or {}
candidates = review.get("candidates") or []
run_id = str(review.get("runId") or "")

columns = st.columns(6)
columns[0].metric("Boundaries", int(metrics.get("boundaries", 0) or 0))
columns[1].metric(
    "Average score",
    f"{float(metrics.get('averageScore', 0) or 0) * 100:.1f}",
)
columns[2].metric(
    "Lowest score",
    f"{float(metrics.get('minimumScore', 0) or 0) * 100:.1f}",
)
columns[3].metric(
    "Smooth or better",
    int(metrics.get("smoothOrBetterBoundaries", 0) or 0),
)
columns[4].metric(
    "Needs attention",
    int(metrics.get("attentionBoundaries", 0) or 0),
)
columns[5].metric(
    "Hard cuts avoided",
    int(metrics.get("hardCutsAvoided", 0) or 0),
)

current_video = _video(result)
if current_video:
    st.subheader("Current cinematic cut")
    st.video(current_video)

st.subheader("Transition report")
rows = transition_rows(plan)
if rows:
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("The selected production currently has only one verified clip.")

if not candidates:
    st.success(
        "No transition currently falls below the director-retake threshold. You can "
        "still rebuild the cut with a different local smoothing strength."
    )
    if st.button("Rebuild current cinematic cut locally", use_container_width=True):
        try:
            with st.spinner("Rebuilding the cinematic edit without provider calls..."):
                rebuilt = rebuild_run_transitions(
                    run_id,
                    output_root="runs",
                    mode=mode,
                )
            st.session_state["director_review"] = prepare_director_review(
                run_id,
                output_root="runs",
                mode=mode,
            )
            st.success("The cinematic cut was rebuilt locally.")
            rebuilt_video = _video(rebuilt)
            if rebuilt_video:
                st.video(rebuilt_video)
        except Exception as exc:
            st.error(str(exc))
    st.stop()

candidate_map = {_candidate_label(item): item for item in candidates}
selected_candidate_label = st.selectbox(
    "Boundary to repair",
    list(candidate_map),
)
candidate = candidate_map[selected_candidate_label]

st.warning(
    f"**Recommended retake:** clip {candidate.get('toOrder')}  \n"
    f"**Relationship:** {str(candidate.get('relation') or '').replace('_', ' ').title()}  \n"
    f"**Current score:** {float(candidate.get('scorePercent', 0) or 0):.1f}/100  \n"
    f"**Reason:** {candidate.get('reason')}"
)
with st.expander("Targeted retake direction", expanded=False):
    st.write(candidate.get("promptDirective") or "No additional direction recorded.")

paid_authorized = st.checkbox(
    "I authorize one additional paid Replicate prediction for this selected transition.",
    value=False,
)
left, right = st.columns(2)
with left:
    schedule_only = st.button(
        "Schedule retake only",
        use_container_width=True,
        disabled=bool(candidate.get("active")),
        help=(
            "Preserves the existing clip and reopens the weak incoming shot. It does "
            "not call Replicate until you continue the saved production."
        ),
    )
with right:
    schedule_and_render = st.button(
        "Schedule and render one retake",
        type="primary",
        use_container_width=True,
        disabled=bool(candidate.get("active")) or not paid_authorized,
    )

if schedule_only or schedule_and_render:
    try:
        scheduled = schedule_transition_retake(
            run_id,
            str(candidate.get("transitionId") or ""),
            output_root="runs",
            reason=str(candidate.get("reason") or ""),
        )
        st.success(
            "The accepted clip was preserved and the incoming shot was reopened for "
            "one targeted transition retake."
        )
        if schedule_and_render:
            with st.spinner(
                "Waiting through any provider throttle, rendering one retake, and "
                "automatically selecting the stronger candidate..."
            ):
                resumed = resume_video_run(
                    run_id,
                    output_root="runs",
                    batch_size=1,
                    continuous=False,
                    max_retries=int(scheduled.get("requiredMaxRetries", 1) or 1),
                    max_provider_calls=int(
                        scheduled.get("authorizedProviderCalls", 1) or 1
                    ),
                    use_continuity=True,
                )
                rebuilt = rebuild_run_transitions(
                    run_id,
                    output_root="runs",
                    mode=mode,
                )
            st.success(
                "The retake was evaluated against the preserved original. "
                "Silver-Screen kept the candidate with the stronger transition score."
            )
            finished_video = _video(rebuilt) or _video(resumed)
            if finished_video:
                st.video(finished_video)
            try:
                st.session_state["director_review"] = prepare_director_review(
                    run_id,
                    output_root="runs",
                    mode=mode,
                )
            except DirectorReviewError:
                st.session_state.pop("director_review", None)
        else:
            st.session_state.pop("director_review", None)
            st.info(
                "The retake is scheduled but no provider call was made. Open Star "
                "Vehicle Studio or Full Blueprint Production and continue this saved "
                "run with one new clip when you are ready."
            )
    except (DirectorReviewError, PipelineError, OSError, ValueError) as exc:
        st.error(str(exc))

st.caption(
    "Targeted retakes never delete the previously accepted source clip. Candidate "
    "paths, scores, decisions, and provider authorization are recorded in the durable queue."
)
