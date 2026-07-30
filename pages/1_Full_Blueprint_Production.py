"""Full-length blueprint production with explicit checkpoint and API controls."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.pipeline import BriefValidationError, PipelineError, resume_video_run, run_pipeline
from silver_screen.production_dashboard import production_view, queue_rows
from silver_screen.render_planning import (
    build_render_plan,
    recommended_provider_call_budget,
    requires_continuous_confirmation,
)
from silver_screen.runtime import list_resumable_runs
from silver_screen.science import FORMATS, GENRES, SCIENCE, TONES
from silver_screen.voice_providers import provider_capabilities

st.set_page_config(
    page_title="Silver-Screen | Full Blueprint Production",
    page_icon="🎬",
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


def _run_label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    title = brief.get("title") or "Untitled"
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
    if data is not None:
        st.download_button(
            label,
            data,
            file_name=Path(str(path)).name,
            mime="video/mp4",
            key=key,
            use_container_width=True,
        )


st.title("🎬 Full Blueprint Production")
st.caption(SCIENCE["credit"])
st.write(
    "This page keeps the story blueprint and the actual render plan synchronized. "
    "A 2-minute trailer blueprint becomes a 120-second production plan, not a single "
    "8-second finished film. Work is generated in durable checkpoints so the browser "
    "does not need to remain open for the entire production."
)

replicate_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
voice_caps = provider_capabilities()
video_model = os.getenv("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3.1-fast")
clip_duration = int(os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8") or 8)
if clip_duration not in {4, 6, 8}:
    clip_duration = 8

with st.sidebar:
    st.header("Blueprint")
    title = st.text_input("Title", value="Moonie Moo: Queen of the Spotlight")
    premise = st.text_area(
        "Premise",
        "A glamorous celebrity cow and her stubborn loyal bulldog discover that "
        "protecting each other matters more than protecting a perfect public image.",
        height=150,
        max_chars=4000,
    )
    genre_options = list(GENRES)
    genre_default = "comedy" if "comedy" in genre_options else "drama"
    genre = st.selectbox(
        "Genre", genre_options, index=genre_options.index(genre_default)
    )
    tone = st.selectbox(
        "Tone", list(TONES), index=list(TONES).index("cinematic")
    )
    format_key = st.selectbox(
        "Story format",
        list(FORMATS),
        index=list(FORMATS).index("trailer"),
        format_func=lambda key: (
            f"{FORMATS[key]['label']} | {FORMATS[key]['minutes']} min blueprint"
        ),
    )
    seed = st.text_input("Seed", placeholder="Derived automatically")

    with st.expander("Named cast", expanded=True):
        lead_name = st.text_input("Lead character", value="Moonie Moo")
        lead_role = st.text_input("Lead role", value="Glamorous celebrity cow")
        lead_description = st.text_area(
            "Lead appearance",
            "A fashionable black-and-white anthropomorphic cow with expressive eyes, "
            "red lipstick, elegant clothing, gold jewelry, and confident celebrity energy.",
            height=90,
        )
        support_name = st.text_input("Supporting character", value="Bully")
        support_role = st.text_input(
            "Supporting role", value="Loyal grumpy bulldog companion"
        )
        support_description = st.text_area(
            "Supporting appearance",
            "A compact blue-gray bulldog with a permanently unimpressed expression, "
            "comic timing, and fierce loyalty.",
            height=85,
        )

    st.divider()
    st.header("Render plan")
    render_mode_label = st.radio(
        "Render length",
        ["Match the blueprint", "One-clip preview", "Custom runtime"],
        index=0,
        help=(
            "Match the blueprint is the full planned movie length. A checkpoint may "
            "still generate only one or a few new clips per request."
        ),
    )
    render_mode = {
        "Match the blueprint": "match_blueprint",
        "One-clip preview": "preview",
        "Custom runtime": "custom",
    }[render_mode_label]
    custom_runtime = None
    if render_mode == "custom":
        custom_runtime = st.number_input(
            "Custom runtime (seconds)",
            min_value=4,
            max_value=5400,
            value=60,
            step=clip_duration,
        )

    plan = build_render_plan(
        format_key,
        mode=render_mode,
        custom_runtime_seconds=custom_runtime,
        clip_duration_seconds=clip_duration,
    )

    st.info(
        f"**{plan.format_label} blueprint:** {_runtime(plan.blueprint_minutes * 60)}  \n"
        f"**Planned render:** {_runtime(plan.runtime_seconds)}  \n"
        f"**Provider clips:** {plan.planned_clips} × about {plan.clip_duration_seconds}s"
    )
    if not plan.matches_blueprint:
        st.warning(
            "The selected render length does not match the complete blueprint. "
            "Use Match the blueprint for the full planned film."
        )

    batch_size = int(
        st.number_input(
            "New clips per checkpoint",
            min_value=1,
            max_value=max(1, min(16, plan.planned_clips)),
            value=1,
            step=1,
            help=(
                "This limits new paid video calls in one browser request. The complete "
                "production remains planned and resumable."
            ),
        )
    )
    retries = int(
        st.slider(
            "TGRM retries per clip",
            min_value=0,
            max_value=6,
            value=1,
            help="Only the affected clip is retried after a classified failure.",
        )
    )
    automatic_budget = st.checkbox(
        "Automatic whole-production call ceiling",
        value=True,
        help="Includes enough capacity for every planned clip and its allowed retries.",
    )
    recommended_budget = recommended_provider_call_budget(
        plan, retries_per_clip=retries, include_retry_capacity=True
    )
    if automatic_budget:
        provider_call_budget = recommended_budget
        st.caption(
            f"Maximum safety ceiling: **{provider_call_budget} calls**. "
            f"A checkpoint still creates at most **{batch_size} new clips**."
        )
    else:
        provider_call_budget = int(
            st.number_input(
                "Custom whole-production provider-call ceiling",
                min_value=1,
                max_value=10000,
                value=max(1, plan.planned_clips),
                step=1,
            )
        )

    use_continuity = st.checkbox("Chain verified final frames", value=True)
    continuous = st.checkbox(
        "Continue in this browser request until complete",
        value=False,
        help=(
            "Leave this off on hosted Streamlit deployments. Checkpoint mode is safer "
            "and preserves every accepted clip."
        ),
    )
    continuous_needs_confirmation = requires_continuous_confirmation(
        plan, continuous=continuous
    )
    continuous_confirmed = False
    if continuous_needs_confirmation:
        continuous_confirmed = st.checkbox(
            f"I authorize continuous work up to the {provider_call_budget}-call ceiling.",
            value=False,
        )

    images = st.file_uploader(
        "Authorized character/reference images",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        help=(
            "The first image initializes shot one. Verified final frames can carry "
            "identity and visual continuity into later clips."
        ),
    )

    with st.expander("API setup", expanded=True):
        st.write(
            f"Replicate video: **{'ready' if replicate_ready else 'missing'}** "
            f"(`REPLICATE_API_TOKEN`)"
        )
        st.write(f"Video model: `{video_model}`")
        st.write(
            f"OpenAI voices: **{'ready' if voice_caps.get('openai') else 'optional / missing'}** "
            f"(`OPENAI_API_KEY`)"
        )
        st.write(
            f"ElevenLabs voices: **{'ready' if voice_caps.get('elevenlabs') else 'optional / missing'}** "
            f"(`ELEVENLABS_API_KEY`)"
        )
        st.caption(
            "Replicate is required for generated video. For speech, choose OpenAI, "
            "ElevenLabs, or authorized finished audio tracks; you do not need both voice APIs. "
            "Script timing, TGRM, subtitles, FFmpeg assembly, and checkpointing do not require another API."
        )
        if not replicate_ready:
            st.error("Add REPLICATE_API_TOKEN to the Streamlit deployment secrets.")

    start_disabled = bool(
        not replicate_ready
        or len(premise.strip()) < 12
        or (continuous_needs_confirmation and not continuous_confirmed)
    )
    start_clicked = st.button(
        "Start blueprint production",
        type="primary",
        use_container_width=True,
        disabled=start_disabled,
    )

    st.divider()
    st.header("Continue a checkpoint")
    resumable = list_resumable_runs("runs", 50)
    resume_map = {_run_label(record): record for record in resumable}
    selected_resume = st.selectbox(
        "Saved production",
        list(resume_map) or ["No resumable productions"],
        disabled=not resume_map,
    )
    resume_batch = int(
        st.number_input(
            "New clips for this continuation",
            min_value=1,
            max_value=16,
            value=1,
            step=1,
            disabled=not resume_map,
        )
    )
    resume_clicked = st.button(
        "Continue selected production",
        use_container_width=True,
        disabled=not resume_map or not replicate_ready,
    )

if start_clicked:
    cast = []
    if lead_name.strip() and support_name.strip():
        cast = [
            {
                "name": lead_name.strip(),
                "role": lead_role.strip() or "Lead character",
                "description": lead_description.strip(),
            },
            {
                "name": support_name.strip(),
                "role": support_role.strip() or "Supporting character",
                "description": support_description.strip(),
            },
        ]
    brief: dict[str, Any] = {
        "title": title,
        "premise": premise,
        "genre": genre,
        "tone": tone,
        "format": format_key,
        "cast": cast,
    }
    if seed.strip():
        brief["seed"] = seed.strip()

    progress = st.progress(0, text="Opening production workspace")
    try:
        result = run_pipeline(
            brief,
            images=images,
            output_root="runs",
            persist=True,
            render_media=True,
            video_mode="ai-video",
            target_runtime_seconds=plan.runtime_seconds,
            video_max_shots=plan.planned_clips,
            video_batch_size=batch_size,
            video_max_retries=retries,
            video_max_provider_calls=provider_call_budget,
            video_continuous=continuous,
            video_use_continuity=use_continuity,
        )
        st.session_state["full_blueprint_result"] = result
        view = production_view(result.get("media") or {})
        progress.progress(view.progress_percent, text=view.headline)
    except (BriefValidationError, PipelineError) as exc:
        st.error(str(exc))

if resume_clicked and resume_map:
    record = resume_map[selected_resume]
    progress = st.progress(0, text="Opening saved checkpoint")
    try:
        result = resume_video_run(
            str(record.get("runId")),
            output_root="runs",
            batch_size=resume_batch,
            continuous=False,
        )
        st.session_state["full_blueprint_result"] = result
        view = production_view(result.get("media") or {})
        progress.progress(view.progress_percent, text=view.headline)
    except PipelineError as exc:
        st.error(str(exc))

result = st.session_state.get("full_blueprint_result")
if not result:
    st.info(
        "For a 2-minute Trailer blueprint, Match the blueprint creates a 120-second "
        "plan of 15 eight-second clips. With a one-clip checkpoint, the first request "
        "ends at 1/15 and is ready to continue; it no longer reports 1/1 complete."
    )
    st.stop()

media = result.get("media") or {}
metrics = media.get("metrics") or {}
view = production_view(media)
verified = int(metrics.get("verifiedShots", 0) or 0)
planned = int(metrics.get("plannedShots", 0) or 0)
verified_seconds = float(metrics.get("verifiedSeconds", 0) or 0)
planned_seconds = float(
    (media.get("queue") or {}).get("plannedRuntimeSeconds")
    or metrics.get("plannedRuntimeSeconds")
    or 0
)

if view.severity == "success":
    st.success(f"{view.headline}. {view.detail}")
elif view.severity == "warning":
    st.warning(f"{view.headline}. {view.detail}")
else:
    st.info(f"{view.headline}. {view.detail}")

st.progress(
    view.progress_percent,
    text=f"Verified {verified} of {planned} clips",
)
columns = st.columns(5)
columns[0].metric("Verified clips", f"{verified}/{planned}")
columns[1].metric("Verified runtime", _runtime(verified_seconds))
columns[2].metric(
    "Remaining runtime", _runtime(max(0.0, planned_seconds - verified_seconds))
)
columns[3].metric("Provider calls", metrics.get("providerCalls", 0))
columns[4].metric("State", str((media.get("msil") or {}).get("verdict") or "planning").upper())

video_tab, queue_tab = st.tabs(["Video", "Production queue"])
with video_tab:
    playable = (
        media.get("final_video_path")
        or media.get("partial_video_path")
        or media.get("hero_path")
    )
    if playable:
        st.video(playable)
        _download("Download assembled checkpoint MP4", playable, "blueprint-video")
    for index, path in enumerate(media.get("video_paths") or [], start=1):
        st.markdown(f"**Verified clip {index}**")
        st.video(path)
    if not playable and not media.get("video_paths"):
        st.warning("No verified MP4 is available yet.")

with queue_tab:
    rows = queue_rows(media)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("The production queue has not been created yet.")
