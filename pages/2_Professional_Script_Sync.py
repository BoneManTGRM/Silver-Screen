"""Professional Script Sync page for authored dialogue and narration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.runtime import find_run_workspace, list_runs
from silver_screen.script_sync import (
    ScriptSyncConfig,
    VoiceStudioError,
    build_timing_plan,
    parse_script,
    render_script_production,
)
from silver_screen.voice_providers import OPENAI_BUILTIN_VOICES, provider_capabilities

st.set_page_config(
    page_title="Professional Script Sync | Silver-Screen",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _read_result(run_root: Path) -> dict[str, Any]:
    candidates = [run_root / "result.json", run_root / "media" / "result.json"]
    for path in candidates:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    raise VoiceStudioError("The selected run does not contain result.json")


def _download(label: str, path: str | None, mime: str, key: str) -> None:
    if not path:
        return
    candidate = Path(path)
    if not candidate.exists():
        return
    st.download_button(
        label,
        candidate.read_bytes(),
        file_name=candidate.name,
        mime=mime,
        key=key,
        use_container_width=True,
    )


st.title("🎙️ Professional Script Sync")
st.write(
    "Enter a finished script, assign voices, preview automatic timing, and render "
    "a professionally synchronized film with measured speech, line and word timing, "
    "loudness-normalized audio, and optional cinematic captions."
)

runs = list_runs("runs", 50)
run_map = {
    f"{item.get('runId')} | {item.get('status')} | {(item.get('brief') or {}).get('title', 'Untitled')}": item
    for item in runs
}

with st.sidebar:
    st.header("Production")
    selected = st.selectbox(
        "Verified film run",
        list(run_map) or ["No runs available"],
        disabled=not run_map,
    )

    st.header("Authored script")
    script = st.text_area(
        "Script",
        value=(
            "NARRATOR: Tonight, the spotlight belongs to one unforgettable star.\n"
            "MOONIE MOO: Darling, the spotlight has always belonged to me.\n"
            "BULLY: Keep walking. I saw the snack table first."
        ),
        height=260,
        help=(
            "Use SPEAKER: dialogue. Optional exact cues are supported, for example "
            "[00:00.200 --> 00:03.800] MOONIE MOO: Welcome to my premiere."
        ),
    )

    st.header("Voice casting")
    provider_label = st.selectbox(
        "Speech provider",
        ["OpenAI", "ElevenLabs"],
    )
    provider = provider_label.lower()
    if provider == "openai":
        lead_voice = st.selectbox("Lead voice", list(OPENAI_BUILTIN_VOICES), index=list(OPENAI_BUILTIN_VOICES).index("coral"))
        supporting_voice = st.selectbox("Supporting voice", list(OPENAI_BUILTIN_VOICES), index=list(OPENAI_BUILTIN_VOICES).index("onyx"))
        narrator_voice = st.selectbox("Narrator voice", list(OPENAI_BUILTIN_VOICES), index=list(OPENAI_BUILTIN_VOICES).index("cedar"))
        authorization = True
    else:
        lead_voice = st.text_input("Lead ElevenLabs voice ID")
        supporting_voice = st.text_input("Supporting ElevenLabs voice ID")
        narrator_voice = st.text_input("Narrator ElevenLabs voice ID")
        authorization = st.checkbox(
            "I confirm I own or have explicit permission to use these voice IDs.",
            value=False,
        )

    instructions = st.text_area(
        "Performance direction",
        "Polished animated feature-film performance, emotionally natural, precise comic timing, clear diction, no announcer exaggeration unless the line is narration.",
        height=110,
    )
    speed = st.slider("Speech speed", 0.7, 1.3, 1.0, 0.05)
    wpm = st.slider("Timing target, words per minute", 90, 220, 145, 5)
    preserve_audio = st.checkbox("Keep quiet original scene audio underneath", value=False)
    burn_captions = st.checkbox("Burn professional captions into a second MP4", value=True)
    caption_style = st.selectbox("Caption style", ["cinematic", "clean", "social"])

    caps = provider_capabilities()
    provider_ready = bool(caps.get(provider))
    if not provider_ready:
        key = "OPENAI_API_KEY" if provider == "openai" else "ELEVENLABS_API_KEY"
        st.error(f"Add {key} to Streamlit deployment secrets.")

    preview_clicked = st.button(
        "Analyze and time script",
        use_container_width=True,
        disabled=not run_map or not script.strip(),
    )
    render_clicked = st.button(
        "Render professional synchronized film",
        type="primary",
        use_container_width=True,
        disabled=(
            not run_map
            or not script.strip()
            or not provider_ready
            or (provider == "elevenlabs" and not authorization)
        ),
    )

config = ScriptSyncConfig(
    provider=provider,
    lead_voice=lead_voice,
    supporting_voice=supporting_voice,
    narrator_voice=narrator_voice,
    instructions=instructions,
    speed=float(speed),
    words_per_minute=int(wpm),
    preserve_source_audio=preserve_audio,
    burn_captions=burn_captions,
    caption_style=caption_style,
    authorization_confirmed=authorization,
)

if preview_clicked and run_map:
    try:
        record = run_map[selected]
        root = find_run_workspace(str(record.get("runId")), "runs")
        result = _read_result(root)
        plan = build_timing_plan(script, result.get("media") or {}, config)
        st.session_state["script_sync_preview"] = plan
    except Exception as exc:
        st.error(str(exc))

if render_clicked and run_map:
    try:
        record = run_map[selected]
        root = find_run_workspace(str(record.get("runId")), "runs")
        result = _read_result(root)
        with st.status("Rendering synchronized script", expanded=True) as status:
            status.write("Parsing speakers and timing cues")
            status.write("Generating authorized speech and measuring actual duration")
            status.write("Building word alignment, subtitles, and normalized shot mixes")
            rendered = render_script_production(root, result.get("media") or {}, script, config)
            status.update(label="Professional script synchronization complete", state="complete")
        st.session_state["script_sync_result"] = rendered
        st.session_state["script_sync_preview"] = rendered.get("plan")
    except Exception as exc:
        st.error(str(exc))

preview = st.session_state.get("script_sync_preview")
if preview:
    metrics = preview.get("metrics") or {}
    columns = st.columns(5)
    columns[0].metric("Lines", metrics.get("lineCount", 0))
    columns[1].metric("Words", metrics.get("wordCount", 0))
    columns[2].metric("Video runtime", f"{float(preview.get('videoDurationSeconds', 0)):.1f}s")
    columns[3].metric("Speech estimate", f"{float(metrics.get('estimatedSpeechSeconds', 0)):.1f}s")
    columns[4].metric("Fit", str(metrics.get("fitStatus", "unknown")).replace("_", " ").title())

    if float(metrics.get("overflowSeconds", 0) or 0) > 0.15:
        st.warning(
            f"The script exceeds available shot windows by approximately {metrics['overflowSeconds']:.2f}s. "
            "Shorten highlighted lines, increase speech speed slightly, or generate more video."
        )

    st.subheader("Timed script")
    st.dataframe(
        [
            {
                "Line": line.get("order"),
                "Speaker": line.get("speaker"),
                "Text": line.get("text"),
                "Clip": line.get("shotId"),
                "Start": line.get("globalStartSeconds"),
                "End": line.get("globalEndSeconds"),
                "Available": line.get("targetDurationSeconds"),
                "Estimated speech": line.get("estimatedSpeechSeconds"),
                "Overflow": line.get("overflowSeconds"),
            }
            for line in preview.get("lines") or []
        ],
        use_container_width=True,
        hide_index=True,
    )

rendered = st.session_state.get("script_sync_result")
if rendered:
    st.success("Professional synchronized film completed.")
    playable = rendered.get("captioned_video_path") or rendered.get("final_video_path")
    if playable:
        st.video(playable)
    left, right = st.columns(2)
    with left:
        _download("Download synchronized MP4", rendered.get("final_video_path"), "video/mp4", "script-sync-final")
        _download("Download captioned MP4", rendered.get("captioned_video_path"), "video/mp4", "script-sync-captioned")
        _download("Download SRT subtitles", rendered.get("srt_path"), "application/x-subrip", "script-sync-srt")
    with right:
        _download("Download WebVTT subtitles", rendered.get("vtt_path"), "text/vtt", "script-sync-vtt")
        _download("Download styled ASS subtitles", rendered.get("ass_path"), "text/plain", "script-sync-ass")
        _download("Download word alignment JSON", rendered.get("word_alignment_path"), "application/json", "script-sync-words")
        _download("Download timing plan JSON", rendered.get("plan_path"), "application/json", "script-sync-plan")
else:
    st.info(
        "Start with Analyze and time script. Rendering makes paid speech-provider calls only after the timing plan is visible."
    )
