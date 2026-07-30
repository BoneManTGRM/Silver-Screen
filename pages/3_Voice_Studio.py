"""Attach authorized voices and narration to any saved Silver-Screen film."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.pipeline import PipelineError
from silver_screen.runtime import list_runs
from silver_screen.voice_providers import OPENAI_BUILTIN_VOICES, provider_capabilities
from silver_screen.voice_studio import VoiceStudioError, attach_voice_to_run

st.set_page_config(
    page_title="Silver-Screen | Voice Studio",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _run_label(record: dict[str, Any]) -> str:
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
    if data is not None:
        st.download_button(
            label,
            data,
            file_name=Path(str(path)).name,
            mime="video/mp4",
            key=key,
            use_container_width=True,
        )


st.title("🎙️ Voice Studio")
st.write(
    "Add character dialogue, narration, subtitles, and a dubbed MP4 to any saved "
    "Silver-Screen film. The original verified video is kept separately."
)

caps = provider_capabilities()
runs = list_runs("runs", 100)
run_map = {_run_label(record): record for record in runs}

with st.sidebar:
    st.header("Saved film")
    selected_run = st.selectbox(
        "Production run",
        list(run_map) or ["No saved productions"],
        disabled=not run_map,
    )

    st.header("Voice source")
    provider_label = st.selectbox(
        "Provider",
        [
            "OpenAI built-in or authorized custom voice",
            "ElevenLabs authorized voice IDs",
            "Finished audio tracks",
        ],
    )
    provider = {
        "OpenAI built-in or authorized custom voice": "openai",
        "ElevenLabs authorized voice IDs": "elevenlabs",
        "Finished audio tracks": "manual",
    }[provider_label]
    mode_label = st.selectbox(
        "Spoken content",
        ["Dialogue and narration", "Dialogue only", "Narration only"],
    )
    mode = {
        "Dialogue and narration": "dialogue+narration",
        "Dialogue only": "dialogue",
        "Narration only": "narration",
    }[mode_label]

    custom_voice = False
    custom_name = "Authorized Custom Voice"
    voice_sample = None
    consent_recording = None
    manual_tracks: list[Any] = []

    if provider == "openai":
        voices = list(OPENAI_BUILTIN_VOICES)
        lead_voice = st.selectbox(
            "Lead voice", voices, index=voices.index("coral")
        )
        supporting_voice = st.selectbox(
            "Supporting voice", voices, index=voices.index("onyx")
        )
        narrator_voice = st.selectbox(
            "Narrator voice", voices, index=voices.index("cedar")
        )
        with st.expander("Optional custom OpenAI voice"):
            custom_voice = st.checkbox("Enroll an authorized custom voice")
            custom_name = st.text_input(
                "Custom voice name",
                value="Authorized Custom Voice",
                disabled=not custom_voice,
            )
            voice_sample = st.file_uploader(
                "Authorized voice sample",
                type=["wav", "mp3", "m4a", "ogg", "webm"],
                disabled=not custom_voice,
                key="voice-studio-sample",
            )
            consent_recording = st.file_uploader(
                "Provider-required consent recording",
                type=["wav", "mp3", "m4a", "ogg", "webm"],
                disabled=not custom_voice,
                key="voice-studio-consent",
            )
    elif provider == "elevenlabs":
        lead_voice = st.text_input("Lead ElevenLabs voice ID")
        supporting_voice = st.text_input("Supporting ElevenLabs voice ID")
        narrator_voice = st.text_input("Narrator ElevenLabs voice ID")
    else:
        lead_voice = supporting_voice = narrator_voice = "manual"
        manual_tracks = (
            st.file_uploader(
                "Finished authorized tracks in clip order",
                type=["wav", "mp3", "m4a", "aac", "ogg", "flac", "webm"],
                accept_multiple_files=True,
                key="voice-studio-manual",
            )
            or []
        )

    instructions = st.text_area(
        "Performance direction",
        "Natural cinematic acting, clear diction, emotional continuity, and timing "
        "that fits the picture without rushing.",
        height=100,
        disabled=provider == "manual",
    )
    speed = st.slider(
        "Voice speed", 0.7, 1.3, 1.0, 0.05, disabled=provider == "manual"
    )
    retries = st.slider(
        "TGRM retries per line", 0, 4, 1, disabled=provider == "manual"
    )
    preserve_source_audio = st.checkbox(
        "Keep quiet source audio under speech", value=False
    )
    subtitles = st.checkbox("Export SRT subtitles", value=True)

    authorization_required = provider in {"elevenlabs", "manual"} or custom_voice
    authorization = st.checkbox(
        "I own or have explicit permission and consent to use these voices or recordings.",
        value=False,
        disabled=not authorization_required,
    )

    provider_ready = provider == "manual" or bool(caps.get(provider))
    custom_ready = not custom_voice or (
        voice_sample is not None and consent_recording is not None
    )
    manual_ready = provider != "manual" or bool(manual_tracks)
    authorization_ready = not authorization_required or authorization

    with st.expander("Provider status", expanded=True):
        st.write(
            f"OpenAI speech: **{'ready' if caps.get('openai') else 'missing'}** "
            "(`OPENAI_API_KEY`)"
        )
        st.write(
            f"ElevenLabs: **{'ready' if caps.get('elevenlabs') else 'missing'}** "
            "(`ELEVENLABS_API_KEY`)"
        )
        st.write("Finished audio tracks: **available without an API**")
        if not provider_ready:
            st.error("Add the selected provider key to Streamlit Secrets.")
        if not custom_ready:
            st.error("Custom voice enrollment requires both the sample and consent recording.")
        if not manual_ready:
            st.error("Upload at least one finished audio track.")
        if not authorization_ready:
            st.error("Confirm authorization before generating or mixing the voice.")

    request = {
        "enabled": True,
        "provider": provider,
        "mode": mode,
        "model": (
            os.getenv("SILVER_SCREEN_OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
            if provider == "openai"
            else os.getenv("SILVER_SCREEN_ELEVENLABS_MODEL", "eleven_multilingual_v2")
            if provider == "elevenlabs"
            else "manual"
        ),
        "lead_voice": lead_voice,
        "supporting_voice": supporting_voice,
        "narrator_voice": narrator_voice,
        "instructions": instructions,
        "speed": float(speed),
        "max_retries_per_line": int(retries),
        "preserve_source_audio": preserve_source_audio,
        "subtitles": subtitles,
        "authorization_confirmed": authorization if authorization_required else True,
        "custom_voice": custom_voice,
        "custom_voice_name": custom_name,
        "voice_sample": voice_sample,
        "consent_recording": consent_recording,
        "manual_tracks": manual_tracks,
    }

    attach_clicked = st.button(
        "Attach or update voices",
        type="primary",
        use_container_width=True,
        disabled=(
            not run_map
            or not provider_ready
            or not custom_ready
            or not manual_ready
            or not authorization_ready
        ),
    )

if attach_clicked and run_map:
    run_id = str(run_map[selected_run].get("runId") or "")
    try:
        with st.spinner("Generating authorized speech and assembling the voiced film..."):
            result = attach_voice_to_run(run_id, [request], output_root="runs")
        st.session_state["generic-voice-result"] = result
        st.success(
            "Voice production saved. The original silent or provider-audio film remains available separately."
        )
    except (VoiceStudioError, PipelineError, ValueError) as exc:
        st.error(str(exc))

result = st.session_state.get("generic-voice-result")
if not result:
    st.info(
        "Select a saved film and voice source. For exact authored dialogue and advanced "
        "timing, use Professional Script Sync instead."
    )
    try:
        st.page_link(
            "pages/2_Professional_Script_Sync.py",
            label="Open Professional Script Sync",
            icon="📝",
        )
    except Exception:
        pass
    st.stop()

voice = (result.get("media") or {}).get("voice") or result.get("voice") or {}
metrics = voice.get("metrics") or {}
columns = st.columns(5)
columns[0].metric("Status", str(voice.get("status") or "unknown").upper())
columns[1].metric("Voiced clips", metrics.get("dubbedClips", 0))
columns[2].metric("Speech calls", metrics.get("providerCalls", 0))
columns[3].metric("Repairs", metrics.get("repairs", 0))
columns[4].metric("Voice seconds", metrics.get("voiceSeconds", 0))

playable = voice.get("final_video_path") or voice.get("partial_video_path")
if playable:
    st.video(playable)
    _download("Download voiced MP4", playable, "generic-voiced-film")
if voice.get("silent_video_path"):
    with st.expander("Original video without added voices"):
        st.video(voice["silent_video_path"])
        _download(
            "Download original MP4",
            voice["silent_video_path"],
            "generic-silent-film",
        )
if voice.get("error"):
    st.error(str(voice["error"]))
with st.expander("Voice production details"):
    st.json(voice)
