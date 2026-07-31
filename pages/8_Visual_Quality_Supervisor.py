"""Local visual-quality review and consent-gated targeted retakes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.pipeline import PipelineError, resume_video_run
from silver_screen.runtime import list_runs
from silver_screen.science import SCIENCE
from silver_screen.visual_quality import (
    VisualQualityError,
    inspect_run,
    schedule_quality_retake,
)

st.set_page_config(
    page_title="Silver-Screen | Visual Quality Supervisor",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    return f"{record.get('runId')} | {record.get('status')} | {record.get('title') or brief.get('title') or 'Untitled'}"


def _report_label(item: dict[str, Any]) -> str:
    return f"Clip {item.get('order')} | {item.get('rating')} | {float(item.get('scorePercent', 0) or 0):.1f}/100"


st.title("🔎 Visual Quality and Identity Supervisor")
st.caption(SCIENCE["credit"])
st.write(
    "Inspect accepted source clips before final delivery, measure blur, exposure, "
    "flicker, frozen motion, intra-clip appearance drift, and broad reference-pack "
    "consistency, then reopen only the failed clip for a targeted repair."
)
st.info(
    "Analysis is local and makes no provider call. Broad appearance comparison is "
    "non-biometric: Silver-Screen does not recognize people or create face embeddings."
)

records = [
    record
    for record in list_runs("runs", 100)
    if (Path(str(record.get("workspace") or "")) / "media" / "video_queue.json").exists()
]
run_map = {_label(record): record for record in records}

with st.sidebar:
    st.header("Inspection source")
    selected = st.selectbox(
        "Saved production",
        list(run_map) or ["No saved video productions"],
        disabled=not run_map,
    )
    analyze_clicked = st.button(
        "Inspect every verified clip",
        type="primary",
        use_container_width=True,
        disabled=not run_map,
    )
    st.divider()
    st.caption(
        "New AI clips are also checked automatically after download. Hard technical "
        "failures are rejected before final assembly and enter the existing bounded TGRM retry loop."
    )

if analyze_clicked and run_map:
    try:
        with st.spinner("Sampling frames and measuring local visual quality..."):
            run_id = str(run_map[selected].get("runId") or "")
            st.session_state["visual_quality_review"] = inspect_run(run_id, output_root="runs")
    except (VisualQualityError, OSError, ValueError) as exc:
        st.error(str(exc))

review = st.session_state.get("visual_quality_review")
if not review:
    st.warning("Select a saved video production and inspect it.")
    st.stop()

report = review.get("report") or {}
reports = report.get("reports") or []
run_id = str(report.get("runId") or "")
columns = st.columns(6)
columns[0].metric("Inspected", int(report.get("clips", 0) or 0))
columns[1].metric("Accepted", int(report.get("accepted", 0) or 0))
columns[2].metric("Review", int(report.get("review", 0) or 0))
columns[3].metric("Rejected", int(report.get("rejected", 0) or 0))
columns[4].metric("Average", f"{float(report.get('averageScore', 0) or 0) * 100:.1f}")
columns[5].metric("Minimum", f"{float(report.get('minimumScore', 0) or 0) * 100:.1f}")

rows = []
for item in reports:
    metrics = item.get("metrics") or {}
    rows.append({
        "Clip": item.get("order"),
        "Scene": item.get("scene"),
        "Rating": item.get("rating"),
        "Score": item.get("scorePercent"),
        "Sharpness": round(float(metrics.get("sharpness", 0) or 0) * 100, 1),
        "Stability": round(float(metrics.get("stability", 0) or 0) * 100, 1),
        "Motion": round(float(metrics.get("motion", 0) or 0) * 100, 1),
        "Internal consistency": round(float(metrics.get("internalConsistency", 0) or 0) * 100, 1),
        "Reference consistency": round(float(metrics.get("referenceAppearanceConsistency", 0) or 0) * 100, 1),
        "Findings": len(item.get("findings") or []),
    })
st.subheader("Clip report")
st.dataframe(rows, use_container_width=True, hide_index=True)

candidates = [item for item in reports if not item.get("accepted")]
if not candidates:
    st.success("Every inspected clip passes the current visual-quality acceptance gate.")
    st.stop()

candidate_map = {_report_label(item): item for item in candidates}
selected_label = st.selectbox("Clip to repair", list(candidate_map))
candidate = candidate_map[selected_label]
st.warning(
    f"**Clip:** {candidate.get('order')}  \n"
    f"**Rating:** {candidate.get('rating')}  \n"
    f"**Score:** {float(candidate.get('scorePercent', 0) or 0):.1f}/100"
)
for finding in candidate.get("findings") or []:
    st.write(
        f"**{str(finding.get('severity') or '').title()} | {finding.get('code')}**  \n"
        f"{finding.get('message')}  \n"
        f"Repair: {finding.get('repair')}"
    )
with st.expander("Targeted repair directive", expanded=True):
    st.write(candidate.get("repairDirective") or "No additional repair direction was generated.")

paid_authorized = st.checkbox(
    "I authorize one additional paid Replicate prediction for this selected clip.",
    value=False,
)
left, right = st.columns(2)
with left:
    schedule_only = st.button("Schedule retake only", use_container_width=True)
with right:
    schedule_and_render = st.button(
        "Schedule and render one retake",
        type="primary",
        use_container_width=True,
        disabled=not paid_authorized,
    )

if schedule_only or schedule_and_render:
    try:
        scheduled = schedule_quality_retake(
            run_id,
            str(candidate.get("shotId") or ""),
            output_root="runs",
            reason=str(candidate.get("repairDirective") or ""),
        )
        st.success("The current clip was preserved and only the selected shot was reopened.")
        if schedule_and_render:
            queue = scheduled.get("queue") or {}
            config = queue.get("config") or {}
            with st.spinner("Rendering one targeted candidate through the saved production checkpoint..."):
                resume_video_run(
                    run_id,
                    output_root="runs",
                    batch_size=1,
                    continuous=False,
                    max_retries=int(config.get("max_retries_per_shot", 1) or 1),
                    max_provider_calls=int(config.get("max_provider_calls", 1) or 1),
                    use_continuity=True,
                )
                refreshed = inspect_run(run_id, output_root="runs")
                st.session_state["visual_quality_review"] = refreshed
            st.success("The retake completed and the production was re-inspected locally.")
    except (VisualQualityError, PipelineError, OSError, ValueError) as exc:
        st.error(str(exc))
