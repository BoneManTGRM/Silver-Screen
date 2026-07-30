"""Silver-Screen — Streamlit primary studio (Reparodynamics · TGRM).

Demo UI for Brief → TGRM pipeline → RYE / MSIL metrics + scar memory.
"""

from __future__ import annotations
import streamlit as st
from silver_screen.science import SCIENCE, FIVE_LAWS, FORMATS
from silver_screen.tgrm import run_tgrm

st.set_page_config(
    page_title="Silver-Screen · TGRM Studio",
    page_icon="🎥",
    layout="wide",
)

st.title("🎥 Silver-Screen")
st.caption(SCIENCE["credit"])
st.markdown(f"**{SCIENCE['tagline']}**")

with st.sidebar:
    st.header("Brief")
    title = st.text_input("Title", value="Echoes of the Fracture")
    premise = st.text_area(
        "Premise",
        value="A repair technician discovers that every system she fixes remembers the pain of the break and begins to dream.",
        height=120,
    )
    genre = st.selectbox("Genre", ["sci-fi", "drama", "thriller", "fantasy", "noir"], index=0)
    tone = st.selectbox("Tone", ["cinematic", "intimate", "tense", "poetic"], index=0)
    fmt = st.selectbox("Format", list(FORMATS.keys()), index=2)
    st.divider()
    st.subheader("Your Images & Voices (demo)")
    imgs = st.file_uploader("Character portraits", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
    voices = st.file_uploader("Voice samples", type=["wav", "mp3", "m4a"], accept_multiple_files=True)
    st.caption("Uploads are accepted for future media pipeline; current demo uses text TGRM.")

if st.button("Run TGRM Pipeline", type="primary"):
    fmt_info = FORMATS.get(fmt, FORMATS["feature"])
    acts = [{"name": f"Act {i+1}", "scene_count": max(1, fmt_info["chapters"] // fmt_info["acts"])} for i in range(fmt_info["acts"])]
    scenes = [{"id": i, "summary": f"Scene {i+1} of the journey"} for i in range(fmt_info["chapters"])]
    characters = [
        {"name": "Aria", "role": "protagonist"},
        {"name": "The System", "role": "antagonist / mirror"},
    ]
    seed_script = f"""TITLE: {title}
GENRE: {genre} · TONE: {tone} · FORMAT: {fmt}

PREMISE: {premise}

ACT 1
Aria notices a machine that refuses to forget its last failure.

ACT 2
She tries standard repair protocols. They fail.

ACT 3
The system begins to speak in her own voice.
"""
    state = {
        "title": title,
        "premise": premise,
        "genre": genre,
        "tone": tone,
        "format": fmt,
        "script": seed_script,
        "characters": characters,
        "scenes": scenes,
        "acts": acts,
        "scars": [],
    }

    with st.spinner("TGRM running: Detect → Minimal → Verify → Reinforce..."):
        result = run_tgrm(state)

    st.success("Pipeline complete")
    col1, col2, col3, col4 = st.columns(4)
    m = result["metrics"]
    col1.metric("RYE", f"{m['rye']:.4f}")
    col2.metric("ΔR", f"{m['deltaR']:.4f}")
    col3.metric("Energy", m["energy"])
    col4.metric("MSIL", result["msil"]["verdict"].upper())

    st.subheader("Repaired screenplay (seed + micro-fixes)")
    st.text_area("Script", result["state"].get("script", ""), height=280)

    st.subheader("TGRM cycle log")
    for entry in result["log"]:
        with st.expander(f"Cycle {entry['cycle']} · {entry['phase']} · RYE {entry.get('rye', 0):.4f}"):
            st.write(entry.get("notes", []))
            if entry.get("fracture"):
                st.json(entry["fracture"])
            st.write("Correction:", entry.get("correction"))

    st.subheader("Scar memory")
    if result["scars"]:
        st.json(result["scars"])
    else:
        st.info("No scars reinforced this run.")

    st.subheader("MSIL report")
    st.json(result["msil"])

    st.subheader("NFT-style metadata traits")
    st.json({
        "name": title,
        "description": premise[:200],
        "attributes": [
            {"trait_type": "RYE", "value": m["rye"]},
            {"trait_type": "MSIL_verdict", "value": result["msil"]["verdict"]},
            {"trait_type": "TGRM_cycles", "value": m["cycles"]},
            {"trait_type": "micro_repairs", "value": m["microRepairs"]},
            {"trait_type": "full_repairs", "value": m["fullRepairs"]},
            {"trait_type": "tau", "value": SCIENCE["tau"]},
            {"trait_type": "format", "value": fmt},
        ],
    })
else:
    st.info("Fill the brief in the sidebar and click **Run TGRM Pipeline** to execute Detect → Minimal → Verify → Reinforce.")
    st.markdown("### Five Laws of Reparodynamics (cinema)")
    for law in FIVE_LAWS:
        st.markdown(f"**{law['name']}** — {law['cinema']}")
