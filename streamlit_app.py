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
        st.error("No verified video was produced and the provider returned no useful detail.")
        return
    diagnosis = diagnose_provider_error(error)
    st.error(diagnosis.title)
    st.write(diagnosis.detail)
    with st.expander("Exact provider error", expanded=True):
        st.code(error)
    if diagnosis.retryable:
        st.info("Continue this same saved production. Do not start a second run.")
    elif diagnosis.code == "billing_required":
        st.warning(
            "Replicate can offer free playground predictions on selected models while Veo API calls still require billing or usable credits. This cannot be bypassed in Silver-Screen."
        )


def _show_status(result: dict[str, Any]) -> None:
    state = result.get("state") or {}
    media = result.get("media") or {}
    metrics = media.get("metrics") or {}
    verified = int(metrics.get("verifiedShots", 0) or 0)
    status = str(result.get("status") or media.get("status") or "unknown")
    title = state.get("title") or "Untitled"
    run_id = (result.get("run") or {}).get("id", "?")

    if status == "complete" and verified > 0:
        st.success(f"Completed: **{title}** | `{run_id}`")
    elif status == "partial" and verified > 0:
        st.info(
            f"Checkpoint saved with {verified} verified clip(s): **{title}** | `{run_id}`. Continue this run for the next batch."
        )
    elif verified == 0 and str(media.get("mode")) == "ai-video":
        st.error(f"No video clip was verified for **{title}** | `{run_id}`.")
        _show_provider_failure(media)
    elif status == "blocked":
        st.warning(f"Production is blocked: **{title}** | `{run_id}`. Existing verified footage remains saved.")
        _show_provider_failure(media)
    else:
        st.info(f"Production status: **{status}** | `{run_id}`")


st.title("🎥 Silver-Screen")
st.caption(SCIENCE["credit"])
st.write(
    "Creates real AI-generated film clips through Replicate, verifies each MP4, saves durable checkpoints, and resumes the same production without discarding accepted footage."
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
        help="Use 8 seconds for the first provider test.",
    )
    planned_shots = max(1, math.ceil(int(target_runtime) / clip_duration))
    if ai_mode:
        st.caption(f"Planned clips: **{planned_shots}**. Start with one clip until provider access is confirmed.")
    batch_size = st.slider("New clips per checkpoint", 1, 16, 1, disabled=not ai_mode)
    max_retries = st.slider(
        "TGRM retries per clip",
        0,
        6,
        0,
        disabled=not ai_mode,
        help="Keep this at 0 for the first test. Configuration and billing errors should not be retried.",
    )
    max_provider_calls = st.number_input(
        "Provider-call budget",
        min_value=0,
        max_value=10000,
        value=1 if ai_mode else 0,
        step=1,
        disabled=not ai_mode,
        help="Use 1 for the first test to prevent duplicate paid attempts.",
    )
    use_continuity = st.checkbox("Chain verified final frames", value=True, disabled=not ai_mode)
    continuous = st.checkbox(
        "Continue in this request until complete",
        value=False,
        disabled=not ai_mode,
        help="Leave off on Streamlit hosting. Resume from checkpoints instead.",
    )
    images = st.file_uploader(
        "Authorized reference image",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        help="Optional. For the first diagnostic run, try without an image if Replicate reports invalid input.",
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
        verified = int(((result.get("media") or {}).get("metrics") or {}).get("verifiedShots", 0) or 0)
        bar.progress(100 if verified else 99, text="Production complete" if verified else "Provider response recorded")
    except (BriefValidationError, PipelineError) as exc:
        st.error(str(exc))

if resume_clicked and resume_map:
    record = resume_map[selected_resume]
    bar = st.progress(72, text="Opening checkpoint")
    message = st.empty()
    try:
        result = resume_video_run(
            str(record.get("runId")),
            output_root="runs",
            batch_size=int(batch_size),
            continuous=False,
            max_retries=int(max_retries),
            max_provider_calls=int(max_provider_calls) if max_provider_calls else None,
            use_continuity=bool(use_continuity),
            progress=_progress(bar, message),
        )
        st.session_state["silver_screen_result"] = result
        bar.progress(100, text="Checkpoint processed")
    except PipelineError as exc:
        st.error(str(exc))

result = st.session_state.get("silver_screen_result")
if not result:
    st.info("Start with an 8-second, one-call test. A free Replicate playground run does not necessarily include Veo API access.")
    st.stop()

state = result.get("state") or {}
media = result.get("media") or {}
artifacts = result.get("artifacts") or {}
narrative_metrics = result.get("metrics") or {}
video_metrics = media.get("metrics") or {}
video_msil = media.get("msil") or {}

_show_status(result)
columns = st.columns(6)
columns[0].metric("Narrative score", f"{float(narrative_metrics.get('finalScore', 0)):.3f}")
columns[1].metric("Video clips", f"{video_metrics.get('verifiedShots', 0)}/{video_metrics.get('plannedShots', 0)}")
columns[2].metric("Verified runtime", _runtime(video_metrics.get("verifiedSeconds", 0)))
columns[3].metric("Provider calls", video_metrics.get("providerCalls", 0))
columns[4].metric("Repairs", video_metrics.get("repairs", 0))
columns[5].metric("Video MSIL", str(video_msil.get("verdict") or "not evaluated").upper())

video_tab, queue_tab, story_tab, audit_tab, files_tab = st.tabs(
    ["Video", "Production queue", "Story", "TGRM audit", "Files"]
)

with video_tab:
    st.caption(media.get("note") or "No media note")
    playable = media.get("final_video_path") or media.get("partial_video_path") or media.get("hero_path")
    if playable:
        st.video(playable)
        _download("Download assembled MP4", playable, "video/mp4", "assembled-video")
    for index, path in enumerate(media.get("video_paths") or [], start=1):
        st.markdown(f"**Verified clip {index}**")
        st.video(path)
        _download(f"Download clip {index}", path, "video/mp4", f"clip-{index}")
    if not playable and not media.get("video_paths"):
        st.warning("No verified MP4 is available for this production.")

with queue_tab:
    queue = media.get("queue") or {}
    shots = queue.get("shots") or []
    if shots:
        st.dataframe(
            [
                {
                    "Shot": shot.get("id"),
                    "Status": shot.get("status"),
                    "Attempts": shot.get("attempts"),
                    "Provider": shot.get("providerStatus"),
                    "Error": shot.get("lastError"),
                }
                for shot in shots
                if isinstance(shot, dict)
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.json({"stopReason": media.get("stopReason"), "fractures": media.get("fractures")})

with story_tab:
    st.subheader(state.get("logline") or state.get("title") or "Story")
    st.json(state.get("storyBible") or {})
    st.text_area("Screenplay", str(state.get("script") or ""), height=500)

with audit_tab:
    st.json(
        {
            "videoMetrics": video_metrics,
            "videoMSIL": video_msil,
            "videoScars": media.get("scars"),
            "providerPredictions": media.get("predictions"),
            "events": (media.get("queue") or {}).get("events"),
            "narrativeTGRM": result.get("log"),
        }
    )

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
