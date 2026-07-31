"""Local non-linear editor for verified Silver-Screen footage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from silver_screen.runtime import list_runs
from silver_screen.science import SCIENCE
from silver_screen.timeline_editor import (
    TimelineEditorError,
    load_timeline,
    render_timeline,
    save_timeline,
)

st.set_page_config(
    page_title="Silver-Screen | Timeline Editor",
    page_icon="🎞️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    return (
        f"{record.get('runId')} | {record.get('status')} | "
        f"{brief.get('title') or record.get('title') or 'Untitled'}"
    )


st.title("🎞️ Timeline Editor")
st.caption(SCIENCE["credit"])
st.write(
    "Reorder verified shots, trim their heads and tails, choose transition handles, "
    "lock approved footage against autonomous retakes, annotate the edit, and render a "
    "new local cut without another video-model call."
)
st.info(
    "Timeline edits and rendering use local FFmpeg processing. They do not call "
    "Replicate, OpenAI, or ElevenLabs."
)

records = [
    item
    for item in list_runs("runs", 100)
    if (Path(str(item.get("workspace") or "")) / "media" / "video_queue.json").exists()
]
run_map = {_label(item): item for item in records}
with st.sidebar:
    selected = st.selectbox(
        "Saved production",
        list(run_map) or ["No saved productions"],
        disabled=not run_map,
    )
    load_clicked = st.button(
        "Load timeline",
        type="primary",
        use_container_width=True,
        disabled=not run_map,
    )

if load_clicked and run_map:
    run_id = str(run_map[selected].get("runId") or "")
    try:
        st.session_state["timeline-run-id"] = run_id
        st.session_state["timeline"] = load_timeline(run_id, output_root="runs")
    except TimelineEditorError as exc:
        st.error(str(exc))

run_id = st.session_state.get("timeline-run-id")
timeline = st.session_state.get("timeline")
if not run_id or not timeline:
    st.warning("Select a saved production and load its timeline.")
    st.stop()

items = timeline.get("items") or []
frame = pd.DataFrame(items)
columns = [
    "timelineOrder",
    "shotId",
    "sourceOrder",
    "scene",
    "label",
    "inSeconds",
    "outSeconds",
    "transitionStyle",
    "transitionSeconds",
    "locked",
    "notes",
    "sourcePath",
]
for column in columns:
    if column not in frame:
        frame[column] = None
frame = frame[columns]

st.subheader("Edit decision timeline")
st.caption(
    "Change `timelineOrder` to reorder clips. The transition fields on a row control "
    "the join from that row into the following row. Locking a shot prevents the "
    "Autonomous Director from selecting it for another automatic retake."
)
edited = st.data_editor(
    frame,
    use_container_width=True,
    hide_index=True,
    num_rows="fixed",
    column_config={
        "timelineOrder": st.column_config.NumberColumn("Order", min_value=1, step=1),
        "shotId": st.column_config.TextColumn("Shot ID", disabled=True),
        "sourceOrder": st.column_config.NumberColumn("Source #", disabled=True),
        "scene": st.column_config.NumberColumn("Scene", disabled=True),
        "label": st.column_config.TextColumn("Label"),
        "inSeconds": st.column_config.NumberColumn("Trim in", min_value=0.0, step=0.05, format="%.2f"),
        "outSeconds": st.column_config.NumberColumn("Trim out", min_value=0.2, step=0.05, format="%.2f"),
        "transitionStyle": st.column_config.SelectboxColumn("Transition", options=["fade", "fadeblack"]),
        "transitionSeconds": st.column_config.NumberColumn("Blend", min_value=0.0, max_value=1.2, step=0.02, format="%.2f"),
        "locked": st.column_config.CheckboxColumn("Lock"),
        "notes": st.column_config.TextColumn("Editor notes"),
        "sourcePath": st.column_config.TextColumn("Source", disabled=True),
    },
    key="timeline-data-editor",
)

left, right = st.columns(2)
with left:
    save_clicked = st.button("Save timeline", use_container_width=True)
with right:
    render_clicked = st.button(
        "Render professional editor cut",
        type="primary",
        use_container_width=True,
    )

payload = {
    **timeline,
    "items": edited.to_dict(orient="records"),
}
if save_clicked:
    try:
        st.session_state["timeline"] = save_timeline(
            run_id,
            payload,
            output_root="runs",
        )
        st.success("Timeline, locks, trims, and transition handles were saved.")
    except TimelineEditorError as exc:
        st.error(str(exc))

if render_clicked:
    try:
        with st.spinner("Trimming, normalizing, transitioning, and rendering the editor cut..."):
            result = render_timeline(
                run_id,
                payload,
                output_root="runs",
            )
        st.session_state["timeline"] = result["timeline"]
        st.session_state["timeline-render"] = result
        st.success("The local editor cut was rendered without provider calls.")
    except Exception as exc:
        st.error(str(exc))

rendered = st.session_state.get("timeline-render")
if rendered:
    st.subheader("Rendered editor cut")
    path = rendered.get("outputPath")
    if path and Path(path).exists():
        st.video(path)
        st.download_button(
            "Download editor cut",
            Path(path).read_bytes(),
            file_name=Path(path).name,
            mime="video/mp4",
            use_container_width=True,
        )
    edl = rendered.get("edl") or {}
    cols = st.columns(2)
    for column, (label, value, mime) in zip(
        cols,
        [
            ("Download JSON EDL", edl.get("json"), "application/json"),
            ("Download CSV EDL", edl.get("csv"), "text/csv"),
        ],
    ):
        if value and Path(value).exists():
            column.download_button(
                label,
                Path(value).read_bytes(),
                file_name=Path(value).name,
                mime=mime,
                use_container_width=True,
            )
    with st.expander("Render report"):
        st.json(rendered.get("report") or {})
