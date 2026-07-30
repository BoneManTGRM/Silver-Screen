"""Silver-Screen operational Streamlit studio."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.health import health_report
from silver_screen.pipeline import BriefValidationError, PipelineError, resume_video_run, run_pipeline
from silver_screen.production_dashboard import dashboard_metrics, display_msil, production_view, queue_rows
from silver_screen.provider_diagnostics import diagnose_provider_error, latest_video_error
from silver_screen.runtime import list_resumable_runs, list_runs
from silver_screen.science import FORMATS, GENRES, SCIENCE, TONES

st.set_page_config(
    page_title="Silver-Screen | AI Film Studio",
    page_icon="🎥",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _read(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _download(label: str, path: str | None, mime: str, key: str) -> None:
    data = _read(path)
    if data is not None:
        st.download_button(
            label,
            data,
            file_name=Path(path).name,
            mime=mime,
            key=key,
            use_container_width=True,
        )


def _runtime(value: object) -> str:
    seconds = max(0, int(float(value or 0)))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def _run_label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    title = record.get("title") or brief.get("title") or "Untitled"
    return f"{record.get('runId')} | {record.get('status')} | {title}"


def _progress(bar, message):
    def callback(stage: str, percent: int, text: str) -> None:
        bar.progress(max(0, min(100, percent)), text=text)
        message.caption(f"{stage.replace('_', ' ').title()} | {percent}%")

    return callback


def _show_provider_failure(media: dict[str, Any]) -> None:
    error = latest_video_error(media)
    if not error:
        return
    diagnosis = diagnose_provider_error(error)
    st.error(diagnosis.title)
    st.write(diagnosis.detail)
    with st.expander("Exact provider error", expanded=False):
        st.code(error)
    if diagnosis.retryable:
        st.info("Continue this saved production. Do not start a duplicate run.")


def _show_status(result: dict[str, Any]) -> None:
    state = result.get("state") or {}
    media = result.get("media") or {}
    title = state.get("title") or "Untitled"
    run_id = (result.get("run") or {}).get("id", "?")
    view = production_view(media)
    text = f"{view.headline}: **{title}** | `{run_id}`. {view.detail}"
    if view.severity == "success":
        st.success(text)
    elif view.severity == "warning":
        st.warning(text)
    else:
        st.info(text)
    if view.is_hard_block:
        _show_provider_failure(media)


st.title("🎥 Silver-Screen")
st.caption(SCIENCE["credit"])
st.write(
    "Generates real AI film clips, verifies every MP4, preserves accepted footage, "
    "and resumes the same production through bounded Reparodynamics and TGRM cycles."
)

provider_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
model_name = os.getenv("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3.1-fast")
clip_duration = int(os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8"))
if clip_duration not in {4, 6, 8}:
    clip_duration = 8

with st.sidebar:
    st.header("Production brief")
    title = st.text_input("Title", placeholder="Optional")
    premise = st.text_area(
        "Premise",
        "A repair technician discovers that every system she fixes remembers the pain of the break and begins to dream.",
        height=145,
        max_chars=4000,
    )
    genre = st.selectbox("Genre", list(GENRES), index=list(GENRES).index("scifi"))
    tone = st.selectbox("Tone", list(TONES), index=list(TONES).index("cinematic"))
    format_key = st.selectbox(
        "Story format",
        list(FORMATS),
        index=list(FORMATS).index("short"),
        format_func=lambda key: f"{FORMATS[key]['label']} | {FORMATS[key]['minutes']} min blueprint",
    )
    seed = st.text_input("Seed", placeholder="Derived automatically")

    st.divider()
    st.header("Film production")
    media_mode = st.selectbox(
        "Output type",
        ["ai-video", "cards", "preview", "preview-film", "off"],
        format_func=lambda value: {
            "ai-video": "Actual AI-generated video",
            "cards": "Static chapter cards",
            "preview": "Local card-animation clips",
            "preview-film": "Assembled local card preview",
            "off": "No media",
        }[value],
    )
    ai_mode = media_mode == "ai-video"
    target_runtime = st.number_input(
        "Target runtime (seconds)",
        min_value=4,
        max_value=5400,
        value=8,
        step=clip_duration,
        disabled=not ai_mode,
    )
    planned_shots = max(1, math.ceil(int(target_runtime) / clip_duration))
    if ai_mode:
        st.caption(f"Planned clips: **{planned_shots}** at approximately {clip_duration}s each.")
    batch_size = st.slider("New clips per checkpoint", 1, 16, 1, disabled=not ai_mode)
    max_retries = st.slider(
        "TGRM retries per clip",
        0,
        6,
        1,
        disabled=not ai_mode,
        help="Retries only the affected clip after a classified provider or verification failure.",
    )
    max_provider_calls = st.number_input(
        "Provider-call budget",
        min_value=0,
        max_value=10000,
        value=max(1, planned_shots) if ai_mode else 0,
        step=1,
        disabled=not ai_mode,
        help="This is a safety ceiling, not the number of clips generated in one checkpoint.",
    )
    use_continuity = st.checkbox("Chain verified final frames", value=True, disabled=not ai_mode)
    continuous = st.checkbox(
        "Continue in this request until complete",
        value=False,
        disabled=not ai_mode,
        help="Checkpoint mode is safer on hosted Streamlit deployments.",
    )
    images = st.file_uploader(
        "Authorized reference image",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
    )

    with st.expander("Provider status", expanded=True):
        st.write(f"Replicate token: **{'configured' if provider_ready else 'missing'}**")
        st.write(f"Model: `{model_name}`")
        st.write(f"Clip duration: **{clip_duration}s**")
        if ai_mode and not provider_ready:
            st.error("Add REPLICATE_API_TOKEN to Streamlit deployment secrets.")

    start_clicked = st.button(
        "Start production",
        type="primary",
        use_container_width=True,
        disabled=ai_mode and not provider_ready,
    )

    st.divider()
    st.header("Resume production")
    resumable = list_resumable_runs("runs", 30)
    resume_map = {_run_label(record): record for record in resumable}
    selected_resume = st.selectbox(
        "Checkpointed run",
        list(resume_map) or ["No resumable runs"],
        disabled=not resume_map,
    )
    resume_clicked = st.button(
        "Continue production",
        use_container_width=True,
        disabled=not resume_map or not provider_ready,
    )

    with st.expander("Runtime health"):
        st.json(health_report("runs"))
    with st.expander("Recent runs"):
        for record in list_runs("runs", 8):
            st.caption(_run_label(record))

if start_clicked:
    brief: dict[str, Any] = {
        "title": title,
        "premise": premise,
        "genre": genre,
        "tone": tone,
        "format": format_key,
    }
    if seed.strip():
        brief["seed"] = seed.strip()
    bar = st.progress(0, text="Starting production")
    message = st.empty()
    try:
        result = run_pipeline(
            brief,
            images=images,
            output_root="runs",
            persist=True,
            render_media=media_mode != "off",
            video_mode="cards" if media_mode == "off" else media_mode,
            max_chapters=4,
            target_runtime_seconds=int(target_runtime) if ai_mode else None,
            video_max_shots=planned_shots if ai_mode else None,
            video_batch_size=int(batch_size) if ai_mode else None,
            video_max_retries=int(max_retries) if ai_mode else None,
            video_max_provider_calls=int(max_provider_calls) if ai_mode else None,
            video_continuous=bool(continuous) if ai_mode else False,
            video_use_continuity=bool(use_continuity) if ai_mode else None,
            progress=_progress(bar, message),
        )
        st.session_state["silver_screen_result"] = result
        view = production_view(result.get("media") or {})
        bar.progress(view.progress_percent, text=view.headline)
    except (BriefValidationError, PipelineError) as exc:
        st.error(str(exc))

if resume_clicked and resume_map:
    record = resume_map[selected_resume]
    bar = st.progress(0, text="Opening checkpoint")
    message = st.empty()
    try:
        result = resume_video_run(
            str(record.get("runId")),
            output_root="runs",
            batch_size=int(batch_size),
            continuous=bool(continuous),
            max_retries=int(max_retries),
            max_provider_calls=int(max_provider_calls) if max_provider_calls else None,
            use_continuity=bool(use_continuity),
            progress=_progress(bar, message),
        )
        st.session_state["silver_screen_result"] = result
        view = production_view(result.get("media") or {})
        bar.progress(view.progress_percent, text=view.headline)
    except PipelineError as exc:
        st.error(str(exc))

result = st.session_state.get("silver_screen_result")
if not result:
    st.info("Start a production or resume a saved checkpoint.")
    st.stop()

state = result.get("state") or {}
media = result.get("media") or {}
artifacts = result.get("artifacts") or {}
narrative_metrics = result.get("metrics") or {}
summary = dashboard_metrics(media)
view = production_view(media)

_show_status(result)
st.progress(view.progress_percent, text=f"Film progress: {summary['verified']} of {summary['planned']} clips")

columns = st.columns(7)
columns[0].metric("Narrative score", f"{float(narrative_metrics.get('finalScore', 0)):.3f}")
columns[1].metric("Video clips", f"{summary['verified']}/{summary['planned']}")
columns[2].metric("Verified runtime", _runtime(summary["verifiedSeconds"]))
columns[3].metric("Remaining", _runtime(summary["remainingSeconds"]))
columns[4].metric("Continuity", f"{summary['continuityPercent']:.0f}%")
columns[5].metric("Provider calls", summary["providerCalls"])
columns[6].metric("Video state", display_msil(media))

video_tab, queue_tab, story_tab, audit_tab, files_tab = st.tabs(
    ["Video", "Production queue", "Story", "TGRM audit", "Files"]
)

with video_tab:
    st.caption(media.get("note") or view.detail)
    playable = media.get("final_video_path") or media.get("partial_video_path") or media.get("hero_path")
    if playable:
        st.video(playable)
        _download("Download assembled MP4", playable, "video/mp4", "assembled-video")
    for index, path in enumerate(media.get("video_paths") or [], start=1):
        st.markdown(f"**Verified clip {index}**")
        st.video(path)
        _download(f"Download clip {index}", path, "video/mp4", f"clip-{index}")
    if not playable and not media.get("video_paths"):
        st.warning("No verified MP4 is available yet.")

with queue_tab:
    rows = queue_rows(media)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No video queue exists for this run.")
    st.json({
        "stage": view.stage,
        "stopReason": media.get("stopReason"),
        "fractures": media.get("fractures"),
        "estimatedSpendUsd": summary["estimatedSpendUsd"],
    })

with story_tab:
    st.subheader(state.get("logline") or state.get("title") or "Story")
    st.json(state.get("storyBible") or {})
    st.text_area("Screenplay", str(state.get("script") or ""), height=500)

with audit_tab:
    st.json({
        "productionView": view.__dict__,
        "videoMetrics": media.get("metrics"),
        "videoMSIL": media.get("msil"),
        "videoScars": media.get("scars"),
        "providerPredictions": media.get("predictions"),
        "events": (media.get("queue") or {}).get("events"),
        "narrativeTGRM": result.get("log"),
    })

with files_tab:
    _download("Download complete bundle", artifacts.get("bundle"), "application/zip", "bundle")
    _download("Download film JSON", artifacts.get("film"), "application/json", "film")
    _download("Download TGRM JSON", artifacts.get("tgrm"), "application/json", "tgrm")
    _download("Download video queue", media.get("queue_path"), "application/json", "video-queue")
    _download("Download video runtime", media.get("runtime_path"), "application/json", "video-runtime")
    _download("Download video scar memory", media.get("scar_memory_path"), "application/json", "video-scars")
    st.download_button(
        "Download result JSON",
        json.dumps(result, indent=2, default=str).encode(),
        file_name="result.json",
        mime="application/json",
    )
