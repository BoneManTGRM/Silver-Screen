"""Silver-Screen long-running Reparodynamics AI film studio."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.health import health_report
from silver_screen.pipeline import (
    BriefValidationError,
    PipelineError,
    resume_video_run,
    run_pipeline,
)
from silver_screen.runtime import list_resumable_runs, list_runs
from silver_screen.science import FORMATS, GENRES, SCIENCE, TONES

st.set_page_config(
    page_title="Silver-Screen | Long-Running AI Film Studio",
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
    if data is None:
        return
    st.download_button(
        label,
        data,
        file_name=Path(path).name,
        mime=mime,
        key=key,
        use_container_width=True,
    )


def _progress(bar, message):
    def callback(stage: str, percent: int, text: str) -> None:
        bar.progress(percent, text=text)
        message.caption(f"{stage.replace('_', ' ').title()} | {percent}%")

    return callback


def _runtime(value: float) -> str:
    seconds = max(0, int(value))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def _run_label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    title = record.get("title") or brief.get("title") or "Untitled"
    return f"{record.get('runId')} | {record.get('status')} | {title}"


def _show_result_status(result: dict[str, Any]) -> None:
    state = result.get("state") or {}
    run_id = (result.get("run") or {}).get("id", "?")
    title = state.get("title") or "Untitled"
    status = str(result.get("status") or "unknown")
    if status == "complete":
        st.success(f"Completed: **{title}** | `{run_id}`")
    elif status == "partial":
        st.info(
            f"Checkpoint saved: **{title}** | `{run_id}`. "
            "Continue the same run to generate the next batch."
        )
    elif status == "blocked":
        st.warning(
            f"Production stopped at a repair or budget gate: **{title}** | "
            f"`{run_id}`. Verified footage remains saved."
        )
    else:
        st.error(f"Production status: {status} | `{run_id}`")


st.title("🎥 Silver-Screen")
st.caption(SCIENCE["credit"])
st.write(
    "A durable AI-film worker. Reparodynamics plans and stabilizes the production; "
    "TGRM detects failed clips, applies the smallest repair, verifies the MP4, "
    "records successful scars, and resumes until the runtime or budget target is met."
)

provider_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
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
    genre = st.selectbox(
        "Genre",
        list(GENRES),
        index=list(GENRES).index("scifi"),
        format_func=lambda value: "Sci-Fi" if value == "scifi" else value.title(),
    )
    tone = st.selectbox(
        "Tone",
        list(TONES),
        index=list(TONES).index("cinematic"),
        format_func=str.title,
    )
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
        "Target AI-film runtime (seconds)",
        min_value=4,
        max_value=5400,
        value=60,
        step=clip_duration,
        disabled=not ai_mode,
    )
    planned_shots = max(1, math.ceil(int(target_runtime) / clip_duration))
    if ai_mode:
        st.caption(
            f"Plan: approximately **{planned_shots} paid clips** at "
            f"{clip_duration}s each. Retries can increase paid calls."
        )
    batch_size = st.slider(
        "New clips per checkpoint",
        1,
        16,
        2,
        disabled=not ai_mode,
        help="Small batches are safer on hosted apps; accepted clips remain durable.",
    )
    max_retries = st.slider(
        "TGRM repair retries per clip",
        0,
        6,
        2,
        disabled=not ai_mode,
    )
    max_provider_calls = st.number_input(
        "Whole-production provider-call budget",
        min_value=0,
        max_value=10000,
        value=0,
        step=1,
        disabled=not ai_mode,
        help="0 derives the call budget from planned clips and retries.",
    )
    cost_per_second = st.number_input(
        "Provider cost per generated second (USD)",
        min_value=0.0,
        max_value=100.0,
        value=0.0,
        step=0.01,
        format="%.4f",
        disabled=not ai_mode,
        help="Operator-supplied value used only for the spend gate.",
    )
    max_spend = st.number_input(
        "Estimated maximum spend (USD)",
        min_value=0.0,
        max_value=100000.0,
        value=0.0,
        step=1.0,
        disabled=not ai_mode,
        help="0 disables estimated-spend gating.",
    )
    use_continuity = st.checkbox(
        "Chain verified final frames",
        value=True,
        disabled=not ai_mode,
    )
    continuous = st.checkbox(
        "Continue in this request until complete",
        value=False,
        disabled=not ai_mode,
        help="Checkpointed batches are safer when the hosting platform limits request duration.",
    )
    images = st.file_uploader(
        "Authorized reference image",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        help="The first image may initialize shot one. Later shots can use accepted final frames.",
    )

    with st.expander("Provider status", expanded=True):
        st.write(f"Replicate token: **{'configured' if provider_ready else 'missing'}**")
        st.write(
            f"Model: `{os.getenv('SILVER_SCREEN_VIDEO_MODEL', 'google/veo-3.1-fast')}`"
        )
        st.write(f"Clip duration: **{clip_duration}s**")
        if ai_mode and not provider_ready:
            st.error("Add REPLICATE_API_TOKEN to deployment secrets.")
        if ai_mode and max_spend > 0 and cost_per_second <= 0:
            st.warning("Enter cost per generated second to activate spend gating.")

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
    resume_continuous = st.checkbox(
        "Continue selected run until complete",
        value=False,
        disabled=not resume_map,
        key="resume-continuous",
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
            video_max_spend_usd=float(max_spend) if ai_mode else None,
            video_cost_per_second_usd=float(cost_per_second) if ai_mode else None,
            video_continuous=bool(continuous) if ai_mode else False,
            video_use_continuity=bool(use_continuity) if ai_mode else None,
            progress=_progress(bar, message),
        )
        st.session_state["silver_screen_result"] = result
        bar.progress(
            100 if result.get("status") == "complete" else 99,
            text="Production complete" if result.get("status") == "complete" else "Checkpoint saved",
        )
    except (BriefValidationError, PipelineError) as exc:
        st.error(str(exc))

if resume_clicked and resume_map:
    record = resume_map[selected_resume]
    run_id = str(record.get("runId"))
    bar = st.progress(72, text="Opening checkpoint")
    message = st.empty()
    try:
        result = resume_video_run(
            run_id,
            output_root="runs",
            batch_size=int(batch_size),
            continuous=bool(resume_continuous),
            max_retries=int(max_retries),
            max_provider_calls=int(max_provider_calls) if max_provider_calls else None,
            max_spend_usd=float(max_spend) if max_spend else None,
            cost_per_second_usd=float(cost_per_second) if cost_per_second else None,
            use_continuity=bool(use_continuity),
            progress=_progress(bar, message),
        )
        st.session_state["silver_screen_result"] = result
        bar.progress(
            100 if result.get("status") == "complete" else 99,
            text="Long-film target reached" if result.get("status") == "complete" else "Next checkpoint saved",
        )
    except PipelineError as exc:
        st.error(str(exc))

result = st.session_state.get("silver_screen_result")
if not result:
    st.info(
        "Start a new production or resume a checkpoint. Actual AI video uses paid provider calls."
    )
    st.stop()

state = result.get("state") or {}
media = result.get("media") or {}
artifacts = result.get("artifacts") or {}
narrative_metrics = result.get("metrics") or {}
video_metrics = media.get("metrics") or {}
video_msil = media.get("msil") or {}

_show_result_status(result)
columns = st.columns(7)
columns[0].metric("Narrative score", f"{float(narrative_metrics.get('finalScore', 0)):.3f}")
columns[1].metric(
    "Video clips",
    f"{video_metrics.get('verifiedShots', 0)}/{video_metrics.get('plannedShots', 0)}",
)
columns[2].metric("Verified runtime", _runtime(video_metrics.get("verifiedSeconds", 0) or 0))
columns[3].metric("Video RYE", f"{float(video_metrics.get('rye', 0) or 0):.4f}")
columns[4].metric("Provider calls", int(video_metrics.get("providerCalls", 0) or 0))
columns[5].metric("Repairs", int(video_metrics.get("repairs", 0) or 0))
columns[6].metric("Video MSIL", str(video_msil.get("verdict") or "n/a").upper())

video_tab, queue_tab, story_tab, screenplay_tab, audit_tab, files_tab = st.tabs(
    ["Video", "Production queue", "Story", "Screenplay", "TGRM audit", "Files"]
)

with video_tab:
    st.caption(media.get("note") or "No media note")
    if media.get("stopReason"):
        st.write(f"**Stop reason:** `{media['stopReason']}`")
    if media.get("error"):
        st.error(media["error"])
    final_video = media.get("final_video_path")
    partial_video = media.get("partial_video_path")
    if final_video:
        st.subheader("Verified final AI film")
        st.video(final_video)
        _download("Download final MP4", final_video, "video/mp4", "final-video")
    elif partial_video:
        st.subheader("Verified partial assembly")
        st.video(partial_video)
        _download("Download partial MP4", partial_video, "video/mp4", "partial-video")
    scene_paths = media.get("scene_paths") or media.get("video_paths") or []
    if scene_paths:
        with st.expander(f"Verified generated clips ({len(scene_paths)})"):
            for index, path in enumerate(scene_paths, start=1):
                st.markdown(f"**Shot {index}**")
                st.video(path)
                _download(f"Download shot {index}", path, "video/mp4", f"shot-{index}")
    if not final_video and not partial_video and not scene_paths:
        for path in media.get("card_paths") or []:
            st.image(path, use_container_width=True)

with queue_tab:
    queue = media.get("queue") or {}
    shots = [shot for shot in queue.get("shots") or [] if isinstance(shot, dict)]
    if shots:
        st.dataframe(
            [
                {
                    "Shot": shot.get("id"),
                    "Scene": (shot.get("sourceScene") or {}).get("number"),
                    "Chapter": (shot.get("sourceScene") or {}).get("chapter"),
                    "Segment": shot.get("segment"),
                    "Status": shot.get("status"),
                    "Attempts": shot.get("attempts"),
                    "Seconds": shot.get("verifiedDurationSeconds"),
                    "Continuity": shot.get("continuityUsed"),
                    "Prediction": shot.get("providerPredictionId"),
                    "Last error": shot.get("lastError"),
                }
                for shot in shots
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No AI-video queue exists for this run.")
    with st.expander("Queue state"):
        st.json(
            {
                "config": queue.get("config"),
                "metrics": queue.get("metrics"),
                "msil": queue.get("msil"),
                "artifacts": queue.get("artifacts"),
            }
        )

with story_tab:
    st.subheader(state.get("logline") or "Story")
    st.json(state.get("storyBible") or {})
    st.dataframe(
        [
            {
                "Scene": scene.get("number"),
                "Act": scene.get("act"),
                "Chapter": scene.get("chapter"),
                "Slugline": scene.get("slugline"),
                "Summary": scene.get("summary"),
            }
            for scene in state.get("scenes") or []
            if isinstance(scene, dict)
        ],
        use_container_width=True,
        hide_index=True,
    )

with screenplay_tab:
    script = str(state.get("script") or "")
    st.text_area("Screenplay", script, height=650, label_visibility="collapsed")
    st.download_button(
        "Download screenplay",
        script.encode(),
        file_name="screenplay.txt",
        mime="text/plain",
    )

with audit_tab:
    st.subheader("Narrative TGRM")
    st.json(
        {
            "metrics": result.get("metrics"),
            "msil": result.get("msil"),
            "repairLog": result.get("log"),
            "remainingFractures": result.get("remainingFractures"),
        }
    )
    st.subheader("Video Reparodynamics / TGRM")
    st.json(
        {
            "metrics": media.get("metrics"),
            "msil": media.get("msil"),
            "fractures": media.get("fractures"),
            "scarMemory": media.get("scars"),
            "providerPredictions": media.get("predictions"),
        }
    )

with files_tab:
    _download("Download complete bundle", artifacts.get("bundle"), "application/zip", "bundle")
    _download("Download film JSON", artifacts.get("film"), "application/json", "film")
    _download("Download TGRM JSON", artifacts.get("tgrm"), "application/json", "tgrm")
    _download("Download video queue", media.get("queue_path"), "application/json", "video-queue")
    _download("Download video runtime", media.get("runtime_path"), "application/json", "video-runtime")
    _download(
        "Download video scar memory",
        media.get("scar_memory_path"),
        "application/json",
        "video-scars",
    )
    st.download_button(
        "Download result JSON",
        json.dumps(result, indent=2, ensure_ascii=False, default=str).encode(),
        file_name="result.json",
        mime="application/json",
    )
