"""Inspect and rebuild smooth cinematic transitions without new provider calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.cinematic_continuity import (
    CinematicTransitionError,
    load_transition_plan,
    rebuild_run_transitions,
    transition_rows,
)
from silver_screen.runtime import list_runs, load_run
from silver_screen.science import SCIENCE
from silver_screen.video_runtime import load_video_queue

st.set_page_config(
    page_title="Silver-Screen | Cinematic Continuity",
    page_icon="🎞️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    title = record.get("title") or brief.get("title") or "Untitled"
    return f"{record.get('runId')} | {record.get('status')} | {title}"


def _read(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _download(label: str, path: str | None, key: str) -> None:
    data = _read(path)
    if data is None:
        return
    st.download_button(
        label,
        data,
        file_name=Path(str(path)).name,
        mime="video/mp4",
        key=key,
        use_container_width=True,
    )


def _best_video(result: dict[str, Any]) -> str | None:
    media = result.get("media") or {}
    return (
        media.get("final_video_path")
        or media.get("partial_video_path")
        or media.get("hero_path")
    )


st.title("🎞️ Cinematic Continuity")
st.caption(SCIENCE["credit"])
st.write(
    "Analyze every boundary between verified clips, preserve match-on-action "
    "continuity, crossfade ambience and dialogue, and rebuild a smoother cinematic "
    "master. This page performs local FFmpeg work only and does not create new "
    "Replicate predictions."
)

records = list_runs("runs", 100)
eligible: dict[str, dict[str, Any]] = {}
for record in records:
    workspace = Path(str(record.get("workspace") or ""))
    queue_path = workspace / "media" / "video_queue.json"
    if queue_path.exists():
        eligible[_label(record)] = record

with st.sidebar:
    st.header("Saved production")
    selected = st.selectbox(
        "Film run",
        list(eligible) or ["No saved video runs"],
        disabled=not eligible,
    )
    mode_label = st.radio(
        "Smoothing strength",
        ["Automatic cinematic", "Subtle", "Strong masking"],
        index=0,
        help=(
            "Automatic uses short match transitions inside a scene, gentle cross "
            "dissolves between scenes, and controlled fades between chapters."
        ),
    )
    mode = {
        "Automatic cinematic": "auto",
        "Subtle": "subtle",
        "Strong masking": "strong",
    }[mode_label]
    rebuild_clicked = st.button(
        "Rebuild smooth cinematic cut",
        type="primary",
        use_container_width=True,
        disabled=not eligible,
    )
    st.info(
        "No new video-provider call is made. Existing verified source clips are "
        "preserved and a separate cinematic MP4 is assembled."
    )

if not eligible:
    st.warning(
        "No saved production with a durable video queue is available. Generate at "
        "least two verified clips first."
    )
    st.stop()

record = eligible[selected]
run_id = str(record.get("runId") or "")
workspace = Path(str(record.get("workspace") or "")).resolve()
media_root = workspace / "media"

if rebuild_clicked:
    try:
        with st.spinner(
            "Measuring clip boundaries, selecting TGRM transition repairs, "
            "crossfading audio, and encoding the cinematic master..."
        ):
            rebuilt = rebuild_run_transitions(
                run_id,
                output_root="runs",
                mode=mode,
            )
        st.session_state["continuity-result"] = rebuilt
        st.success(
            "Cinematic continuity rebuild completed. The verified source clips and "
            "prior assemblies remain available."
        )
    except (CinematicTransitionError, OSError, ValueError) as exc:
        st.error(str(exc))

result = st.session_state.get("continuity-result")
if not isinstance(result, dict) or str(
    (result.get("run") or {}).get("id") or run_id
) != run_id:
    try:
        result = load_run(run_id, "runs")
    except Exception as exc:
        st.error(str(exc))
        st.stop()

queue = load_video_queue(media_root) or {}
plan = queue.get("transitionPlan") or load_transition_plan(media_root) or {}
metrics = plan.get("metrics") or {}
assembly = plan.get("assembly") or {}
transitions = plan.get("transitions") or []

columns = st.columns(6)
columns[0].metric("Boundaries", metrics.get("boundaries", 0))
columns[1].metric(
    "Average transition",
    f"{float(metrics.get('averageScore', 1.0) or 0.0) * 100:.0f}%",
)
columns[2].metric(
    "Minimum transition",
    f"{float(metrics.get('minimumScore', 1.0) or 0.0) * 100:.0f}%",
)
columns[3].metric(
    "Smooth or better",
    metrics.get("smoothOrBetterBoundaries", 0),
)
columns[4].metric(
    "Hard cuts avoided",
    metrics.get("hardCutsAvoided", 0),
)
columns[5].metric(
    "Fallbacks",
    metrics.get("assemblyFallbacks", 0),
)

best = _best_video(result)
video_tab, timeline_tab, audit_tab, files_tab = st.tabs(
    ["Cinematic cut", "Transition timeline", "Continuity audit", "Files"]
)

with video_tab:
    if best and Path(best).exists():
        st.video(best)
        _download(
            "Download cinematic MP4",
            best,
            "cinematic-master",
        )
    else:
        st.info(
            "Press **Rebuild smooth cinematic cut** to create the local transition "
            "master from the saved verified clips."
        )
    source_candidates = [
        media_root / "final_ai_film.mp4",
        media_root / "partial_ai_film.mp4",
    ]
    source = next((path for path in source_candidates if path.exists()), None)
    if source and str(source) != str(best):
        with st.expander("Previous hard-cut assembly"):
            st.video(str(source))
            _download(
                "Download previous assembly",
                str(source),
                "previous-assembly",
            )

with timeline_tab:
    rows = transition_rows(plan)
    if rows:
        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
        )
    elif len(
        [
            shot
            for shot in queue.get("shots") or []
            if isinstance(shot, dict) and shot.get("status") == "verified"
        ]
    ) < 2:
        st.info("At least two verified clips are required for a transition.")
    else:
        st.info("Rebuild the cinematic cut to calculate the transition timeline.")

with audit_tab:
    attention = [
        item
        for item in transitions
        if isinstance(item, dict) and item.get("rating") == "attention"
    ]
    if attention:
        st.warning(
            f"{len(attention)} transition(s) remain visually difficult. The engine "
            "used stronger local masking, but the next clip may eventually benefit "
            "from a targeted provider retake."
        )
    else:
        st.success(
            "No transition is currently classified as requiring provider-level "
            "attention."
        )
    st.json(
        {
            "status": plan.get("status"),
            "settings": plan.get("settings"),
            "metrics": metrics,
            "assembly": assembly,
            "transitions": transitions,
        }
    )

with files_tab:
    plan_path = media_root / "transition_plan.json"
    runtime_path = media_root / "transition_runtime.json"
    if plan_path.exists():
        st.download_button(
            "Download transition plan",
            plan_path.read_bytes(),
            file_name=plan_path.name,
            mime="application/json",
            use_container_width=True,
        )
    if runtime_path.exists():
        st.download_button(
            "Download transition runtime",
            runtime_path.read_bytes(),
            file_name=runtime_path.name,
            mime="application/json",
            use_container_width=True,
        )

st.caption(
    "Cinematic smoothing reduces abrupt joins through match-on-action prompts, "
    "frame-boundary scoring, video xfade, and audio acrossfade. Independently "
    "generated clips can still contain identity or motion differences that require "
    "a targeted retake rather than a stronger edit."
)
