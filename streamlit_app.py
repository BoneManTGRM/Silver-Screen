"""Silver-Screen — Streamlit primary studio (Reparodynamics · TGRM).

Brief → full multi-act script → TGRM repair → optional chapter/hero reels.
Designed to launch cleanly and never crash if media libs fail.
"""
from __future__ import annotations

import streamlit as st

from silver_screen.science import SCIENCE, FIVE_LAWS, FORMATS
from silver_screen.pipeline import run_pipeline

st.set_page_config(
    page_title="Silver-Screen · TGRM Studio",
    page_icon="🎥",
    layout="wide",
)

st.title("🎥 Silver-Screen")
st.caption(SCIENCE.get("credit", "A Reparodynamics Production · TGRM · RYE · MSIL"))
st.markdown(f"**{SCIENCE.get('tagline', '')}**")

with st.sidebar:
    st.header("Brief")
    title = st.text_input("Title (optional — auto-invented if blank)", value="")
    premise = st.text_area(
        "Premise",
        value=(
            "A repair technician discovers that every system she fixes "
            "remembers the pain of the break and begins to dream."
        ),
        height=120,
    )
    genre = st.selectbox(
        "Genre",
        ["scifi", "noir", "drama", "thriller", "fantasy", "horror", "western", "romance"],
        index=0,
        format_func=lambda g: {
            "scifi": "Sci-Fi",
            "noir": "Noir",
            "drama": "Drama",
            "thriller": "Thriller",
            "fantasy": "Fantasy",
            "horror": "Horror",
            "western": "Western",
            "romance": "Romance",
        }.get(g, g),
    )
    tone = st.selectbox(
        "Tone",
        ["cinematic", "intimate", "epic", "melancholy", "tense", "hopeful"],
        index=0,
    )
    fmt_keys = list(FORMATS.keys())
    fmt = st.selectbox(
        "Format",
        fmt_keys,
        index=fmt_keys.index("short") if "short" in fmt_keys else 0,
        format_func=lambda k: f"{FORMATS[k].get('label', k)} ({FORMATS[k].get('minutes', '?')} min)",
    )
    st.divider()
    st.subheader("Your Images (optional)")
    imgs = st.file_uploader(
        "Character portraits",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
    )
    voices = st.file_uploader(
        "Voice samples (metadata only)",
        type=["wav", "mp3", "m4a"],
        accept_multiple_files=True,
    )
    st.caption("Portraits are composited into chapter cards/reels when Pillow + moviepy work.")

if st.button("Run TGRM Pipeline", type="primary"):
    if not premise.strip():
        st.error("Please enter a premise.")
        st.stop()

    brief = {
        "title": title.strip() or None,
        "premise": premise.strip(),
        "genre": genre,
        "tone": tone,
        "format": fmt,
    }

    with st.spinner("Building multi-act screenplay → TGRM repair → media..."):
        try:
            out = run_pipeline(brief, images=imgs, voices=voices)
        except Exception as e:
            st.error(f"Pipeline failed: {e}")
            st.exception(e)
            st.stop()

    state = out.get("state") or {}
    metrics = out.get("metrics") or {}
    msil = out.get("msil") or {}
    media = out.get("media") or {}
    log = out.get("log") or []
    scars = out.get("scars") or []

    film_title = state.get("title") or title or "Untitled"
    st.success(f"Complete — **{film_title}**")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("RYE", f"{float(metrics.get('rye', 0)):.4f}")
    c2.metric("ΔR", f"{float(metrics.get('deltaR', 0)):.4f}")
    c3.metric("Energy", metrics.get("energy", 0))
    c4.metric("MSIL", str(msil.get("verdict", "—")).upper())

    if state.get("logline"):
        st.markdown(f"**Logline:** {state['logline']}")

    chars = state.get("characters") or []
    if chars:
        st.subheader("Cast")
        for ch in chars:
            if isinstance(ch, dict):
                st.markdown(
                    f"- **{ch.get('name', '?')}** — {ch.get('role', '')} "
                    f"({ch.get('arc', '')})"
                )

    st.subheader("Repaired screenplay")
    st.text_area(
        "Script",
        state.get("script", ""),
        height=360,
        label_visibility="collapsed",
    )

    # Media output
    st.subheader("Chapter / Hero media")
    if media.get("hero_path"):
        st.markdown("**Hero reel**")
        try:
            st.video(media["hero_path"])
            with open(media["hero_path"], "rb") as f:
                st.download_button(
                    "Download hero reel",
                    f,
                    file_name="hero_reel.webm",
                    key="dl_hero",
                )
        except Exception as e:
            st.info(f"Hero path: `{media['hero_path']}` ({e})")

    paths = media.get("chapter_paths") or []
    if paths:
        for i, p in enumerate(paths):
            st.markdown(f"**Chapter {i + 1}**")
            try:
                pl = str(p).lower()
                if pl.endswith((".webm", ".mp4", ".mov")):
                    st.video(p)
                else:
                    st.image(p)
                with open(p, "rb") as f:
                    name = f"chapter_{i + 1}.webm" if pl.endswith(".webm") else f"chapter_{i + 1}.png"
                    st.download_button(
                        f"Download chapter {i + 1}",
                        f,
                        file_name=name,
                        key=f"dl_ch_{i}",
                    )
            except Exception as e:
                st.write(p, e)
    else:
        note = media.get("note") or media.get("error") or "No reels produced (install moviepy + ffmpeg for video; stills may still appear)."
        st.info(note)

    with st.expander("TGRM cycle log", expanded=False):
        for entry in log:
            st.markdown(
                f"**Cycle {entry.get('cycle')} · {entry.get('phase')} · "
                f"RYE {entry.get('rye', 0)}**"
            )
            st.write(entry.get("notes", []))
            if entry.get("correction"):
                st.caption(f"Correction: {entry['correction']}")

    with st.expander("Scar memory & MSIL", expanded=False):
        st.json({"scars": scars, "msil": msil})

    st.subheader("NFT-style traits")
    st.json(
        {
            "name": film_title,
            "description": (state.get("premise") or premise)[:200],
            "attributes": [
                {"trait_type": "RYE", "value": metrics.get("rye")},
                {"trait_type": "MSIL_verdict", "value": msil.get("verdict")},
                {"trait_type": "TGRM_cycles", "value": metrics.get("cycles")},
                {"trait_type": "micro_repairs", "value": metrics.get("microRepairs")},
                {"trait_type": "full_repairs", "value": metrics.get("fullRepairs")},
                {"trait_type": "format", "value": fmt},
                {"trait_type": "tau", "value": SCIENCE.get("tau", 0.6)},
                {"trait_type": "genre", "value": genre},
            ],
        }
    )
else:
    st.info("Fill the brief in the sidebar and click **Run TGRM Pipeline**.")
    st.markdown("### Five Laws of Reparodynamics (cinema)")
    for law in FIVE_LAWS:
        st.markdown(f"**{law.get('name', '')}** — {law.get('cinema', '')}")
    st.markdown("### Formats")
    for key, meta in FORMATS.items():
        st.markdown(
            f"- **{meta.get('label', key)}** — {meta.get('minutes')} min · "
            f"{meta.get('acts')} acts · {meta.get('chapters')} chapters · {meta.get('hint', '')}"
        )
