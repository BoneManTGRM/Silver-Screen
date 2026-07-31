"""One-click Autonomous Studio for Silver-Screen 9."""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.autonomous_studio import (
    QUALITY_PROFILES, continue_autonomous_production,
    prepare_autonomous_plan, start_autonomous_production,
)
from silver_screen.runtime import list_resumable_runs
from silver_screen.science import GENRES, SCIENCE, TONES
from silver_screen.voice_providers import provider_capabilities

st.set_page_config(page_title="Silver-Screen | Autonomous Studio", page_icon="🎬", layout="wide", initial_sidebar_state="expanded")
st.title("Autonomous Studio")
st.caption(SCIENCE["credit"])
st.write(
    "Describe the movie once. Silver-Screen plans and locks the screenplay and shot deck, "
    "uses persistent project memory, routes every shot, generates durable footage, checks "
    "quality, repairs technical failures, smooths transitions, and creates an evidence-backed edit. "
    "Blockbuster target means maximum available orchestration; current video models still determine the visual ceiling."
)

replicate_ready = bool(os.getenv("REPLICATE_API_TOKEN", "").strip())
openai_ready = bool(os.getenv("OPENAI_API_KEY", "").strip())
voice_caps = provider_capabilities()

with st.sidebar:
    st.header("One-click movie")
    title = st.text_input("Title", placeholder="Optional")
    premise = st.text_area("What should the movie be about?", height=170, placeholder="A grounded high-stakes story with a specific lead, conflict, and decision.")
    exact_script = st.text_area("Exact script (optional)", height=180, help="Leave blank for a generated grounded screenplay. A pasted script becomes the binding source.")
    a, b = st.columns(2)
    genre = a.selectbox("Genre", list(GENRES), index=list(GENRES).index("thriller"))
    tone = b.selectbox("Tone", list(TONES), index=list(TONES).index("cinematic"))
    runtime_label = st.selectbox("Movie length", ["8-second screen test", "30-second cinematic sequence", "60-second trailer", "2-minute full trailer", "Custom"], index=1)
    runtime_map = {"8-second screen test": 8, "30-second cinematic sequence": 30, "60-second trailer": 60, "2-minute full trailer": 120}
    runtime_seconds = int(st.number_input("Runtime in seconds", min_value=8, max_value=5400, value=60, step=8)) if runtime_label == "Custom" else runtime_map[runtime_label]
    quality_key = st.selectbox("Quality strategy", list(QUALITY_PROFILES), index=list(QUALITY_PROFILES).index("blockbuster_target"), format_func=lambda key: QUALITY_PROFILES[key].label)
    st.caption(QUALITY_PROFILES[quality_key].description)

    st.header("Cast and identity")
    lead_name = st.text_input("Lead name", value="Lead")
    lead_description = st.text_area("Lead appearance and performance", value="A recognizable grounded leading performer with stable facial details, body proportions, age appearance, wardrobe, and restrained acting.", height=100)
    support_name = st.text_input("Supporting character", value="Supporting Character")
    support_description = st.text_area("Supporting description", value="A visually distinct supporting performer with clear motivation and a controlled naturalistic performance.", height=85)
    images = st.file_uploader("Authorized reference images (optional)", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True) or []
    likeness_authorized = st.checkbox("I am the subject shown, or I have explicit permission to use every uploaded likeness.", value=False, disabled=not images)

    st.header("Autonomous finishing")
    semantic_qa = st.checkbox("Use OpenAI semantic shot review", value=openai_ready, help="Checks visible story action, cast, wardrobe, props, framing, performance, and continuity against the approved shot contract.")
    semantic_authorized = st.checkbox("I authorize sampled generated frames to be sent to OpenAI for quality review.", value=False, disabled=not semantic_qa)
    finish_audio = st.checkbox("Generate voices and cinematic captions automatically", value=False)
    voice_provider = st.selectbox("Voice provider", ["openai", "elevenlabs"], disabled=not finish_audio)

    clips = int(math.ceil(runtime_seconds / 8))
    recommended_calls = clips * (QUALITY_PROFILES[quality_key].retries_per_shot + 1)
    max_calls = int(st.number_input("Maximum paid video predictions", min_value=1, max_value=5000, value=max(1, recommended_calls), step=1))
    cost_per_second = float(st.number_input("Current video price per generated second (optional)", min_value=0.0, max_value=1000.0, value=0.0, step=0.01, format="%.4f"))
    max_spend = float(st.number_input("Maximum approved video spend in USD (0 = call ceiling only)", min_value=0.0, max_value=1_000_000.0, value=0.0, step=1.0))
    estimated = max_calls * 8 * cost_per_second
    if cost_per_second > 0:
        st.info(f"Maximum configured video estimate: **${estimated:,.2f}**. Speech and specialist post-processing are separate.")
    st.write(f"Planned footage: **{runtime_seconds}s**, approximately **{clips} clips**.")
    make_clicked = st.button("MAKE MY MOVIE", type="primary", use_container_width=True)
    st.caption("This button remains available. Missing setup is reported after you press it instead of silently disabling production.")

    st.divider()
    st.header("Continue a saved job")
    resumable = list_resumable_runs("runs", 100)
    jobs = [item for item in resumable if (Path(str(item.get("workspace") or "")) / "autonomous_job.json").exists()]
    run_map = {f"{item.get('runId')} | {item.get('status')} | {item.get('title') or (item.get('brief') or {}).get('title') or 'Untitled'}": item for item in jobs}
    selected_run = st.selectbox("Saved autonomous checkpoint", list(run_map) or ["No autonomous checkpoints"], disabled=not run_map)
    continue_clicked = st.button("Continue selected job", use_container_width=True, disabled=not run_map or not replicate_ready)


def _progress(stage: str, percent: int, message: str) -> None:
    bar = st.session_state.get("autonomous_progress")
    status = st.session_state.get("autonomous_status")
    if bar:
        bar.progress(max(0, min(100, int(percent))), text=message)
    if status:
        status.write(f"{stage.replace('_', ' ').title()}: {message}")


if make_clicked:
    errors = []
    if not replicate_ready:
        errors.append("Add REPLICATE_API_TOKEN to Streamlit deployment secrets.")
    if len(premise.strip()) < 12:
        errors.append("Enter a premise with at least 12 characters.")
    if images and not likeness_authorized:
        errors.append("Confirm permission for every uploaded reference image.")
    if semantic_qa and not openai_ready:
        errors.append("OpenAI semantic review requires OPENAI_API_KEY, or turn it off.")
    if semantic_qa and not semantic_authorized:
        errors.append("Authorize sampled-frame semantic review, or turn it off.")
    if finish_audio and not voice_caps.get(voice_provider):
        errors.append(f"Automatic audio finishing is unavailable until the {voice_provider} API key is configured.")
    if max_spend > 0 and cost_per_second <= 0:
        errors.append("Enter current cost per generated second when using a dollar budget.")
    if max_spend > 0 and estimated > max_spend:
        errors.append("The call ceiling exceeds the approved dollar budget.")
    if errors:
        for error in errors:
            st.error(error)
    else:
        cast = [
            {"name": lead_name.strip() or "Lead", "role": "Lead protagonist", "description": lead_description, "arc": "A specific internal conflict tested through concrete action."},
            {"name": support_name.strip() or "Supporting Character", "role": "Supporting dramatic counterpoint", "description": support_description, "arc": "Applies pressure that reveals the lead's decisions."},
        ]
        format_key = "trailer" if runtime_seconds <= 120 else "short" if runtime_seconds <= 720 else "episode"
        brief: dict[str, Any] = {"title": title.strip() or None, "premise": premise.strip(), "genre": genre, "tone": tone, "format": format_key, "cast": cast}
        if exact_script.strip():
            brief["authoredScript"] = exact_script.strip()
            brief["creativeDirection"] = {"scriptSource": "authored"}
        config = {"qualityProfile": quality_key, "continuous": True, "semanticQa": semantic_qa, "semanticAuthorized": semantic_authorized, "finishAudio": finish_audio, "voiceProvider": voice_provider, "maxProviderCalls": max_calls, "maxSpendUsd": max_spend, "costPerSecondUsd": cost_per_second, "batchSize": clips}
        st.session_state["autonomous_progress"] = st.progress(0, text="Building free production plan")
        st.session_state["autonomous_status"] = st.status("Autonomous production", expanded=True)
        try:
            with st.spinner("Planning, generating, verifying, repairing, and finishing..."):
                plan = prepare_autonomous_plan(brief, target_runtime_seconds=runtime_seconds, clip_duration_seconds=8, max_shots=clips, config=config, output_root="runs")
                st.session_state["autonomous_plan"] = plan
                st.session_state["autonomous_result"] = start_autonomous_production(plan, images=images, output_root="runs", progress=_progress)
            st.session_state["autonomous_status"].update(label="Autonomous production checkpoint complete", state="complete")
        except Exception as exc:
            st.session_state["autonomous_status"].update(label="Production stopped with accepted work preserved", state="error")
            st.error(str(exc))

if continue_clicked and run_map:
    try:
        with st.spinner("Continuing the durable production and rerunning finishing..."):
            st.session_state["autonomous_result"] = continue_autonomous_production(str(run_map[selected_run].get("runId") or ""), output_root="runs", continuous=True)
    except Exception as exc:
        st.error(str(exc))

plan = st.session_state.get("autonomous_plan")
if plan:
    st.header("Approved production contract")
    render, cost = plan.get("renderPlan") or {}, plan.get("costForecast") or {}
    columns = st.columns(6)
    columns[0].metric("Planned clips", render.get("plannedShots", 0))
    columns[1].metric("Runtime", f"{render.get('plannedRuntimeSeconds', 0)}s")
    columns[2].metric("Screenplay score", (plan.get("screenplayAudit") or {}).get("score", 0))
    columns[3].metric("Prompt floor", (plan.get("promptGate") or {}).get("score", 0))
    columns[4].metric("Authorized calls", cost.get("authorizedCalls", 0))
    columns[5].metric("Ledger", str((plan.get("promptLedger") or {}).get("ledgerHash") or "")[:10])
    with st.expander("Binding screenplay"):
        st.text_area("Screenplay", str(plan.get("screenplay") or ""), height=480, disabled=True)
    with st.expander("Model-independent shot routing"):
        st.dataframe([{"Clip": item.get("order"), "Task": item.get("task"), "Recommended": item.get("recommendedModel"), "Execution": item.get("executionModel"), "Fallback": item.get("fallbackModel"), "Reason": item.get("reason")} for item in (plan.get("modelRoutes") or {}).get("routes") or []], use_container_width=True, hide_index=True)

output = st.session_state.get("autonomous_result")
if output:
    result, quality = output.get("result") or {}, output.get("quality") or {}
    media, metrics = result.get("media") or {}, (result.get("media") or {}).get("metrics") or {}
    status = str(result.get("status") or (output.get("job") or {}).get("status") or "unknown")
    st.header("Autonomous result")
    st.success("Planned footage and finishing completed.") if status == "complete" else st.warning("A durable checkpoint was preserved. Continue this job instead of starting over.")
    columns = st.columns(6)
    columns[0].metric("Status", status.title())
    columns[1].metric("Clips", f"{metrics.get('verifiedShots', 0)}/{metrics.get('plannedShots', 0)}")
    columns[2].metric("Runtime", f"{float(metrics.get('verifiedSeconds', 0) or 0):.1f}s")
    columns[3].metric("Quality", f"{float(quality.get('scorePercent', 0) or 0):.1f}")
    columns[4].metric("Visual", f"{float(quality.get('visualAverage', 0) or 0) * 100:.1f}")
    columns[5].metric("Semantic", "Used" if quality.get("semanticAvailable") else "Local only")
    playable = (output.get("audio") or {}).get("captioned_video_path") or (output.get("audio") or {}).get("final_video_path") or media.get("final_video_path") or media.get("partial_video_path") or media.get("hero_path")
    if playable and Path(str(playable)).exists():
        st.video(str(playable))
        st.download_button("Download current master", Path(str(playable)).read_bytes(), file_name=Path(str(playable)).name, mime="video/mp4", use_container_width=True)
    if output.get("errors"):
        with st.expander("Finishing notes"):
            for item in output["errors"]:
                st.warning(item)
    evidence_tab, memory_tab, edit_tab = st.tabs(["Evidence and quality", "Production memory", "Edit decision list"])
    with evidence_tab:
        st.json(quality)
        semantic = (output.get("semantic") or {}).get("report")
        if semantic:
            st.dataframe([{"Clip": (item.get("contract") or {}).get("order"), "Score": item.get("scorePercent"), "Rating": item.get("rating"), "Semantic": item.get("semanticAvailable"), "Summary": item.get("summary")} for item in semantic.get("reports") or []], use_container_width=True, hide_index=True)
    with memory_tab:
        memory = output.get("memory") or {}
        st.write(memory.get("compactSummary") or "No project summary yet.")
        st.json({"projectId": memory.get("projectId"), "worldGraph": memory.get("worldGraph"), "lockedFacts": memory.get("lockedFacts"), "modelMemory": memory.get("modelMemory"), "scarMemory": memory.get("scarMemory")})
    with edit_tab:
        st.json(output.get("edl") or {})
else:
    st.info("Enter the concept and press MAKE MY MOVIE. Free planning occurs first; paid work is bounded by the explicit call and spend ceilings.")
