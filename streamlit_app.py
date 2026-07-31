"""General-purpose landing page for Silver-Screen."""

from __future__ import annotations

import os

import streamlit as st

from silver_screen.health import health_report
from silver_screen.runtime import list_runs
from silver_screen.science import SCIENCE
from silver_screen.voice_providers import provider_capabilities

st.set_page_config(
    page_title="Silver-Screen | AI Film Studio",
    page_icon="🎥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🎥 Silver-Screen")
st.caption(SCIENCE["credit"])
st.subheader("A general-purpose AI film production studio")
st.write(
    "Create original films, make yourself the lead, work with an authorized real "
    "person or character, generate full blueprint-length productions, add voices, "
    "synchronize authored scripts, rebuild smooth cinematic transitions, and repair "
    "only the weak boundary of an otherwise accepted film. Moonie Moo is available "
    "as an optional example project; the repository is not dedicated to any single character."
)

replicate_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
voice_caps = provider_capabilities()
continuity_ready = os.getenv(
    "SILVER_SCREEN_CINEMATIC_TRANSITIONS", "1"
).strip().lower() not in {"0", "false", "no", "off"}

status_columns = st.columns(5)
status_columns[0].metric(
    "Video generation",
    "Ready" if replicate_ready else "Needs key",
)
status_columns[1].metric(
    "OpenAI voices",
    "Ready" if voice_caps.get("openai") else "Optional",
)
status_columns[2].metric(
    "ElevenLabs voices",
    "Ready" if voice_caps.get("elevenlabs") else "Optional",
)
status_columns[3].metric("Durable checkpoints", "Enabled")
status_columns[4].metric(
    "Cinematic transitions",
    "Enabled" if continuity_ready else "Disabled",
)

st.divider()
st.header("Start a production")
left, right = st.columns(2)
with left:
    st.subheader("🎬 Creative Director - recommended")
    st.write(
        "Choose grounded prestige, modern spy thriller, naturalistic drama, dark "
        "psychological thriller, premium animation, or a custom visual contract. Build "
        "the screenplay and exact provider prompts for free, inspect anti-cliche scores, "
        "then approve the script, prompt direction, and paid call ceiling separately."
    )
    try:
        st.page_link(
            "pages/6_Creative_Director.py",
            label="Open Creative Director",
            icon="🎬",
            use_container_width=True,
        )
    except TypeError:
        st.page_link(
            "pages/6_Creative_Director.py",
            label="Open Creative Director",
            icon="🎬",
        )

    st.subheader("⭐ Put yourself in the movie")
    st.write(
        "Upload authorized photos of yourself, lock your identity and wardrobe, run "
        "a one-clip screen test, then continue the same production into a trailer, "
        "short, episode, featurette, or feature film."
    )
    try:
        st.page_link(
            "pages/0_Star_Vehicle_Studio.py",
            label="Open Star Vehicle Studio",
            icon="⭐",
            use_container_width=True,
        )
    except TypeError:
        st.page_link(
            "pages/0_Star_Vehicle_Studio.py",
            label="Open Star Vehicle Studio",
            icon="⭐",
        )

    st.subheader("🎬 Build a complete original film")
    st.write(
        "Start from a blank project or an optional template. The story blueprint and "
        "paid render runtime stay synchronized, so a two-minute trailer plans the full "
        "two minutes rather than one eight-second clip."
    )
    try:
        st.page_link(
            "pages/1_Full_Blueprint_Production.py",
            label="Open Full Blueprint Production",
            icon="🎬",
            use_container_width=True,
        )
    except TypeError:
        st.page_link(
            "pages/1_Full_Blueprint_Production.py",
            label="Open Full Blueprint Production",
            icon="🎬",
        )

    st.subheader("🎞️ Smooth an existing film")
    st.write(
        "Measure every clip boundary, apply match-on-action transitions, crossfade "
        "audio, and rebuild a smoother local cinematic master without another "
        "Replicate prediction."
    )
    try:
        st.page_link(
            "pages/4_Cinematic_Continuity.py",
            label="Open Cinematic Continuity",
            icon="🎞️",
            use_container_width=True,
        )
    except TypeError:
        st.page_link(
            "pages/4_Cinematic_Continuity.py",
            label="Open Cinematic Continuity",
            icon="🎞️",
        )

with right:
    st.subheader("🎙️ Add voices to a saved film")
    st.write(
        "Use OpenAI voices, authorized ElevenLabs voice IDs, an authorized custom "
        "voice where supported, or finished recordings you upload yourself."
    )
    try:
        st.page_link(
            "pages/3_Voice_Studio.py",
            label="Open Voice Studio",
            icon="🎙️",
            use_container_width=True,
        )
    except TypeError:
        st.page_link(
            "pages/3_Voice_Studio.py",
            label="Open Voice Studio",
            icon="🎙️",
        )

    st.subheader("📝 Enter and synchronize a script")
    st.write(
        "Time authored dialogue against verified footage, generate speech, export "
        "SRT/VTT/ASS captions, and create synchronized and captioned professional cuts."
    )
    try:
        st.page_link(
            "pages/2_Professional_Script_Sync.py",
            label="Open Professional Script Sync",
            icon="📝",
            use_container_width=True,
        )
    except TypeError:
        st.page_link(
            "pages/2_Professional_Script_Sync.py",
            label="Open Professional Script Sync",
            icon="📝",
        )

    st.subheader("🎬 Repair a weak transition")
    st.write(
        "Director Review finds the boundaries that still read as abrupt. It preserves "
        "the accepted incoming clip, authorizes at most one targeted retake at a time, "
        "then automatically keeps whichever candidate scores better."
    )
    try:
        st.page_link(
            "pages/5_Director_Review.py",
            label="Open Director Review",
            icon="🎬",
            use_container_width=True,
        )
    except TypeError:
        st.page_link(
            "pages/5_Director_Review.py",
            label="Open Director Review",
            icon="🎬",
        )

st.divider()
st.header("Continue without wasting accepted footage")
col_a, col_b = st.columns(2)
with col_a:
    st.write(
        "Use **Extend Existing Production** to turn a successful one-clip test into a "
        "longer film while preserving the verified clip."
    )
    try:
        st.page_link(
            "pages/2_Extend_Existing_Production.py",
            label="Extend an existing production",
            icon="➕",
        )
    except Exception:
        pass
with col_b:
    st.write(
        "All paid video work is checkpointed. Temporary Replicate 429 throttles use "
        "the provider's Retry-After window, and targeted transition retakes preserve "
        "the accepted original before making another paid request."
    )

with st.sidebar:
    st.header("Provider setup")
    st.write(
        f"Replicate: **{'ready' if replicate_ready else 'missing'}**  \n"
        "Secret: `REPLICATE_API_TOKEN`"
    )
    st.write(
        f"OpenAI speech: **{'ready' if voice_caps.get('openai') else 'optional / missing'}**  \n"
        "Secret: `OPENAI_API_KEY`"
    )
    st.write(
        f"ElevenLabs speech: **{'ready' if voice_caps.get('elevenlabs') else 'optional / missing'}**  \n"
        "Secret: `ELEVENLABS_API_KEY`"
    )
    st.write(
        f"Cinematic continuity: **{'enabled' if continuity_ready else 'disabled'}**"
    )
    st.caption(
        "Replicate is required for generated video. Choose one speech provider or use "
        "authorized finished audio tracks. Creative preview, quality gates, script "
        "analysis, and cinematic transition assembly require no additional API."
    )
    with st.expander("Runtime health"):
        st.json(health_report("runs"))
    with st.expander("Recent productions"):
        records = list_runs("runs", 10)
        if not records:
            st.caption("No saved productions yet.")
        for record in records:
            brief = record.get("brief") or {}
            st.caption(
                f"{record.get('runId')} | {record.get('status')} | "
                f"{record.get('title') or brief.get('title') or 'Untitled'}"
            )

st.info(
    "For the least corny result, start in **Creative Director**, choose Grounded "
    "prestige or Modern spy thriller, keep melodrama and exposition below 15, inspect "
    "the free prompt preview, and begin with one eight-second screen test."
)
