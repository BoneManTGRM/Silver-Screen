"""General-purpose landing page for Silver-Screen."""
from __future__ import annotations

import os
import streamlit as st

from silver_screen.health import health_report
from silver_screen.runtime import list_runs
from silver_screen.science import SCIENCE
from silver_screen.voice_providers import provider_capabilities

st.set_page_config(page_title="Silver-Screen | Autonomous AI Film Studio", page_icon="🎥", layout="wide", initial_sidebar_state="expanded")
st.title("Silver-Screen")
st.caption(SCIENCE["credit"])
st.subheader("A durable, memory-backed autonomous film-production studio")
st.write(
    "Create an original film, make yourself the lead with authorized references, or use an authorized character. "
    "Silver-Screen 9 adds a one-click Autonomous Studio that coordinates screenplay planning, prompt ledgers, "
    "persistent world memory, model routing, resumable generation, visual and semantic QA, repair, transitions, "
    "voices, captions, evidence, and a machine-readable edit timeline. Moonie Moo remains an optional example."
)

replicate_ready = bool(os.getenv("REPLICATE_API_TOKEN", "").strip())
openai_ready = bool(os.getenv("OPENAI_API_KEY", "").strip())
voice_caps = provider_capabilities()
continuity_ready = os.getenv("SILVER_SCREEN_CINEMATIC_TRANSITIONS", "1").strip().lower() not in {"0", "false", "no", "off"}

status = st.columns(6)
status[0].metric("Video", "Ready" if replicate_ready else "Needs key")
status[1].metric("Semantic QA", "Ready" if openai_ready else "Optional")
status[2].metric("OpenAI voices", "Ready" if voice_caps.get("openai") else "Optional")
status[3].metric("ElevenLabs", "Ready" if voice_caps.get("elevenlabs") else "Optional")
status[4].metric("Project memory", "Enabled")
status[5].metric("Transitions", "Enabled" if continuity_ready else "Disabled")

st.divider()
st.header("Recommended")
st.subheader("Autonomous Studio: one-click production")
st.write(
    "Enter the concept once. The system performs free preproduction, locks the approved shot contract, "
    "creates persistent project memory, generates within explicit budgets, inspects accepted footage, "
    "finishes the edit, and preserves a durable checkpoint if hosting or provider limits interrupt the run."
)
st.warning(
    "Blockbuster target maximizes the available workflow, but no orchestration layer can guarantee that current "
    "generative models will equal a human-produced Hollywood feature in every shot."
)
try:
    st.page_link("pages/9_Autonomous_Studio.py", label="Open Autonomous Studio", icon="🎬", use_container_width=True)
except TypeError:
    st.page_link("pages/9_Autonomous_Studio.py", label="Open Autonomous Studio", icon="🎬")

st.divider()
st.header("Specialist workspaces")
left, right = st.columns(2)
with left:
    pages = [
        ("pages/7_Shot_Director.py", "Shot Director", "Inspect and lock every clip prompt before spending."),
        ("pages/6_Creative_Director.py", "Creative Director", "Control screenplay maturity, performance, camera, pacing, and anti-cliche gates."),
        ("pages/0_Star_Vehicle_Studio.py", "Star Vehicle Studio", "Use authorized references to put yourself or another permitted subject in the lead."),
        ("pages/1_Full_Blueprint_Production.py", "Full Blueprint Production", "Match the paid render length to the complete story blueprint."),
        ("pages/8_Visual_Quality_Supervisor.py", "Visual Quality Supervisor", "Inspect sharpness, exposure, flicker, motion, and broad appearance consistency."),
    ]
    for path, label, description in pages:
        st.subheader(label)
        st.write(description)
        try:
            st.page_link(path, label=f"Open {label}", use_container_width=True)
        except TypeError:
            st.page_link(path, label=f"Open {label}")
with right:
    pages = [
        ("pages/3_Voice_Studio.py", "Voice Studio", "Add generated or finished authorized voices to saved footage."),
        ("pages/2_Professional_Script_Sync.py", "Professional Script Sync", "Time authored dialogue, speech, captions, and synchronized exports."),
        ("pages/4_Cinematic_Continuity.py", "Cinematic Continuity", "Rebuild smoother local transitions without a new video prediction."),
        ("pages/5_Director_Review.py", "Director Review", "Repair only a weak transition while preserving the accepted original."),
        ("pages/2_Extend_Existing_Production.py", "Extend Existing Production", "Turn a successful screen test into a longer film without discarding it."),
    ]
    for path, label, description in pages:
        st.subheader(label)
        st.write(description)
        try:
            st.page_link(path, label=f"Open {label}", use_container_width=True)
        except TypeError:
            st.page_link(path, label=f"Open {label}")

with st.sidebar:
    st.header("Provider setup")
    st.write(f"Replicate: **{'ready' if replicate_ready else 'missing'}**  \n`REPLICATE_API_TOKEN`")
    st.write(f"OpenAI: **{'ready' if openai_ready else 'optional / missing'}**  \n`OPENAI_API_KEY`")
    st.write(f"ElevenLabs: **{'ready' if voice_caps.get('elevenlabs') else 'optional / missing'}**  \n`ELEVENLABS_API_KEY`")
    st.caption(
        "Replicate is required for generated video. OpenAI is optional for speech and explicitly authorized semantic shot review. "
        "Free planning, project memory, prompt ledgers, visual QA, transition analysis, and local assembly require no additional API."
    )
    with st.expander("Runtime health"):
        st.json(health_report("runs"))
    with st.expander("Recent productions"):
        records = list_runs("runs", 10)
        if not records:
            st.caption("No saved productions yet.")
        for record in records:
            brief = record.get("brief") or {}
            st.caption(f"{record.get('runId')} | {record.get('status')} | {record.get('title') or brief.get('title') or 'Untitled'}")

st.info(
    "For the safest first paid test, use Autonomous Studio with an 8-second screen test. "
    "Once identity, acting, camera language, and visual quality are acceptable, continue the same durable project."
)
