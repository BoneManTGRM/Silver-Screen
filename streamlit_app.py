"""Silver-Screen operational Streamlit studio with real AI video generation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.health import health_report
from silver_screen.pipeline import BriefValidationError, PipelineError, run_pipeline
from silver_screen.runtime import list_runs
from silver_screen.science import FORMATS, GENRES, SCIENCE, TONES

st.set_page_config(page_title="Silver-Screen | AI Film Studio", page_icon="🎥", layout="wide")


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
        st.download_button(label, data, file_name=Path(path).name, mime=mime, key=key, use_container_width=True)


def _progress(bar, message):
    def callback(stage: str, percent: int, text: str) -> None:
        bar.progress(percent, text=text)
        message.caption(f"{stage.replace('_', ' ').title()} | {percent}%")

    return callback


st.title("🎥 Silver-Screen")
st.caption(SCIENCE["credit"])
st.write(
    "Builds a repaired screenplay and can now call a real generative-video provider. "
    "Static cards and local card animations are labeled separately and are never presented as AI footage."
)

with st.sidebar:
    st.header("Production brief")
    title = st.text_input("Title", placeholder="Optional")
    premise = st.text_area(
        "Premise",
        "A repair technician discovers that every system she fixes remembers the pain of the break and begins to dream.",
        height=150,
        max_chars=4000,
    )
    genre = st.selectbox("Genre", list(GENRES), index=list(GENRES).index("scifi"))
    tone = st.selectbox("Tone", list(TONES), index=list(TONES).index("cinematic"))
    format_key = st.selectbox(
        "Format",
        list(FORMATS),
        index=list(FORMATS).index("short"),
        format_func=lambda key: f"{FORMATS[key]['label']} | {FORMATS[key]['minutes']} min blueprint",
    )
    seed = st.text_input("Seed", placeholder="Derived automatically")

    st.subheader("Video output")
    media_mode = st.selectbox(
        "Output type",
        ["ai-video", "cards", "preview", "preview-film", "off"],
        format_func=lambda value: {
            "ai-video": "Actual AI-generated video (Replicate)",
            "cards": "Static chapter cards only",
            "preview": "Local card-animation previews",
            "preview-film": "Assembled local card-animation preview",
            "off": "No media",
        }[value],
    )
    max_scenes = st.slider(
        "AI scenes / preview chapters",
        1,
        8,
        2,
        help="Each AI scene is a separate paid model generation. Start with one or two scenes.",
    )
    portraits = st.file_uploader(
        "Optional portraits for static cards",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
    )

    with st.expander("Provider status", expanded=True):
        provider_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
        st.write(f"Replicate token: **{'configured' if provider_ready else 'missing'}**")
        st.write(f"Model: `{os.getenv('SILVER_SCREEN_VIDEO_MODEL', 'google/veo-3-fast')}`")
        if media_mode == "ai-video" and not provider_ready:
            st.error("Add REPLICATE_API_TOKEN to the deployment secrets. AI video cannot run without it.")

    run_clicked = st.button(
        "Generate production",
        type="primary",
        use_container_width=True,
        disabled=media_mode == "ai-video" and not provider_ready,
    )

    with st.expander("Runtime health"):
        st.json(health_report("runs"))
    with st.expander("Recent runs"):
        for record in list_runs("runs", 8):
            st.caption(f"{record.get('runId')} | {record.get('status')} | {(record.get('brief') or {}).get('title') or 'Untitled'}")

if run_clicked:
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
            images=portraits,
            output_root="runs",
            persist=True,
            render_media=media_mode != "off",
            video_mode="cards" if media_mode == "off" else media_mode,
            max_chapters=max_scenes,
            progress=_progress(bar, message),
        )
        media = result.get("media") or {}
        if media_mode == "ai-video" and not media.get("final_video_path"):
            raise PipelineError(media.get("error") or "AI video was requested, but no final MP4 was produced")
        st.session_state["silver_screen_result"] = result
        bar.progress(100, text="Production complete")
    except (BriefValidationError, PipelineError) as exc:
        st.error(str(exc))

result = st.session_state.get("silver_screen_result")
if not result:
    st.info("Choose the output type and generate a production. Actual AI video requires a Replicate API token and incurs provider charges.")
    st.stop()

state = result.get("state") or {}
media = result.get("media") or {}
artifacts = result.get("artifacts") or {}
metrics = result.get("metrics") or {}

st.success(f"Completed: **{state.get('title', 'Untitled')}** | `{(result.get('run') or {}).get('id', '?')}`")
columns = st.columns(5)
columns[0].metric("Final score", f"{float(metrics.get('finalScore', 0)):.3f}")
columns[1].metric("RYE", f"{float(metrics.get('rye', 0)):.4f}")
columns[2].metric("Scenes", len(state.get("scenes") or []))
columns[3].metric("Generated clips", len(media.get("video_paths") or []))
columns[4].metric("Media mode", str(media.get("mode") or "off"))

video_tab, story_tab, screenplay_tab, audit_tab, files_tab = st.tabs(
    ["Video", "Story", "Screenplay", "TGRM audit", "Files"]
)

with video_tab:
    st.caption(media.get("note") or "No media note")
    if media.get("error"):
        st.error(media["error"])
    final_video = media.get("final_video_path") or media.get("hero_path")
    if final_video:
        label = "AI-generated final video" if media.get("mode") == "ai-video" else "Local preview film"
        st.subheader(label)
        st.video(final_video)
        _download("Download final MP4", final_video, "video/mp4", "final-video")
    paths = media.get("video_paths") or []
    if paths:
        st.subheader("Generated scene clips" if media.get("mode") == "ai-video" else "Preview clips")
        for index, path in enumerate(paths, start=1):
            st.markdown(f"**Scene {index}**")
            st.video(path)
            _download(f"Download scene {index}", path, "video/mp4", f"scene-{index}")
    if not final_video and not paths:
        for path in media.get("card_paths") or []:
            st.image(path, use_container_width=True)

with story_tab:
    st.subheader(state.get("logline") or "Story")
    st.json(state.get("storyBible") or {})
    st.dataframe(
        [
            {
                "Scene": scene.get("number"),
                "Act": scene.get("act"),
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
    st.download_button("Download screenplay", script.encode(), file_name="screenplay.txt", mime="text/plain")

with audit_tab:
    st.json(
        {
            "metrics": result.get("metrics"),
            "msil": result.get("msil"),
            "repairLog": result.get("log"),
            "remainingFractures": result.get("remainingFractures"),
            "providerPredictions": media.get("predictions"),
        }
    )

with files_tab:
    _download("Download complete bundle", artifacts.get("bundle"), "application/zip", "bundle")
    _download("Download film JSON", artifacts.get("film"), "application/json", "film")
    _download("Download TGRM JSON", artifacts.get("tgrm"), "application/json", "tgrm")
    st.download_button(
        "Download result JSON",
        json.dumps(result, indent=2, default=str).encode(),
        file_name="result.json",
        mime="application/json",
    )
