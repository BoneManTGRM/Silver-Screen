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

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("RYE", f"{float(metrics.get('rye', 0)):.4f}")
    c2.metric("ΔR", f"{float(metrics.get('deltaR', 0)):.4f}")
    c3.metric("Energy", metrics.get("energy", 0))
    c4.metric("Repairs", int(metrics.get("microRepairs", 0)) + int(metrics.get("fullRepairs", 0)))
    c5.metric("MSIL", str(msil.get("verdict", "—")).upper())

    if state.get("logline"):
        st.markdown(f"**Logline:** {state['logline']}")

    chars = state.get("characters") or []
    if chars:
        st.subheader("Cast")
        for ch in chars:
            if isinstance(ch, dict):
                st.markdown(
                    f"- **{ch.get('name', '?')}** — {ch.get('role', '')} "
                    f"· _{ch.get('arc', '')}_  \n  {ch.get('description', '')}"
                )

    acts = state.get("acts") or []
    scenes = state.get("scenes") or []
    if acts or scenes:
        with st.expander(f"Structure — {len(acts)} act(s), {len(scenes)} scene(s)", expanded=False):
            for a in acts:
                if isinstance(a, dict):
                    st.markdown(
                        f"**Act {a.get('number')} · {a.get('title', '')}** "
                        f"({a.get('scene_count', 0)} scenes) — {a.get('purpose', '')}"
                    )
            if scenes:
                rows = []
                for s in scenes:
                    if isinstance(s, dict):
                        rows.append({
                            "#": s.get("number"),
                            "Act": s.get("act"),
                            "Ch": s.get("chapter"),
                            "Slugline": s.get("slugline", ""),
                            "Summary": (s.get("summary") or "")[:80],
                        })
                if rows:
                    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader("Repaired screenplay")
    script_text = state.get("script") or ""
    st.text_area("Script", script_text, height=420, label_visibility="collapsed")
    if script_text:
        st.download_button(
            "Download screenplay (.txt)",
            script_text,
            file_name=f"{(film_title or 'screenplay').replace(' ', '_').lower()}.txt",
            mime="text/plain",
            key="dl_script",
        )

    col_log, col_scar = st.columns(2)
    with col_log:
        st.subheader("TGRM cycles")
        if log:
            for e in log:
                if not isinstance(e, dict):
                    continue
                phase = e.get("phase", "?")
                note = e.get("correction") or (
                    "; ".join(e.get("notes") or []) if e.get("notes") else "—"
                )
                st.markdown(
                    f"**Cycle {e.get('cycle', '?')} · {phase}**  \n"
                    f"ΔR={e.get('deltaR', 0)} · RYE={e.get('rye', 0)}  \n"
                    f"{note}"
                )
        else:
            st.caption("No cycle log.")
    with col_scar:
        st.subheader("Scar memory")
        if scars:
            for sc in scars:
                if isinstance(sc, dict):
                    st.markdown(
                        f"- `{sc.get('fracture_class', '?')}` → {sc.get('fix', '')} "
                        f"(RYE {sc.get('rye', 0)})"
                    )
        else:
            st.caption("No scars reinforced this run.")

    st.subheader("Chapter / Hero media")
    if media.get("note"):
        st.caption(media["note"])
    if media.get("error"):
        st.warning(f"Media note: {media['error']}")

    hero = media.get("hero_path")
    if hero:
        st.markdown("**Hero reel**")
        try:
            st.video(hero)
            with open(hero, "rb") as f:
                st.download_button(
                    "Download hero reel",
                    f,
                    file_name="hero_reel.mp4",
                    key="dl_hero",
                )
        except Exception as e:
            st.info(f"Hero path: `{hero}` ({e})")

    paths = media.get("chapter_paths") or []
    if paths:
        st.markdown("**Chapters**")
        cols = st.columns(min(3, len(paths)))
        for i, p in enumerate(paths):
            with cols[i % len(cols)]:
                try:
                    if str(p).endswith((".mp4", ".webm", ".mov")):
                        st.video(p)
                    else:
                        st.image(p, use_container_width=True)
                    with open(p, "rb") as f:
                        st.download_button(
                            f"Download ch {i + 1}",
                            f,
                            file_name=f"chapter_{i + 1}"
                            + (".mp4" if str(p).endswith(".mp4") else ".png"),
                            key=f"dl_ch_{i}",
                        )
                except Exception as e:
                    st.caption(f"{p}: {e}")
    elif not hero:
        st.info("No chapter media generated (Pillow/moviepy/ffmpeg unavailable or empty film).")

else:
    st.info("Fill the brief in the sidebar and click **Run TGRM Pipeline**.")
    st.subheader("Five Laws of Reparodynamics (cinema)")
    for law in FIVE_LAWS:
        st.markdown(f"**{law.get('name', '')}** — {law.get('cinema', '')}")
    st.subheader("Formats")
    for k, v in FORMATS.items():
        st.markdown(
            f"- **{v.get('label', k)}** — {v.get('minutes', '?')} min · "
            f"{v.get('acts', '?')} acts · {v.get('chapters', '?')} chapters · "
            f"{v.get('hint', '')}"
        )
