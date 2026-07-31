"""One guided workflow for autonomous, memory-backed film production."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.autonomous_director import (
    AutonomousDirectorError,
    advance_autonomous_production,
    load_autonomous_state,
    prepare_autonomous_project,
    start_autonomous_production,
)
from silver_screen.render_planning import build_render_plan
from silver_screen.runtime import list_runs
from silver_screen.science import FORMATS, GENRES, SCIENCE, TONES

st.set_page_config(
    page_title="Silver-Screen | Autonomous Director",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _run_label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    return (
        f"{record.get('runId')} | {record.get('status')} | "
        f"{brief.get('title') or record.get('title') or 'Untitled'}"
    )


def _download(label: str, path: str | None, key: str, mime: str) -> None:
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


def _progress(bar, text):
    def callback(stage: str, percent: int, message: str) -> None:
        bar.progress(max(0, min(100, int(percent))), text=message)
        text.caption(f"{stage.replace('_', ' ').title()} | {percent}%")

    return callback


def _show_output(payload: dict[str, Any]) -> None:
    result = payload.get("result") or {}
    autonomous = payload.get("autonomous") or {}
    quality = payload.get("projectQuality") or autonomous.get("evidence", {}).get("projectQuality") or {}
    status = str(payload.get("status") or autonomous.get("status") or result.get("status") or "unknown")
    if status == "complete":
        st.success("Autonomous production completed and passed its project quality target.")
    elif status == "attention":
        st.warning("The production completed, but at least one quality target still needs director review.")
    else:
        st.info(f"Autonomous production checkpoint: **{status}**")
    columns = st.columns(6)
    columns[0].metric("Project quality", f"{float(quality.get('scorePercent', 0) or 0):.1f}/100")
    columns[1].metric("Confidence", f"{float(quality.get('confidence', 0) or 0) * 100:.0f}%")
    columns[2].metric("Verified clips", f"{quality.get('verifiedShots', 0)}/{quality.get('plannedShots', 0)}")
    columns[3].metric("Visual", f"{float(quality.get('visual', 0) or 0) * 100:.1f}")
    columns[4].metric("Semantic", f"{float(quality.get('semantic', 0) or 0) * 100:.1f}")
    columns[5].metric("Transitions", f"{float(quality.get('transitions', 0) or 0) * 100:.1f}")

    media = result.get("media") or {}
    voice = media.get("voice") or {}
    master = payload.get("deliveryMaster") or autonomous.get("deliveryMaster") or {}
    playable = (
        master.get("outputPath")
        or voice.get("captionedVideoPath")
        or voice.get("outputVideoPath")
        or media.get("final_cinematic_video_path")
        or media.get("final_video_path")
        or media.get("partial_cinematic_video_path")
        or media.get("partial_video_path")
    )
    if playable and Path(str(playable)).exists():
        st.subheader("Current autonomous cut")
        st.video(playable)
        _download("Download current master", playable, "autonomous-master", "video/mp4")

    tabs = st.tabs(["Stages", "Retakes", "Evidence", "World memory"])
    with tabs[0]:
        st.json(autonomous.get("stages") or {})
        if autonomous.get("warnings"):
            st.warning("\n".join(str(item) for item in autonomous["warnings"]))
    with tabs[1]:
        retakes = autonomous.get("retakes") or []
        if retakes:
            st.dataframe(retakes, use_container_width=True, hide_index=True)
        else:
            st.caption("No autonomous retake was required or authorized yet.")
    with tabs[2]:
        evidence = payload.get("evidence") or autonomous.get("evidence") or {}
        if isinstance(evidence.get("report"), dict):
            st.json(evidence["report"])
        _download(
            "Download evidence JSON",
            evidence.get("jsonPath"),
            "evidence-json",
            "application/json",
        )
        _download(
            "Download evidence HTML",
            evidence.get("htmlPath"),
            "evidence-html",
            "text/html",
        )
    with tabs[3]:
        state = result.get("state") or {}
        memory = state.get("productionMemory") or result.get("productionMemory") or autonomous.get("productionMemory") or {}
        st.json(memory)


st.title("🎬 Autonomous Director")
st.caption(SCIENCE["credit"])
st.write(
    "Give Silver-Screen a story, authorized references, a quality standard, and a "
    "budget. It selects the strongest provider-free production plan, builds persistent "
    "world memory, locks the approved shot ledger, routes shots, generates footage, "
    "reviews the actual clips, preserves accepted candidates, repairs only weak units, "
    "finishes transitions, and produces an evidence-backed delivery master."
)
st.info(
    "Planning, world memory, model recommendations, storyboard cards, animatic, and "
    "quality reports are local. Replicate is called only after the single provider-budget authorization below."
)

with st.sidebar:
    st.header("Project")
    title = st.text_input("Title", placeholder="Untitled film")
    premise = st.text_area(
        "Premise",
        "A capable but overlooked person discovers that a routine handoff was designed to expose them and must identify who controls the operation before the evidence disappears.",
        height=150,
        max_chars=5000,
    )
    genre = st.selectbox("Genre", list(GENRES), index=list(GENRES).index("thriller") if "thriller" in GENRES else 0)
    tone = st.selectbox("Tone", list(TONES), index=list(TONES).index("cinematic") if "cinematic" in TONES else 0)
    format_key = st.selectbox(
        "Story format",
        list(FORMATS),
        format_func=lambda item: f"{FORMATS[item]['label']} | {FORMATS[item]['minutes']} min blueprint",
    )
    exact_script = st.text_area(
        "Exact script (optional)",
        height=190,
        placeholder="INT. HOTEL CORRIDOR - NIGHT\nCODY studies the reflection...",
    )

    st.divider()
    st.header("Cast and memory")
    lead_mode = st.selectbox(
        "Lead",
        ["Original fictional lead", "Star as myself", "Authorized real person", "Authorized character"],
        index=0,
    )
    lead_name = st.text_input("Lead name", value="Cody" if lead_mode == "Star as myself" else "")
    lead_role = st.text_input("Lead role", value="Lead protagonist")
    lead_description = st.text_area(
        "Lead identity and appearance contract",
        height=110,
        placeholder="Face, hair, build, age appearance, posture, distinguishing features, and performance style.",
    )
    wardrobe = st.text_area(
        "Locked wardrobe",
        height=80,
        placeholder="Describe the wardrobe state that must remain consistent.",
    )
    support_name = st.text_input("Supporting character", value="Mara")
    support_description = st.text_area(
        "Supporting character contract",
        "A composed operational counterpoint with a private objective and visually distinct wardrobe.",
        height=90,
    )
    references = st.file_uploader(
        "Authorized reference images",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        help="References are stored as production assets. Silver-Screen does not identify people or create biometric embeddings.",
    ) or []
    project_id = st.text_input(
        "Persistent project ID",
        placeholder="Optional. Reuse the same ID across sequels or episodes.",
    )
    project_notes = st.text_area(
        "World rules and permanent memory",
        height=120,
        placeholder="Locations, props, relationships, chronology, visual rules, prohibited changes, and facts future shots must remember.",
    )
    likeness_authorized = st.checkbox(
        "I am the subject shown, or I have explicit permission to use every uploaded likeness and character design.",
        value=False,
        disabled=not references and lead_mode == "Original fictional lead",
    )

    st.divider()
    st.header("Autonomous quality")
    profile = st.selectbox(
        "Production profile",
        ["prestige", "blockbuster", "efficient", "custom"],
        index=0,
        format_func=lambda item: {
            "prestige": "Prestige balance",
            "blockbuster": "Maximum quality",
            "efficient": "Cost-efficient",
            "custom": "Custom",
        }[item],
    )
    render_mode = st.radio(
        "Render length",
        ["One-clip screen test", "Match blueprint", "Custom runtime"],
        index=0,
    )
    custom_runtime = None
    if render_mode == "Custom runtime":
        custom_runtime = st.number_input(
            "Runtime seconds", min_value=4, max_value=5400, value=60, step=8
        )
    plan = build_render_plan(
        format_key,
        mode={
            "One-clip screen test": "preview",
            "Match blueprint": "match_blueprint",
            "Custom runtime": "custom",
        }[render_mode],
        custom_runtime_seconds=custom_runtime,
        clip_duration_seconds=8,
    )
    st.caption(
        f"Planned runtime: **{plan.runtime_seconds}s** | clips: **{plan.planned_clips}**"
    )
    continuous = st.checkbox(
        "Continue autonomously until complete or a hard budget/provider gate",
        value=True,
    )
    auto_retakes = st.checkbox("Automatically compare and repair weak clips", value=True)
    semantic_review = st.checkbox(
        "Use semantic frame review when OpenAI API is available",
        value=True,
        help="When enabled and configured, sampled frames are sent to OpenAI only for shot-contract comparison. No identity recognition is requested.",
    )
    max_calls_default = max(1, plan.planned_clips * (3 if profile in {"prestige", "blockbuster"} else 2))
    max_calls = st.number_input(
        "Maximum Replicate calls for the whole production",
        min_value=1,
        max_value=1000,
        value=min(1000, max_calls_default),
        step=1,
    )
    cost_per_second = st.number_input(
        "Current video cost per generated second (USD, optional)",
        min_value=0.0,
        max_value=1000.0,
        value=0.0,
        step=0.01,
        format="%.4f",
    )
    max_spend = st.number_input(
        "Maximum estimated video spend (USD, 0 disables spend gate)",
        min_value=0.0,
        max_value=1000000.0,
        value=0.0,
        step=1.0,
    )
    voice_enabled = st.checkbox("Finish with generated dialogue/narration", value=False)
    voice_provider = st.selectbox(
        "Voice provider",
        ["openai", "elevenlabs"],
        disabled=not voice_enabled,
    )
    delivery = st.selectbox("Delivery master", ["1080p", "4k", "source"], index=0)

brief: dict[str, Any] = {
    "title": title.strip() or "Untitled Film",
    "premise": premise.strip(),
    "genre": genre,
    "tone": tone,
    "format": format_key,
    "cast": [
        {
            "name": lead_name.strip() or "Lead",
            "role": lead_role.strip() or "Lead protagonist",
            "description": (
                f"IDENTITY AND PERFORMANCE CONTRACT: {lead_description.strip()} "
                f"LOCKED WARDROBE: {wardrobe.strip()}"
            ).strip(),
        },
        {
            "name": support_name.strip() or "Supporting Character",
            "role": "Supporting character and dramatic counterpoint",
            "description": support_description.strip(),
        },
    ],
    "creativeDirection": {
        "profile": "grounded_prestige" if profile != "blockbuster" else "modern_spy_thriller",
        "scriptSource": "authored" if exact_script.strip() else "generated",
        "strictGate": True,
    },
    "shotDirection": {
        "audioStrategy": "dub_later",
        "coverageGate": True,
    },
    "productionMemory": {
        "projectId": project_id.strip(),
        "projectNotes": project_notes.strip(),
        "world": {
            "characters": {
                (lead_name.strip() or "Lead"): {
                    "identityContract": lead_description.strip(),
                    "wardrobe": wardrobe.strip(),
                    "locked": True,
                }
            },
            "storyRules": [line.strip() for line in project_notes.splitlines() if line.strip()],
        },
    },
    "projectId": project_id.strip() or None,
    "loadProjectMemory": bool(project_id.strip()),
}
if exact_script.strip():
    brief["authoredScript"] = exact_script.strip()

config = {
    "profile": profile,
    "continuous": continuous,
    "autoRetakes": auto_retakes,
    "semanticReview": semantic_review,
    "maxProviderCalls": int(max_calls),
    "maxSpendUsd": float(max_spend),
    "costPerSecondUsd": float(cost_per_second),
    "voiceEnabled": voice_enabled,
    "voiceProvider": voice_provider,
    "deliveryMaster": delivery,
    "projectId": project_id.strip(),
    "projectNotes": project_notes.strip(),
}

blockers = []
if len(premise.strip()) < 12:
    blockers.append("Enter a premise with at least 12 characters.")
if not (lead_name.strip() or lead_mode == "Original fictional lead"):
    blockers.append("Enter a lead name.")
if lead_mode in {"Star as myself", "Authorized real person"} and not references:
    blockers.append("A real-person production needs at least one authorized reference image.")
if references and not likeness_authorized:
    blockers.append("Confirm permission for the uploaded references.")
if lead_mode == "Authorized character" and not likeness_authorized:
    blockers.append("Confirm permission to use the character design.")

left, right = st.columns([1, 1])
with left:
    build_clicked = st.button(
        "Build free autonomous plan",
        type="primary",
        use_container_width=True,
        disabled=bool(blockers),
    )
with right:
    if blockers:
        st.error("Start blockers:\n\n" + "\n".join(f"• {item}" for item in blockers))

if build_clicked:
    try:
        with st.spinner("Selecting the strongest screenplay, shot ledger, routing plan, and animatic..."):
            st.session_state["autonomous-plan"] = prepare_autonomous_project(
                brief,
                target_runtime_seconds=plan.runtime_seconds,
                clip_duration_seconds=8,
                max_shots=plan.planned_clips,
                config=config,
            )
    except Exception as exc:
        st.error(str(exc))

prepared = st.session_state.get("autonomous-plan")
if prepared:
    st.subheader("Provider-free production plan")
    score = prepared.get("planningScore") or {}
    metrics = st.columns(5)
    metrics[0].metric("Planning score", f"{float(score.get('scorePercent', 0) or 0):.1f}/100")
    metrics[1].metric("Selected attempt", prepared.get("selectedAttempt"))
    metrics[2].metric("Planned shots", len((prepared.get("animatic") or {}).get("shots") or []))
    metrics[3].metric("Provider calls", 0)
    metrics[4].metric("Ledger", str(((prepared.get("preview") or {}).get("promptLedger") or {}).get("ledgerHash") or "")[:10])
    plan_tabs = st.tabs(["Animatic", "Screenplay", "Shot contracts", "Model routing", "Memory"])
    with plan_tabs[0]:
        st.dataframe((prepared.get("animatic") or {}).get("shots") or [], use_container_width=True, hide_index=True)
    with plan_tabs[1]:
        st.text_area(
            "Selected screenplay",
            str((prepared.get("preview") or {}).get("screenplay") or ""),
            height=520,
        )
    with plan_tabs[2]:
        st.dataframe((prepared.get("preview") or {}).get("prompts") or [], use_container_width=True, hide_index=True)
    with plan_tabs[3]:
        st.json(prepared.get("routingPlan") or {})
    with plan_tabs[4]:
        st.json(((prepared.get("preview") or {}).get("state") or {}).get("productionMemory") or {})

    st.warning(
        f"This authorization permits at most **{int(max_calls)} Replicate calls**"
        + (f" and an estimated **${float(max_spend):,.2f}** ceiling" if max_spend else "")
        + ". Silver-Screen may stop earlier when the quality target is reached."
    )
    authorize = st.checkbox(
        "I authorize Silver-Screen to execute this approved autonomous plan within the displayed provider-call and spend ceilings.",
        value=False,
    )
    start_clicked = st.button(
        "Produce this film autonomously",
        type="primary",
        use_container_width=True,
        disabled=not authorize,
    )
    if start_clicked:
        config["authorized"] = True
        config["authorizationText"] = (
            f"Authorized {int(max_calls)} provider calls and ${float(max_spend):.2f} estimated spend ceiling."
        )
        bar = st.progress(0, text="Starting autonomous production")
        message = st.empty()
        try:
            with st.spinner("Generating, verifying, repairing, and finishing the film..."):
                output = start_autonomous_production(
                    brief,
                    images=list(references),
                    output_root="runs",
                    target_runtime_seconds=plan.runtime_seconds,
                    clip_duration_seconds=8,
                    max_shots=plan.planned_clips,
                    batch_size=1,
                    config=config,
                    progress=_progress(bar, message),
                )
            st.session_state["autonomous-output"] = output
        except (AutonomousDirectorError, Exception) as exc:
            st.error(str(exc))

if st.session_state.get("autonomous-output"):
    _show_output(st.session_state["autonomous-output"])

st.divider()
st.header("Continue a durable autonomous production")
records = []
for record in list_runs("runs", 100):
    workspace = Path(str(record.get("workspace") or ""))
    if (workspace / "autonomous_director.json").exists():
        records.append(record)
run_map = {_run_label(record): record for record in records}
selected = st.selectbox(
    "Autonomous production",
    list(run_map) or ["No autonomous productions"],
    disabled=not run_map,
)
continue_clicked = st.button(
    "Continue autonomous production",
    use_container_width=True,
    disabled=not run_map,
)
if continue_clicked and run_map:
    run_id = str(run_map[selected].get("runId") or "")
    bar = st.progress(0, text="Continuing autonomous production")
    message = st.empty()
    try:
        with st.spinner("Continuing from the durable checkpoint..."):
            output = advance_autonomous_production(
                run_id,
                output_root="runs",
                resume_generation=True,
                progress=_progress(bar, message),
            )
        st.session_state["autonomous-output"] = output
        _show_output(output)
    except Exception as exc:
        st.error(str(exc))

with st.expander("Autonomous Director state", expanded=False):
    if run_map:
        try:
            run_id = str(run_map[selected].get("runId") or "")
            st.json(load_autonomous_state(run_id, output_root="runs"))
        except Exception:
            pass
