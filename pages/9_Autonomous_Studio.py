"""One-click Autonomous Studio for Silver-Screen 9."""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.ai_video import image_to_data_url
from silver_screen.cast_library import cast_library_root, saved_cast_photos
from silver_screen.comfyui import comfyui_ready, video_backend, video_backend_ready

from silver_screen.autonomous_studio import (
    QUALITY_PROFILES, continue_autonomous_production,
    prepare_autonomous_plan, start_autonomous_production,
)
from silver_screen.runtime import list_resumable_runs
from silver_screen.science import GENRES, SCIENCE, TONES
from silver_screen.screenplay_writer import SCREENPLAY_LENGTHS
from silver_screen.voice_providers import provider_capabilities

st.set_page_config(page_title="Silver-Screen | Autonomous Studio", page_icon="🎬", layout="wide")
st.title("Autonomous Studio")

video_ready = video_backend_ready()
openai_ready = bool(os.getenv("OPENAI_API_KEY", "").strip())
voice_caps = provider_capabilities()
draft = st.session_state.get("screenplay-production")

if draft:
    is_ad = draft["kind"] == "ad"
    started = st.session_state.get("screenplay-production-started") == draft["seed"]
    st.header(draft["title"])
    st.caption(f"{draft['seconds']} seconds planned · five clips · {video_backend()} video")
    st.text(draft["premise"])
    st.text_area("Screenplay", draft["authoredScript"], height=310, disabled=True)
    root = cast_library_root()
    photos = saved_cast_photos(root) if root else {}
    lead_photo = photos.get("Your photo") if st.session_state.get("screenplay-production-photo") else None
    render_draft = False
    if not started:
        with st.form("render-written-draft"):
            upload = st.file_uploader(
                "Product photo (optional)" if is_ad else "Lead photo (optional)",
                type=["png", "jpg", "jpeg", "webp"],
                help="The video model uses this image as the starting frame.",
            )
            if lead_photo:
                st.caption("Your saved lead photo is selected. You can replace it by uploading another photo.")
            authorized = st.checkbox(
                "I have permission to use this product photo."
                if is_ad else "Everyone shown in the photo agreed to this scene and its audience."
            )
            call_budget = st.number_input("Maximum video requests", min_value=5, max_value=15, value=5)
            render_draft = st.form_submit_button("Render first shot", type="primary", disabled=not video_ready)
    if not video_ready:
        st.error(f"Configure the {video_backend()} video backend to render this draft.")
    if st.button("Return to the writer"):
        st.session_state.pop("screenplay-production", None)
        st.session_state.pop("screenplay-production-photo", None)
        st.switch_page("pages/0_Write_a_Movie.py")
    if render_draft:
        reference = upload if upload is not None else lead_photo
        if reference is not None and not authorized:
            st.error("Confirm permission to use the selected reference before rendering.")
        elif reference is not None and video_backend() == "comfyui" and not comfyui_ready("reference"):
            st.error("The current video backend needs an image-to-video workflow to use this reference.")
        elif reference is not None and not image_to_data_url(reference):
            st.error("This photo could not be opened. Choose a readable JPEG, PNG, or WebP photo.")
        else:
            def progress(stage: str, percent: int, message: str) -> None:
                bar.progress(max(0, min(100, percent)), text=message)

            bar = st.progress(0, text="Planning five shots")
            try:
                plan = prepare_autonomous_plan(
                    draft, target_runtime_seconds=draft["seconds"],
                    clip_duration_seconds=SCREENPLAY_LENGTHS[draft["seconds"]], max_shots=5,
                    config={"qualityProfile": "cinematic", "continuous": False, "batchSize": 1,
                            "maxProviderCalls": int(call_budget), "semanticQa": False,
                            "transitionMode": "off"},
                )
                st.session_state["autonomous_plan"] = plan
                st.session_state["autonomous_result"] = start_autonomous_production(
                    plan, images=[reference] if reference is not None else [], progress=progress,
                )
                if st.session_state["autonomous_result"].get("runId"):
                    st.session_state["screenplay-production-started"] = draft["seed"]
                    st.rerun()
            except Exception as exc:
                st.error(str(exc))
else:
    st.caption(SCIENCE["credit"])
    st.write("Plan a film, render the approved shots, and continue from a saved checkpoint.")
    st.page_link("pages/0_Write_a_Movie.py", label="Write a movie or advertisement from one idea")

with st.sidebar:
    make_clicked = False
    if not draft:
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

    st.divider()
    st.header("Continue a saved job")
    resumable = list_resumable_runs("runs", 100)
    jobs = [item for item in resumable if (Path(str(item.get("workspace") or "")) / "autonomous_job.json").exists()]
    run_map = {f"{item.get('runId')} | {item.get('status')} | {item.get('title') or (item.get('brief') or {}).get('title') or 'Untitled'}": item for item in jobs}
    selected_run = st.selectbox("Saved autonomous checkpoint", list(run_map) or ["No autonomous checkpoints"], disabled=not run_map)


def _progress(stage: str, percent: int, message: str) -> None:
    bar = st.session_state.get("autonomous_progress")
    status = st.session_state.get("autonomous_status")
    if bar:
        bar.progress(max(0, min(100, int(percent))), text=message)
    if status:
        status.write(f"{stage.replace('_', ' ').title()}: {message}")


if make_clicked:
    errors = []
    if not video_ready:
        errors.append(f"Configure the {video_backend()} video backend.")
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

if run_map:
    continue_clicked = st.button("Continue selected job", type="primary", use_container_width=True)
else:
    continue_clicked = False

if continue_clicked:
    try:
        with st.spinner("Continuing the durable production and rerunning finishing..."):
            st.session_state["autonomous_result"] = continue_autonomous_production(
                str(run_map[selected_run].get("runId") or ""), output_root="runs",
            )
        st.rerun()
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
        st.text_area("Screenplay", str(plan.get("screenplay") or ""), height=480, disabled=True, key="approved-screenplay")
    with st.expander("Shot prompts"):
        for shot in plan.get("prompts") or []:
            st.text(f"Clip {shot['order']}")
            st.code(shot["fallbackPrompt"], language=None)
    with st.expander("Model-independent shot routing"):
        st.dataframe([{"Clip": item.get("order"), "Task": item.get("task"), "Recommended": item.get("recommendedModel"), "Execution": item.get("executionModel"), "Fallback": item.get("fallbackModel"), "Reason": item.get("reason")} for item in (plan.get("modelRoutes") or {}).get("routes") or []], use_container_width=True, hide_index=True)

output = st.session_state.get("autonomous_result")
if output:
    result, quality = output.get("result") or {}, output.get("quality") or {}
    media, metrics = result.get("media") or {}, (result.get("media") or {}).get("metrics") or {}
    status = str(result.get("status") or (output.get("job") or {}).get("status") or "unknown")
    st.header("Autonomous result")
    short_video = ((output.get("job") or {}).get("artifacts") or {}).get("shortVideo")
    writer_kind = (result.get("brief") or {}).get("screenplayWriter")
    if status == "complete":
        if writer_kind and not short_video:
            st.warning("All five shots rendered. See Finishing notes for the export status.")
        else:
            st.success("Your MP4 is ready.")
    else:
        if media.get("stopReason") == "prediction_pending":
            st.info("A render is still pending. Continue the saved job to check it.")
        elif media.get("stopReason") in {"provider_call_budget_exhausted", "shot_retry_budget_exhausted"}:
            st.warning("The render reached its request limit. You can download any completed shots below.")
        else:
            st.warning("Checkpoint saved. Continue the existing job to render the next shot.")
        queue = media.get("queue") or {}
        for shot in queue.get("shots") or []:
            if shot.get("lastError") and shot.get("status") != "verified":
                st.text(f"Clip {shot.get('order')}: {shot['lastError']}")
    columns = st.columns(6)
    columns[0].metric("Status", status.title())
    columns[1].metric("Clips", f"{metrics.get('verifiedShots', 0)}/{metrics.get('plannedShots', 0)}")
    columns[2].metric("Runtime", f"{float(metrics.get('verifiedSeconds', 0) or 0):.1f}s")
    columns[3].metric("Quality", f"{float(quality.get('scorePercent', 0) or 0):.1f}")
    columns[4].metric("Visual", f"{float(quality.get('visualAverage', 0) or 0) * 100:.1f}")
    columns[5].metric("Semantic", "Used" if quality.get("semanticAvailable") else "Local only")
    playable = short_video or (output.get("audio") or {}).get("captioned_video_path") or (output.get("audio") or {}).get("final_video_path") or media.get("final_video_path") or media.get("partial_video_path") or media.get("hero_path")
    if playable and Path(str(playable)).exists():
        st.video(str(playable))
        download_label = "Download advertisement" if writer_kind == "ad" and short_video else "Download MP4" if status == "complete" else "Download preview"
        st.download_button(download_label, Path(str(playable)).read_bytes(), file_name=Path(str(playable)).name, mime="video/mp4", use_container_width=True)
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
elif not draft:
    st.info("Enter an idea in the sidebar, then choose Make my movie.")
