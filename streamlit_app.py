"""Silver-Screen AI film and authorized voice studio."""
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
from silver_screen.production_dashboard import (
    dashboard_metrics,
    display_msil,
    production_view,
    queue_rows,
)
from silver_screen.provider_diagnostics import (
    diagnose_provider_error,
    latest_video_error,
)
from silver_screen.runtime import list_resumable_runs, list_runs
from silver_screen.science import FORMATS, GENRES, SCIENCE, TONES
from silver_screen.voice_providers import (
    OPENAI_BUILTIN_VOICES,
    diagnose_voice_error,
    provider_capabilities,
)
from silver_screen.voice_studio import VoiceStudioError, attach_voice_to_run

st.set_page_config(
    page_title="Silver-Screen | AI Film & Voice Studio",
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
    with st.expander("Exact video-provider error", expanded=False):
        st.code(error)
    if diagnosis.retryable:
        st.info("Continue this saved production. Do not start a duplicate run.")


def _show_voice_failure(voice: dict[str, Any]) -> None:
    error = str(voice.get("error") or "")
    if not error:
        return
    diagnosis = diagnose_voice_error(error)
    st.error(diagnosis.title)
    st.write(diagnosis.detail)
    with st.expander("Exact voice-provider error", expanded=False):
        st.code(error)
    if diagnosis.retryable:
        st.info(
            "Retry the same saved voice production; verified video remains intact."
        )


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
    voice = media.get("voice") or result.get("voice") or {}
    if voice.get("status") == "blocked":
        _show_voice_failure(voice)


def _voice_request(
    enabled: bool,
    provider: str,
    mode: str,
    lead_voice: str,
    supporting_voice: str,
    narrator_voice: str,
    instructions: str,
    speed: float,
    retries: int,
    preserve_source_audio: bool,
    subtitles: bool,
    authorization: bool,
    custom_voice: bool,
    custom_name: str,
    voice_sample: Any,
    consent_recording: Any,
    manual_tracks: list[Any],
) -> dict[str, Any]:
    model = ""
    if provider == "openai":
        model = os.getenv(
            "SILVER_SCREEN_OPENAI_TTS_MODEL", "gpt-4o-mini-tts"
        )
    elif provider == "elevenlabs":
        model = os.getenv(
            "SILVER_SCREEN_ELEVENLABS_MODEL", "eleven_multilingual_v2"
        )
    return {
        "enabled": enabled,
        "provider": provider,
        "mode": mode,
        "model": model,
        "lead_voice": lead_voice,
        "supporting_voice": supporting_voice,
        "narrator_voice": narrator_voice,
        "instructions": instructions,
        "speed": speed,
        "max_retries_per_line": retries,
        "preserve_source_audio": preserve_source_audio,
        "subtitles": subtitles,
        "authorization_confirmed": authorization,
        "custom_voice": custom_voice,
        "custom_voice_name": custom_name,
        "voice_sample": voice_sample,
        "consent_recording": consent_recording,
        "manual_tracks": manual_tracks,
    }


st.title("🎥 Silver-Screen")
st.caption(SCIENCE["credit"])
st.write(
    "Generates real AI film clips, preserves accepted footage, and can add "
    "authorized character voices, narration, subtitles, and a final dubbed MP4 "
    "through resumable TGRM production."
)

provider_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
model_name = os.getenv("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3.1-fast")
clip_duration = int(os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8"))
if clip_duration not in {4, 6, 8}:
    clip_duration = 8
voice_caps = provider_capabilities()

with st.sidebar:
    st.header("Production brief")
    title = st.text_input("Title", value="Moonie Moo: Queen of the Spotlight")
    premise = st.text_area(
        "Premise",
        "A glamorous celebrity cow and her stubborn loyal bulldog discover that "
        "protecting each other matters more than protecting a perfect public image.",
        height=145,
        max_chars=4000,
    )
    genre_options = list(GENRES)
    default_genre = "comedy" if "comedy" in genre_options else "drama"
    genre = st.selectbox(
        "Genre", genre_options, index=genre_options.index(default_genre)
    )
    tone = st.selectbox(
        "Tone", list(TONES), index=list(TONES).index("cinematic")
    )
    format_key = st.selectbox(
        "Story format",
        list(FORMATS),
        index=list(FORMATS).index("short"),
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
            height=80,
        )

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
        st.caption(
            f"Planned clips: **{planned_shots}** at approximately {clip_duration}s each."
        )
    batch_size = st.slider(
        "New clips per checkpoint", 1, 16, 1, disabled=not ai_mode
    )
    max_retries = st.slider(
        "Video TGRM retries per clip",
        0,
        6,
        1,
        disabled=not ai_mode,
        help="Retries only the affected clip after a classified failure.",
    )
    max_provider_calls = st.number_input(
        "Video provider-call budget",
        min_value=0,
        max_value=10000,
        value=max(1, planned_shots) if ai_mode else 0,
        step=1,
        disabled=not ai_mode,
        help="Safety ceiling for the whole video production.",
    )
    use_continuity = st.checkbox(
        "Chain verified final frames", value=True, disabled=not ai_mode
    )
    continuous = st.checkbox(
        "Continue in this request until complete",
        value=False,
        disabled=not ai_mode,
        help="Checkpoint mode is safer on hosted Streamlit deployments.",
    )
    images = st.file_uploader(
        "Authorized character/reference images",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        help="The first image initializes the first shot; accepted final frames carry continuity forward.",
    )

    st.divider()
    st.header("Voice Studio")
    enable_voices = st.checkbox(
        "Add voices and narration", value=False, disabled=not ai_mode
    )
    voice_provider_label = st.selectbox(
        "Voice source",
        [
            "OpenAI built-in/custom voice",
            "ElevenLabs voice IDs",
            "Finished audio tracks",
        ],
        disabled=not enable_voices,
    )
    voice_provider = {
        "OpenAI built-in/custom voice": "openai",
        "ElevenLabs voice IDs": "elevenlabs",
        "Finished audio tracks": "manual",
    }[voice_provider_label]
    voice_mode_label = st.selectbox(
        "Spoken content",
        ["Dialogue and narration", "Dialogue only", "Narration only"],
        disabled=not enable_voices,
    )
    voice_mode = {
        "Dialogue and narration": "dialogue+narration",
        "Dialogue only": "dialogue",
        "Narration only": "narration",
    }[voice_mode_label]

    if voice_provider == "openai":
        lead_voice = st.selectbox(
            "Lead voice",
            list(OPENAI_BUILTIN_VOICES),
            index=list(OPENAI_BUILTIN_VOICES).index("coral"),
            disabled=not enable_voices,
        )
        supporting_voice = st.selectbox(
            "Supporting voice",
            list(OPENAI_BUILTIN_VOICES),
            index=list(OPENAI_BUILTIN_VOICES).index("onyx"),
            disabled=not enable_voices,
        )
        narrator_voice = st.selectbox(
            "Narrator voice",
            list(OPENAI_BUILTIN_VOICES),
            index=list(OPENAI_BUILTIN_VOICES).index("cedar"),
            disabled=not enable_voices,
        )
    elif voice_provider == "elevenlabs":
        lead_voice = st.text_input(
            "Lead ElevenLabs voice ID", disabled=not enable_voices
        )
        supporting_voice = st.text_input(
            "Supporting ElevenLabs voice ID", disabled=not enable_voices
        )
        narrator_voice = st.text_input(
            "Narrator ElevenLabs voice ID", disabled=not enable_voices
        )
    else:
        lead_voice = supporting_voice = narrator_voice = "manual"

    voice_instructions = st.text_area(
        "Performance direction",
        "Expressive animated feature-film performance, warm comic timing, clear "
        "diction, and emotionally natural delivery.",
        height=100,
        disabled=not enable_voices or voice_provider == "manual",
    )
    voice_speed = st.slider(
        "Voice speed",
        0.7,
        1.3,
        1.0,
        0.05,
        disabled=not enable_voices or voice_provider == "manual",
    )
    voice_retries = st.slider(
        "Voice TGRM retries per line",
        0,
        4,
        1,
        disabled=not enable_voices or voice_provider == "manual",
    )
    preserve_source_audio = st.checkbox(
        "Keep quiet original scene audio under speech",
        value=False,
        disabled=not enable_voices,
    )
    create_subtitles = st.checkbox(
        "Export SRT subtitles", value=True, disabled=not enable_voices
    )

    custom_voice = False
    custom_name = "Silver Screen Voice"
    voice_sample = None
    consent_recording = None
    manual_tracks: list[Any] = []
    authorization = False

    if enable_voices and voice_provider == "openai":
        with st.expander("Optional authorized custom voice", expanded=False):
            custom_voice = st.checkbox("Enroll a custom OpenAI voice")
            custom_name = st.text_input(
                "Custom voice name",
                value="Moonie Moo Voice",
                disabled=not custom_voice,
            )
            voice_sample = st.file_uploader(
                "Authorized voice sample",
                type=["wav", "mp3", "m4a", "ogg", "webm"],
                disabled=not custom_voice,
                key="custom-voice-sample",
            )
            consent_recording = st.file_uploader(
                "Required spoken consent recording",
                type=["wav", "mp3", "m4a", "ogg", "webm"],
                disabled=not custom_voice,
                key="custom-voice-consent",
            )
            st.caption(
                "Custom voice creation requires the provider-required consent statement "
                "and a sample you are legally authorized to use."
            )

    if enable_voices and voice_provider == "manual":
        manual_tracks = (
            st.file_uploader(
                "Finished voice tracks, in clip order",
                type=["wav", "mp3", "m4a", "aac", "ogg", "flac", "webm"],
                accept_multiple_files=True,
                key="manual-voice-tracks",
            )
            or []
        )
        st.caption(
            "Upload one finished spoken track per verified video clip. Tracks are "
            "mixed locally and are not cloned."
        )

    authorization_required = enable_voices and (
        custom_voice or voice_provider in {"manual", "elevenlabs"}
    )
    if authorization_required:
        authorization = st.checkbox(
            "I confirm I own or have explicit permission and consent to use these voices or recordings.",
            value=False,
        )

    manual_ready = voice_provider != "manual" or bool(manual_tracks)
    custom_ready = (
        not custom_voice or (voice_sample is not None and consent_recording is not None)
    )
    selected_provider_ready = (
        voice_provider == "manual" or bool(voice_caps.get(voice_provider))
    )
    voice_provider_ready = (
        not enable_voices
        or (selected_provider_ready and manual_ready and custom_ready)
    )
    invalid_authorization = authorization_required and not authorization

    with st.expander("Provider status", expanded=True):
        st.write(
            f"Replicate token: **{'configured' if provider_ready else 'missing'}**"
        )
        st.write(f"Model: `{model_name}` | clip: **{clip_duration}s**")
        if enable_voices:
            st.write(
                f"Voice provider: **{voice_provider}** | "
                f"**{'ready' if voice_provider_ready else 'not ready'}**"
            )
            if voice_provider == "openai":
                st.write(
                    "Speech model: "
                    f"`{os.getenv('SILVER_SCREEN_OPENAI_TTS_MODEL', 'gpt-4o-mini-tts')}`"
                )
            elif voice_provider == "elevenlabs":
                st.write(
                    "Speech model: "
                    f"`{os.getenv('SILVER_SCREEN_ELEVENLABS_MODEL', 'eleven_multilingual_v2')}`"
                )
        if ai_mode and not provider_ready:
            st.error("Add REPLICATE_API_TOKEN to Streamlit deployment secrets.")
        if enable_voices and not selected_provider_ready:
            st.error(
                "Add the selected voice-provider API key to Streamlit secrets, "
                "or use finished audio tracks."
            )
        if enable_voices and voice_provider == "manual" and not manual_tracks:
            st.error("Upload at least one finished audio track.")
        if enable_voices and custom_voice and not custom_ready:
            st.error("Upload both the authorized sample and consent recording.")
        if invalid_authorization:
            st.error("Confirm voice authorization before starting or attaching voices.")

    voice_request = _voice_request(
        enable_voices,
        voice_provider,
        voice_mode,
        lead_voice,
        supporting_voice,
        narrator_voice,
        voice_instructions,
        float(voice_speed),
        int(voice_retries),
        preserve_source_audio,
        create_subtitles,
        authorization,
        custom_voice,
        custom_name,
        voice_sample,
        consent_recording,
        manual_tracks,
    )

    start_disabled = bool(
        ai_mode
        and (
            not provider_ready
            or not voice_provider_ready
            or invalid_authorization
        )
    )
    start_clicked = st.button(
        "Start production",
        type="primary",
        use_container_width=True,
        disabled=start_disabled,
    )

    st.divider()
    st.header("Resume video production")
    resumable = list_resumable_runs("runs", 30)
    resume_map = {_run_label(record): record for record in resumable}
    selected_resume = st.selectbox(
        "Checkpointed video run",
        list(resume_map) or ["No resumable video runs"],
        disabled=not resume_map,
        key="video-resume-run",
    )
    resume_clicked = st.button(
        "Continue video production",
        use_container_width=True,
        disabled=not resume_map or not provider_ready,
    )

    st.header("Attach voices to a saved film")
    all_runs = list_runs("runs", 50)
    voice_run_map = {_run_label(record): record for record in all_runs}
    selected_voice_run = st.selectbox(
        "Saved film run",
        list(voice_run_map) or ["No saved runs"],
        disabled=not voice_run_map,
        key="voice-attach-run",
    )
    voice_clicked = st.button(
        "Attach or update voices",
        use_container_width=True,
        disabled=(
            not voice_run_map
            or not enable_voices
            or not voice_provider_ready
            or invalid_authorization
        ),
    )

    with st.expander("Runtime health"):
        st.json(health_report("runs"))
    with st.expander("Recent runs"):
        for record in list_runs("runs", 8):
            st.caption(_run_label(record))

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
    bar = st.progress(0, text="Starting production")
    message = st.empty()
    try:
        result = run_pipeline(
            brief,
            images=images,
            voices=[voice_request] if enable_voices else None,
            output_root="runs",
            persist=True,
            render_media=media_mode != "off",
            video_mode="cards" if media_mode == "off" else media_mode,
            max_chapters=4,
            target_runtime_seconds=int(target_runtime) if ai_mode else None,
            video_max_shots=planned_shots if ai_mode else None,
            video_batch_size=int(batch_size) if ai_mode else None,
            video_max_retries=int(max_retries) if ai_mode else None,
            video_max_provider_calls=(
                int(max_provider_calls) if ai_mode else None
            ),
            video_continuous=bool(continuous) if ai_mode else False,
            video_use_continuity=bool(use_continuity) if ai_mode else None,
            progress=_progress(bar, message),
        )
        st.session_state["silver_screen_result"] = result
        view = production_view(result.get("media") or {})
        bar.progress(view.progress_percent, text=view.headline)
    except (BriefValidationError, PipelineError, VoiceStudioError) as exc:
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
            max_provider_calls=(
                int(max_provider_calls) if max_provider_calls else None
            ),
            use_continuity=bool(use_continuity),
            progress=_progress(bar, message),
        )
        st.session_state["silver_screen_result"] = result
        view = production_view(result.get("media") or {})
        bar.progress(view.progress_percent, text=view.headline)
    except PipelineError as exc:
        st.error(str(exc))

if voice_clicked and voice_run_map:
    record = voice_run_map[selected_voice_run]
    try:
        with st.spinner(
            "Generating authorized speech and assembling the voiced film..."
        ):
            result = attach_voice_to_run(
                str(record.get("runId")), [voice_request], output_root="runs"
            )
        st.session_state["silver_screen_result"] = result
        st.success(
            "Voice checkpoint saved. The original verified video remains available separately."
        )
    except (VoiceStudioError, PipelineError) as exc:
        st.error(str(exc))

result = st.session_state.get("silver_screen_result")
if not result:
    st.info(
        "Start a production or open a saved run. Enable Voice Studio to add "
        "authorized dialogue, narration, subtitles, and dubbed MP4 output."
    )
    st.stop()

state = result.get("state") or {}
media = result.get("media") or {}
voice = media.get("voice") or result.get("voice") or {}
artifacts = result.get("artifacts") or {}
narrative_metrics = result.get("metrics") or {}
summary = dashboard_metrics(media)
view = production_view(media)

_show_status(result)
st.progress(
    view.progress_percent,
    text=f"Film progress: {summary['verified']} of {summary['planned']} clips",
)

columns = st.columns(8)
columns[0].metric(
    "Narrative", f"{float(narrative_metrics.get('finalScore', 0)):.3f}"
)
columns[1].metric("Video clips", f"{summary['verified']}/{summary['planned']}")
columns[2].metric("Runtime", _runtime(summary["verifiedSeconds"]))
columns[3].metric("Remaining", _runtime(summary["remainingSeconds"]))
columns[4].metric("Continuity", f"{summary['continuityPercent']:.0f}%")
columns[5].metric("Video calls", summary["providerCalls"])
columns[6].metric("Video state", display_msil(media))
columns[7].metric(
    "Voiced clips",
    f"{(voice.get('metrics') or {}).get('dubbedClips', 0)}/"
    f"{(voice.get('metrics') or {}).get('verifiedVideoShots', 0)}",
)

video_tab, voice_tab, queue_tab, story_tab, audit_tab, files_tab = st.tabs(
    ["Video", "Voices", "Production queue", "Story", "TGRM audit", "Files"]
)

with video_tab:
    st.caption(media.get("note") or view.detail)
    playable = (
        voice.get("final_video_path")
        or voice.get("partial_video_path")
        or media.get("final_video_path")
        or media.get("partial_video_path")
        or media.get("hero_path")
    )
    if playable:
        st.video(playable)
        _download(
            "Download best assembled MP4",
            playable,
            "video/mp4",
            "assembled-video",
        )
    silent = voice.get("silent_video_path") or media.get("silent_video_path")
    if silent and silent != playable:
        with st.expander("Original verified video without added voices"):
            st.video(silent)
            _download(
                "Download silent/original MP4",
                silent,
                "video/mp4",
                "silent-video",
            )
    for index, path in enumerate(media.get("video_paths") or [], start=1):
        st.markdown(f"**Verified source clip {index}**")
        st.video(path)
    if not playable and not media.get("video_paths"):
        st.warning("No verified MP4 is available yet.")

with voice_tab:
    if not voice.get("enabled"):
        st.info(
            "Voice Studio is not enabled for this production. Select a saved film "
            "in the sidebar and use Attach or update voices."
        )
    else:
        voice_metrics = voice.get("metrics") or {}
        voice_columns = st.columns(5)
        voice_columns[0].metric(
            "Voice state",
            str(
                (voice.get("msil") or {}).get("verdict")
                or voice.get("status")
                or "unknown"
            ).upper(),
        )
        voice_columns[1].metric(
            "Generated lines",
            f"{voice_metrics.get('generatedLines', 0)}/"
            f"{voice_metrics.get('plannedLines', 0)}",
        )
        voice_columns[2].metric(
            "Provider calls", voice_metrics.get("providerCalls", 0)
        )
        voice_columns[3].metric("Repairs", voice_metrics.get("repairs", 0))
        voice_columns[4].metric(
            "Coverage",
            f"{100 * float(voice_metrics.get('coverageRatio', 0) or 0):.0f}%",
        )
        _show_voice_failure(voice)
        st.subheader("Voice cast")
        cast_rows = [
            *list((voice.get("cast") or {}).get("characters") or []),
            (voice.get("cast") or {}).get("narrator") or {},
        ]
        st.dataframe(cast_rows, use_container_width=True, hide_index=True)
        st.subheader("Spoken line queue")
        line_rows = []
        for line in (voice.get("plan") or {}).get("lines") or []:
            if isinstance(line, dict):
                line_rows.append(
                    {
                        "Clip": line.get("order"),
                        "Speaker": line.get("speaker"),
                        "Text": line.get("text"),
                        "State": line.get("status"),
                        "Attempts": line.get("attempts"),
                        "Error": line.get("lastError"),
                    }
                )
        if line_rows:
            st.dataframe(line_rows, use_container_width=True, hide_index=True)
        for index, path in enumerate(
            voice.get("line_audio_paths") or [], start=1
        ):
            st.audio(path)
            _download(
                f"Download voice line {index}",
                path,
                "audio/mpeg",
                f"voice-line-{index}",
            )
        _download(
            "Download subtitles",
            voice.get("subtitles_path"),
            "application/x-subrip",
            "subtitles",
        )

with queue_tab:
    rows = queue_rows(media)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No video queue exists for this run.")
    st.json(
        {
            "stage": view.stage,
            "stopReason": media.get("stopReason"),
            "fractures": media.get("fractures"),
            "estimatedSpendUsd": summary["estimatedSpendUsd"],
        }
    )

with story_tab:
    st.subheader(state.get("logline") or state.get("title") or "Story")
    st.json(state.get("storyBible") or {})
    st.text_area("Screenplay", str(state.get("script") or ""), height=500)

with audit_tab:
    st.json(
        {
            "productionView": view.__dict__,
            "videoMetrics": media.get("metrics"),
            "videoMSIL": media.get("msil"),
            "videoScars": media.get("scars"),
            "voiceMetrics": voice.get("metrics"),
            "voiceMSIL": voice.get("msil"),
            "voiceScars": (voice.get("plan") or {}).get("scars"),
            "providerPredictions": media.get("predictions"),
            "events": (media.get("queue") or {}).get("events"),
            "voiceEvents": (voice.get("plan") or {}).get("events"),
            "narrativeTGRM": result.get("log"),
        }
    )

with files_tab:
    _download(
        "Download complete bundle",
        artifacts.get("bundle"),
        "application/zip",
        "bundle",
    )
    _download(
        "Download film JSON", artifacts.get("film"), "application/json", "film"
    )
    _download(
        "Download TGRM JSON", artifacts.get("tgrm"), "application/json", "tgrm"
    )
    _download(
        "Download video queue",
        media.get("queue_path"),
        "application/json",
        "video-queue",
    )
    _download(
        "Download video runtime",
        media.get("runtime_path"),
        "application/json",
        "video-runtime",
    )
    _download(
        "Download video scar memory",
        media.get("scar_memory_path"),
        "application/json",
        "video-scars",
    )
    _download(
        "Download voice config",
        voice.get("config_path"),
        "application/json",
        "voice-config",
    )
    _download(
        "Download voice cast",
        voice.get("cast_path"),
        "application/json",
        "voice-cast",
    )
    _download(
        "Download voice plan",
        voice.get("plan_path"),
        "application/json",
        "voice-plan",
    )
    _download(
        "Download voice runtime",
        voice.get("runtime_path"),
        "application/json",
        "voice-runtime",
    )
    _download(
        "Download voice scar memory",
        voice.get("scar_memory_path"),
        "application/json",
        "voice-scars",
    )
    st.download_button(
        "Download result JSON",
        json.dumps(result, indent=2, default=str).encode(),
        file_name="result.json",
        mime="application/json",
    )
