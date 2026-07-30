"""Silver-Screen operational Streamlit studio."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.health import health_report
from silver_screen.pipeline import BriefValidationError, PipelineError, run_pipeline
from silver_screen.runtime import list_runs
from silver_screen.science import FIVE_LAWS, FORMATS, GENRES, SCIENCE, TONES

st.set_page_config(
    page_title="Silver-Screen | TGRM Studio",
    page_icon="🎥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 4rem;}
      [data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.25); padding: .75rem; border-radius: .7rem;}
      .ss-kicker {letter-spacing: .12em; text-transform: uppercase; opacity: .72; font-size: .78rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def _read_bytes(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _artifact_download(
    label: str,
    path: str | None,
    *,
    mime: str,
    key: str,
) -> None:
    data = _read_bytes(path)
    if data is None:
        st.caption(f"{label} is not available on disk.")
        return
    st.download_button(
        label,
        data=data,
        file_name=Path(path).name if path else "artifact",
        mime=mime,
        key=key,
        use_container_width=True,
    )


def _format_label(key: str) -> str:
    meta = FORMATS[key]
    return f"{meta['label']} | {meta['minutes']} min | {meta['scenes']} scenes"


def _progress_callback(progress_bar, progress_text, status_box):
    def callback(stage: str, percent: int, message: str) -> None:
        progress_bar.progress(percent, text=message)
        progress_text.caption(f"{stage.replace('_', ' ').title()} | {percent}%")
        try:
            status_box.update(label=message, state="running")
        except Exception:
            pass

    return callback


st.markdown('<div class="ss-kicker">Reparodynamics production system</div>', unsafe_allow_html=True)
st.title("🎥 Silver-Screen")
st.caption(SCIENCE["credit"])
st.write(SCIENCE["tagline"])

with st.sidebar:
    st.header("Production brief")
    title = st.text_input("Title", placeholder="Optional. A title will be generated when blank.")
    premise = st.text_area(
        "Premise",
        value=(
            "A repair technician discovers that every system she fixes remembers the pain "
            "of the break and begins to dream."
        ),
        height=150,
        max_chars=4000,
    )
    genre = st.selectbox(
        "Genre",
        list(GENRES),
        index=list(GENRES).index("scifi"),
        format_func=lambda value: "Sci-Fi" if value == "scifi" else value.title(),
    )
    tone = st.selectbox("Tone", list(TONES), index=list(TONES).index("cinematic"), format_func=str.title)
    format_keys = list(FORMATS)
    fmt = st.selectbox(
        "Format",
        format_keys,
        index=format_keys.index("short"),
        format_func=_format_label,
    )
    seed_text = st.text_input(
        "Generation seed",
        value="",
        help="Leave blank for a deterministic seed derived from the brief.",
    )

    with st.expander("TGRM controls", expanded=False):
        max_cycles = st.slider("Maximum repair cycles", min_value=1, max_value=20, value=8)
        energy_budget = st.slider("Energy budget", min_value=3, max_value=120, value=40)

    with st.expander("Media controls", expanded=False):
        media_mode = st.selectbox(
            "Media output",
            ["cards", "chapters", "hero", "off"],
            format_func=lambda value: {
                "cards": "PNG chapter cards",
                "chapters": "Cards plus chapter videos",
                "hero": "Cards, chapter videos, and hero reel",
                "off": "No media rendering",
            }[value],
        )
        max_chapters = st.slider("Maximum rendered chapters", min_value=1, max_value=12, value=4)
        images = st.file_uploader(
            "Character portraits",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
        )
        voices = st.file_uploader(
            "Voice files for inventory only",
            type=["wav", "mp3", "m4a"],
            accept_multiple_files=True,
            help="This release records voice-file metadata but does not clone or synthesize a person's voice.",
        )

    with st.expander("Operations", expanded=False):
        output_root = st.text_input(
            "Run workspace root",
            value=os.getenv("SILVER_SCREEN_RUNS_DIR", "runs"),
        )
        st.caption("Each run receives a durable manifest, screenplay, JSON state, TGRM audit, and ZIP bundle.")

    run_clicked = st.button("Run production pipeline", type="primary", use_container_width=True)
    if st.button("Clear current result", use_container_width=True):
        st.session_state.pop("silver_screen_result", None)
        st.rerun()

    with st.expander("Runtime health", expanded=False):
        health = health_report(output_root)
        st.write(f"**Status:** {health['status'].upper()}")
        st.json(health["checks"], expanded=False)
        for note in health["notes"]:
            st.caption(note)

    with st.expander("Recent durable runs", expanded=False):
        recent = list_runs(output_root, limit=8)
        if not recent:
            st.caption("No runs found in this workspace.")
        for record in recent:
            record_title = record.get("title") or (record.get("brief") or {}).get("title") or "Untitled"
            st.caption(
                f"{record.get('runId', '?')} | {record.get('status', '?')} | {record_title}"
            )

if run_clicked:
    brief: dict[str, Any] = {
        "title": title,
        "premise": premise,
        "genre": genre,
        "tone": tone,
        "format": fmt,
    }
    if seed_text.strip():
        brief["seed"] = seed_text.strip()

    progress_bar = st.progress(0, text="Preparing production run")
    progress_text = st.empty()
    with st.status("Starting Silver-Screen", expanded=True) as status:
        try:
            result = run_pipeline(
                brief,
                images=images,
                voices=voices,
                output_root=output_root,
                persist=True,
                render_media=media_mode != "off",
                video_mode="cards" if media_mode == "off" else media_mode,
                max_chapters=max_chapters,
                max_cycles=max_cycles,
                energy_budget=energy_budget,
                progress=_progress_callback(progress_bar, progress_text, status),
            )
            st.session_state["silver_screen_result"] = result
            status.update(label="Production run complete", state="complete", expanded=False)
            progress_bar.progress(100, text="Production run complete")
        except BriefValidationError as exc:
            status.update(label="Brief requires correction", state="error")
            st.error(str(exc))
        except PipelineError as exc:
            status.update(label="Pipeline stopped safely", state="error")
            run_suffix = f" Run ID: {exc.run_id}." if exc.run_id else ""
            st.error(f"{exc}{run_suffix}")
            if os.getenv("SILVER_SCREEN_DEBUG", "0") == "1":
                st.exception(exc)

result = st.session_state.get("silver_screen_result")
if not result:
    st.info("Complete the brief in the sidebar, then run the production pipeline.")
    col_laws, col_formats = st.columns(2)
    with col_laws:
        st.subheader("Five repair laws")
        for law in FIVE_LAWS:
            st.markdown(f"**{law['name']}**  \n{law['cinema']}")
    with col_formats:
        st.subheader("Operational outputs")
        st.markdown(
            """
            - Deterministic story bible, cast, acts, chapters, scenes, and shot plan
            - Bounded TGRM repair with verification and rollback
            - Durable manifest and replayable generation seed
            - Screenplay, outline, audit JSON, media artifacts, and ZIP bundle
            - CLI, health diagnostics, container configuration, tests, and CI
            """
        )
    st.stop()

state = result.get("state") or {}
metrics = result.get("metrics") or {}
msil = result.get("msil") or {}
run_meta = result.get("run") or {}
media = result.get("media") or {}
artifacts = result.get("artifacts") or {}
film_title = state.get("title") or "Untitled"

st.success(f"Complete: **{film_title}** | Run `{run_meta.get('id', '?')}`")
metric_columns = st.columns(6)
metric_columns[0].metric("Final score", f"{float(metrics.get('finalScore', 0)):.3f}")
metric_columns[1].metric("RYE", f"{float(metrics.get('rye', 0)):.4f}")
metric_columns[2].metric("Delta R", f"{float(metrics.get('deltaR', 0)):.4f}")
metric_columns[3].metric("Energy", f"{metrics.get('energy', 0)}/{metrics.get('energyBudget', 0)}")
metric_columns[4].metric("Accepted repairs", int(metrics.get("acceptedRepairs", 0)))
metric_columns[5].metric("MSIL", str(msil.get("verdict", "unknown")).upper())

if result.get("warnings"):
    with st.expander(f"Warnings ({len(result['warnings'])})", expanded=False):
        for warning in result["warnings"]:
            st.warning(warning)

overview_tab, story_tab, screenplay_tab, repair_tab, media_tab, operations_tab = st.tabs(
    ["Overview", "Story system", "Screenplay", "TGRM audit", "Media", "Operations"]
)

with overview_tab:
    st.subheader(state.get("logline") or "Production summary")
    left, right = st.columns(2)
    with left:
        st.markdown("**Production state**")
        st.json(
            {
                "format": state.get("format"),
                "targetMinutes": state.get("targetMinutes"),
                "seed": state.get("seed"),
                "scenes": len(state.get("scenes") or []),
                "acts": len(state.get("acts") or []),
                "chapters": len(state.get("chapters") or []),
                "status": state.get("status"),
                "stopReason": metrics.get("stopReason"),
            }
        )
    with right:
        st.markdown("**MSIL stability report**")
        st.json(msil)

with story_tab:
    bible = state.get("storyBible") or {}
    st.subheader("Story bible")
    st.json(bible)
    st.subheader("Cast")
    cast_rows = [
        {
            "Name": character.get("name"),
            "Role": character.get("role"),
            "Arc": character.get("arc"),
        }
        for character in state.get("characters") or []
        if isinstance(character, dict)
    ]
    if cast_rows:
        st.dataframe(cast_rows, use_container_width=True, hide_index=True)
    st.subheader("Scene plan")
    scene_rows = [
        {
            "Scene": scene.get("number"),
            "Act": scene.get("act"),
            "Chapter": scene.get("chapter"),
            "Slugline": scene.get("slugline"),
            "Purpose": scene.get("dramaticPurpose"),
            "Turn": scene.get("turn"),
        }
        for scene in state.get("scenes") or []
        if isinstance(scene, dict)
    ]
    st.dataframe(scene_rows, use_container_width=True, hide_index=True)

with screenplay_tab:
    script = str(state.get("script") or "")
    st.text_area("Screenplay blueprint", script, height=650, label_visibility="collapsed")
    st.download_button(
        "Download screenplay text",
        data=script.encode("utf-8"),
        file_name=f"{film_title.lower().replace(' ', '-')}-screenplay.txt",
        mime="text/plain",
        use_container_width=True,
    )

with repair_tab:
    st.subheader("Repair cycles")
    logs = result.get("log") or []
    if not logs:
        st.caption("No repair cycles were recorded.")
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        label = (
            f"Cycle {entry.get('cycle', '?')} | {entry.get('phase', '?')} | "
            f"Delta R {float(entry.get('deltaR', 0)):.4f}"
        )
        with st.expander(label, expanded=False):
            st.json(entry)
    st.subheader("Scar memory")
    st.json(result.get("scars") or [])
    st.subheader("Remaining fractures")
    remaining = result.get("remainingFractures") or []
    if remaining:
        st.json(remaining)
    else:
        st.success("No remaining fractures detected.")

with media_tab:
    st.caption(media.get("note") or "No media note was recorded.")
    if media.get("hero_path"):
        st.subheader("Hero reel")
        st.video(media["hero_path"])
    chapter_paths = media.get("chapter_paths") or []
    if chapter_paths:
        columns = st.columns(min(3, len(chapter_paths)))
        for index, path in enumerate(chapter_paths):
            with columns[index % len(columns)]:
                suffix = Path(path).suffix.lower()
                if suffix in {".mp4", ".webm", ".mov"}:
                    st.video(path)
                else:
                    st.image(path, use_container_width=True)
                _artifact_download(
                    f"Download chapter {index + 1}",
                    path,
                    mime="video/mp4" if suffix == ".mp4" else "image/png",
                    key=f"media_download_{index}",
                )
    else:
        st.info("No media artifacts were requested or generated.")

with operations_tab:
    left, right = st.columns(2)
    with left:
        st.subheader("Run record")
        st.json(
            {
                "run": run_meta,
                "options": result.get("options"),
                "timings": result.get("timings"),
            }
        )
    with right:
        st.subheader("Artifact downloads")
        _artifact_download("Download complete bundle", artifacts.get("bundle"), mime="application/zip", key="bundle")
        _artifact_download("Download film JSON", artifacts.get("film"), mime="application/json", key="film_json")
        _artifact_download("Download outline JSON", artifacts.get("outline"), mime="application/json", key="outline_json")
        _artifact_download("Download TGRM audit JSON", artifacts.get("tgrm"), mime="application/json", key="tgrm_json")
    with st.expander("Complete result JSON", expanded=False):
        st.download_button(
            "Download current result JSON",
            data=json.dumps(result, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
            file_name=f"{run_meta.get('id', 'silver-screen')}-result.json",
            mime="application/json",
            use_container_width=True,
        )
        st.json(result, expanded=False)
