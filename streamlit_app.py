"""Silver-Screen — Streamlit primary studio (Reparodynamics · TGRM).

Usable now: Brief → TGRM → metrics + script + scars.
Optional: chapter/hero reels if moviepy works.
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
st.caption(SCIENCE.get("credit", "Reparodynamics · TGRM · RYE · MSIL"))
st.markdown(f"**{SCIENCE.get('tagline', '')}**")

with st.sidebar:
    st.header("Brief")
    title = st.text_input("Title", value="Echoes of the Fracture")
    premise = st.text_area(
        "Premise",
        value="A repair technician discovers that every system she fixes remembers the pain of the break and begins to dream.",
        height=120,
    )
    genre_options = list(FORMATS.keys()) if FORMATS else ["feature", "short", "trailer"]
    genre = st.selectbox("Genre", ["sci-fi", "noir", "drama", "thriller", "fantasy", "horror"], index=0)
    tone = st.selectbox("Tone", ["cinematic", "intimate", "tense", "poetic", "melancholy"], index=0)
    fmt = st.selectbox("Format", genre_options, index=min(2, len(genre_options) - 1))
    st.divider()
    st.subheader("Your Images (optional)")
    imgs = st.file_uploader("Character portraits", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
    voices = st.file_uploader("Voice samples (future)", type=["wav", "mp3", "m4a"], accept_multiple_files=True)
    st.caption("Portraits used for reels when moviepy is available.")

if st.button("Run TGRM Pipeline", type="primary"):
    fmt_info = FORMATS.get(fmt, {"acts": 3, "chapters": 6, "duration_min": 90})
    acts = [
        {"name": f"Act {i+1}", "scene_count": max(1, fmt_info.get("chapters", 6) // max(1, fmt_info.get("acts", 3)))}
        for i in range(fmt_info.get("acts", 3))
    ]
    scenes = [{"id": i, "summary": f"Scene {i+1} of the journey", "chapter": (i // 2) + 1} for i in range(fmt_info.get("chapters", 6))]
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
        "chapters": [{"number": i + 1, "title": f"Chapter {i+1}"} for i in range(fmt_info.get("chapters", 6))],
        "scars": [],
    }

    with st.spinner("TGRM running: Detect → Minimal → Verify → Reinforce..."):
        result = run_tgrm(state)

    st.success("TGRM complete")
    m = result.get("metrics", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("RYE", f"{m.get('rye', 0):.4f}")
    col2.metric("ΔR", f"{m.get('deltaR', 0):.4f}")
    col3.metric("Energy", m.get("energy", 0))
    col4.metric("MSIL", str(result.get("msil", {}).get("verdict", "—")).upper())

    st.subheader("Repaired screenplay")
    st.text_area("Script", result.get("state", {}).get("script", ""), height=280)

    # Optional media reels (does not crash if moviepy fails)
    try:
        from silver_screen.media import process_media
        with st.spinner("Generating chapter / hero reels (optional)..."):
            media = process_media(result.get("state", state), images=imgs, voices=voices)
        if media.get("hero_path"):
            st.subheader("Hero Reel")
            try:
                st.video(media["hero_path"])
                with open(media["hero_path"], "rb") as f:
                    st.download_button("Download hero reel", f, file_name="hero_reel.webm")
            except Exception as e:
                st.info(f"Hero reel path: {media['hero_path']} ({e})")
        if media.get("chapter_paths"):
            st.subheader("Chapter Reels / Stills")
            for i, p in enumerate(media["chapter_paths"]):
                st.markdown(f"**Chapter {i+1}**")
                try:
                    if str(p).endswith((".webm", ".mp4")):
                        st.video(p)
                    else:
                        st.image(p)
                    with open(p, "rb") as f:
                        st.download_button(
                            f"Download chapter {i+1}",
                            f,
                            file_name=f"chapter_{i+1}.webm" if str(p).endswith(".webm") else f"chapter_{i+1}.png",
                            key=f"dl_{i}",
                        )
                except Exception as e:
                    st.write(p, e)
        elif media.get("note"):
            st.info(media.get("note"))
    except Exception as e:
        st.warning(f"Media step skipped: {e}")

    with st.expander("TGRM cycle log"):
        for entry in result.get("log", []):
            st.write(f"Cycle {entry.get('cycle')} · {entry.get('phase')} · RYE {entry.get('rye', 0)}")
            st.write(entry.get("notes", []))

    with st.expander("Scar memory & MSIL"):
        st.json({"scars": result.get("scars", []), "msil": result.get("msil", {})})

    st.subheader("NFT-style traits")
    st.json({
        "name": title,
        "description": premise[:200],
        "attributes": [
            {"trait_type": "RYE", "value": m.get("rye")},
            {"trait_type": "MSIL_verdict", "value": result.get("msil", {}).get("verdict")},
            {"trait_type": "TGRM_cycles", "value": m.get("cycles")},
            {"trait_type": "format", "value": fmt},
            {"trait_type": "tau", "value": SCIENCE.get("tau", 0.6)},
        ],
    })
else:
    st.info("Fill the brief in the sidebar and click **Run TGRM Pipeline**.")
    st.markdown("### Five Laws of Reparodynamics (cinema)")
    for law in FIVE_LAWS:
        st.markdown(f"**{law.get('name', '')}** — {law.get('cinema', '')}")
