"""Shot-level screenplay coverage, prompt ledger, audio strategy, and paid gates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.creative_direction import (
    PROFILE_LABELS,
    PROFILE_PRESETS,
    normalize_creative_direction,
)
from silver_screen.pipeline import BriefValidationError, PipelineError, run_pipeline
from silver_screen.preproduction import build_preproduction_preview, request_fingerprint
from silver_screen.production_dashboard import dashboard_metrics, production_view, queue_rows
from silver_screen.render_planning import (
    build_render_plan,
    recommended_provider_call_budget,
)
from silver_screen.science import FORMATS, GENRES, SCIENCE, TONES
from silver_screen.shot_director import (
    AUDIO_STRATEGIES,
    normalize_shot_direction,
    parse_shot_override_text,
    prompt_ledger_rows,
)
from silver_screen.star_profile import (
    MAX_REFERENCE_IMAGES,
    build_star_cast,
    normalize_star_profile,
    persist_star_profile,
    reorder_primary_reference,
    validate_star_profile,
)

st.set_page_config(
    page_title="Silver-Screen | Shot Director",
    page_icon="🎥",
    layout="wide",
    initial_sidebar_state="expanded",
)

MODE_LABELS = {
    "self": "Star as myself",
    "authorized_person": "Authorized real person",
    "fictional_character": "Original fictional character",
    "authorized_character": "Authorized character or brand",
}


def _runtime(seconds: object) -> str:
    value = max(0, int(float(seconds or 0)))
    minutes, remainder = divmod(value, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {remainder:02d}s"
    if minutes:
        return f"{minutes}m {remainder:02d}s"
    return f"{remainder}s"


def _profile_error(profile: dict[str, Any], images: list[Any]) -> str | None:
    try:
        validate_star_profile(profile, len(images))
        return None
    except (TypeError, ValueError) as exc:
        return str(exc)


def _read(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _download_video(label: str, path: str | None, key: str) -> None:
    data = _read(path)
    if data is not None:
        st.download_button(
            label,
            data,
            file_name=Path(str(path)).name,
            mime="video/mp4",
            key=key,
            use_container_width=True,
        )


def _brief(
    *,
    title: str,
    premise: str,
    genre: str,
    tone: str,
    format_key: str,
    seed: str,
    cast: list[dict[str, str]],
    creative_direction: dict[str, Any],
    shot_direction: dict[str, Any],
    authored_script: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "premise": premise,
        "genre": genre,
        "tone": tone,
        "format": format_key,
        "cast": cast,
        "creativeDirection": creative_direction,
        "shotDirection": shot_direction,
    }
    if seed.strip():
        payload["seed"] = seed.strip()
    if creative_direction.get("scriptSource") == "authored":
        payload["authoredScript"] = authored_script
    return payload


st.title("🎥 Shot Director")
st.caption(SCIENCE["credit"])
st.write(
    "Plan every provider clip as a distinct screenplay shot, preview the exact positive "
    "and negative prompts, choose how production audio is handled, and lock the approved "
    "prompt ledger before any paid video request. This prevents a two-minute film from "
    "becoming a sequence of near-identical scene summaries."
)
st.info(
    "The screenplay, complete shot deck, continuity prompt variants, negative prompts, "
    "coverage audit, and ledger hash are built locally. This preview makes zero Replicate "
    "or speech-provider calls."
)

replicate_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
clip_duration = int(os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8") or 8)
if clip_duration not in {4, 6, 8}:
    clip_duration = 8

with st.sidebar:
    st.header("Story")
    title = st.text_input("Title", value="")
    premise = st.text_area(
        "Premise",
        (
            "A capable but underestimated protagonist discovers that a routine assignment "
            "was designed to expose them, and must identify who controls the operation "
            "before the next handoff."
        ),
        height=145,
        max_chars=4000,
    )
    genre = st.selectbox(
        "Genre",
        list(GENRES),
        index=list(GENRES).index("thriller"),
    )
    tone = st.selectbox(
        "Tone",
        list(TONES),
        index=list(TONES).index("cinematic"),
    )
    format_key = st.selectbox(
        "Story format",
        list(FORMATS),
        index=list(FORMATS).index("trailer"),
        format_func=lambda key: (
            f"{FORMATS[key]['label']} | {FORMATS[key]['minutes']} min blueprint"
        ),
    )
    seed = st.text_input("Seed", placeholder="Derived automatically")

    st.divider()
    st.header("Lead and references")
    lead_type = st.selectbox(
        "Lead type",
        list(MODE_LABELS),
        format_func=lambda key: MODE_LABELS[key],
        index=list(MODE_LABELS).index("self"),
    )
    lead_name = st.text_input("Lead name", placeholder="Your name or character name")
    lead_role = st.text_input("Lead role", value="Lead protagonist")
    lead_appearance = st.text_area(
        "Lead appearance",
        (
            "Describe the normal face, hair, build, age appearance, and distinguishing "
            "features that must remain recognizable."
        ),
        height=95,
    )
    wardrobe = st.text_area(
        "Wardrobe",
        (
            "Understated, tailored, story-appropriate wardrobe with consistent colors "
            "and accessories."
        ),
        height=80,
    )
    invariants = st.text_area(
        "Identity details that must never drift",
        (
            "Keep the same face, hairstyle, age appearance, body proportions, wardrobe, "
            "and distinguishing features."
        ),
        height=90,
    )
    lead_performance = st.text_area(
        "Lead performance direction",
        (
            "Restrained, observant, physically confident, and emotionally controlled. "
            "No posing or exaggerated reactions."
        ),
        height=85,
    )
    images = (
        st.file_uploader(
            "Authorized lead reference images",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            help=(
                "Upload up to six recent references. The primary image anchors the first "
                "clip; verified final frames carry continuity into later clips."
            ),
        )
        or []
    )
    if len(images) > MAX_REFERENCE_IMAGES:
        st.error(f"Use no more than {MAX_REFERENCE_IMAGES} reference images.")
    primary_index = 0
    if images:
        primary_index = st.selectbox(
            "Primary identity anchor",
            list(range(len(images))),
            format_func=lambda index: str(
                getattr(images[index], "name", f"Reference {index + 1}")
            ),
        )
    authorization_required = bool(images) or lead_type in {
        "self",
        "authorized_person",
        "authorized_character",
    }
    authorization = st.checkbox(
        (
            "I am the subject shown, or I have explicit permission to use this person, "
            "character, and likeness."
        ),
        value=False,
        disabled=not authorization_required,
    )

    with st.expander("Supporting character", expanded=False):
        support_name = st.text_input(
            "Supporting name", value="Supporting Character"
        )
        support_role = st.text_input(
            "Supporting role", value="Ally, rival, or operational counterpoint"
        )
        support_description = st.text_area(
            "Supporting appearance",
            (
                "A visually distinct supporting character with a specific objective "
                "and restrained performance."
            ),
            height=85,
        )

    star_profile = normalize_star_profile(
        {
            "mode": lead_type,
            "subjectName": lead_name,
            "role": lead_role,
            "appearance": lead_appearance,
            "wardrobe": wardrobe,
            "identityInvariants": invariants,
            "performanceStyle": lead_performance,
            "authorizationConfirmed": (
                authorization if authorization_required else True
            ),
            "primaryReferenceIndex": primary_index,
        }
    )
    profile_error = _profile_error(star_profile, images)
    if profile_error:
        st.warning(profile_error)

    st.divider()
    st.header("Creative direction")
    profile_key = st.selectbox(
        "Direction profile",
        list(PROFILE_LABELS),
        index=list(PROFILE_LABELS).index("grounded_prestige"),
        format_func=lambda key: PROFILE_LABELS[key],
    )
    preset = PROFILE_PRESETS[profile_key]
    director_notes = st.text_area(
        "Director notes",
        placeholder=(
            "Example: Treat the lead as competent without making the actor pose. "
            "Use silence, reflections, practical objects, and small reactions."
        ),
        height=105,
    )
    avoid_text = st.text_area(
        "Additional avoid list",
        value="\n".join(str(item) for item in preset.get("avoid", [])),
        height=145,
        help="One unwanted trope, visual habit, performance habit, or phrase per line.",
        key=f"shot-director-avoid-{profile_key}",
    )
    with st.expander("Advanced creative controls", expanded=False):
        medium = st.text_input(
            "Visual medium",
            value=str(preset["medium"]),
            key=f"shot-medium-{profile_key}",
        )
        realism = st.text_input(
            "Realism",
            value=str(preset["realism"]),
            key=f"shot-realism-{profile_key}",
        )
        dialogue_style = st.text_input(
            "Dialogue style",
            value=str(preset["dialogueStyle"]),
            key=f"shot-dialogue-{profile_key}",
        )
        performance_style = st.text_input(
            "Performance style",
            value=str(preset["performanceStyle"]),
            key=f"shot-performance-{profile_key}",
        )
        camera_style = st.text_input(
            "Camera and lens language",
            value=str(preset["cameraStyle"]),
            key=f"shot-camera-{profile_key}",
        )
        pacing = st.text_input(
            "Pacing",
            value=str(preset["pacing"]),
            key=f"shot-pacing-{profile_key}",
        )
        color_language = st.text_area(
            "Color and lighting language",
            value=str(preset["colorLanguage"]),
            height=75,
            key=f"shot-color-{profile_key}",
        )
        humor = st.slider(
            "Humor",
            0,
            100,
            int(preset["humorLevel"]),
            key=f"shot-humor-{profile_key}",
        )
        melodrama = st.slider(
            "Melodrama",
            0,
            100,
            int(preset["melodramaLevel"]),
            key=f"shot-melodrama-{profile_key}",
        )
        exposition = st.slider(
            "Exposition",
            0,
            100,
            int(preset["expositionLevel"]),
            key=f"shot-exposition-{profile_key}",
        )
        global_visual = st.text_area(
            "Global visual direction",
            value=str(preset["globalVisualDirection"]),
            height=135,
            key=f"shot-global-{profile_key}",
        )

    st.divider()
    st.header("Script")
    script_source_label = st.radio(
        "Screenplay source",
        ["Generate a grounded screenplay", "Use my exact script"],
        index=0,
    )
    script_source = (
        "authored"
        if script_source_label == "Use my exact script"
        else "generated"
    )
    authored_script = ""
    if script_source == "authored":
        authored_script = st.text_area(
            "Exact authored script",
            placeholder=(
                "EXT. HOTEL TERRACE - NIGHT\n"
                "CODY watches the reflection in the glass, not the street.\n\n"
                "CODY: Were you followed?\n"
                "MARA: I was expected."
            ),
            height=280,
        )
    strict_gate = st.checkbox(
        "Strict screenplay and provider-prompt quality gates",
        value=True,
    )

    creative_direction = normalize_creative_direction(
        {
            "profile": profile_key,
            "scriptSource": script_source,
            "medium": medium,
            "realism": realism,
            "dialogueStyle": dialogue_style,
            "performanceStyle": performance_style,
            "cameraStyle": camera_style,
            "pacing": pacing,
            "colorLanguage": color_language,
            "humorLevel": humor,
            "melodramaLevel": melodrama,
            "expositionLevel": exposition,
            "globalVisualDirection": global_visual,
            "directorNotes": director_notes,
            "avoid": avoid_text,
            "strictGate": strict_gate,
            "minimumScriptScore": preset["minimumScriptScore"],
            "minimumPromptScore": preset["minimumPromptScore"],
            "enforceApprovalGates": False,
        }
    )

    st.divider()
    st.header("Shot Director")
    audio_strategy = st.selectbox(
        "Production audio strategy",
        list(AUDIO_STRATEGIES),
        index=list(AUDIO_STRATEGIES).index("dub_later"),
        format_func=lambda key: AUDIO_STRATEGIES[key]["label"],
        help=(
            "Professional dub later is recommended when you plan to add exact voices "
            "and timing in Professional Script Sync."
        ),
    )
    st.caption(str(AUDIO_STRATEGIES[audio_strategy]["instruction"]))
    shot_override_text = st.text_area(
        "Clip-specific prompt overrides",
        placeholder=(
            "Clip 1: Static 50mm profile through the hotel glass. No dialogue.\n"
            "Clip 2: Slow lateral move through the service corridor.\n"
            "shot_0003: Locked detail of the key card changing hands."
        ),
        height=145,
        help=(
            "Use the clip number or durable shot ID. Later lines without a new header "
            "continue the previous override."
        ),
    )
    maximum_similarity = st.slider(
        "Maximum allowed similarity between consecutive clip plans",
        min_value=0.55,
        max_value=0.99,
        value=0.92,
        step=0.01,
        help=(
            "Lower values demand more varied coverage. Extremely low values may reject "
            "legitimate continuation shots."
        ),
    )
    minimum_distinct_ratio = st.slider(
        "Minimum distinct-shot ratio",
        min_value=0.35,
        max_value=1.0,
        value=0.82,
        step=0.01,
    )
    coverage_gate = st.checkbox(
        "Block paid generation when the shot deck is repetitive or dialogue does not fit",
        value=True,
    )
    shot_direction = normalize_shot_direction(
        {
            "audioStrategy": audio_strategy,
            "shotPromptOverrides": parse_shot_override_text(
                shot_override_text
            ),
            "coverageGate": coverage_gate,
            "maximumPromptSimilarity": maximum_similarity,
            "minimumDistinctShotRatio": minimum_distinct_ratio,
            "enforcePromptLedger": False,
        }
    )

    cast = build_star_cast(
        star_profile,
        support_name=support_name,
        support_role=support_role,
        support_description=support_description,
    )
    base_brief = _brief(
        title=title,
        premise=premise,
        genre=genre,
        tone=tone,
        format_key=format_key,
        seed=seed,
        cast=cast,
        creative_direction=creative_direction,
        shot_direction=shot_direction,
        authored_script=authored_script,
    )

    st.divider()
    st.header("Render plan")
    render_label = st.radio(
        "Render length",
        ["One-clip screen test", "Match the full blueprint", "Custom runtime"],
        index=0,
    )
    render_mode = {
        "One-clip screen test": "preview",
        "Match the full blueprint": "match_blueprint",
        "Custom runtime": "custom",
    }[render_label]
    custom_runtime = None
    if render_mode == "custom":
        custom_runtime = st.number_input(
            "Custom runtime in seconds",
            min_value=4,
            max_value=5400,
            value=60,
            step=clip_duration,
        )
    plan = build_render_plan(
        format_key,
        mode=render_mode,
        custom_runtime_seconds=custom_runtime,
        clip_duration_seconds=clip_duration,
    )
    st.info(
        f"**Blueprint:** {_runtime(plan.blueprint_minutes * 60)}  \n"
        f"**Planned render:** {_runtime(plan.runtime_seconds)}  \n"
        f"**Provider clips:** {plan.planned_clips} × about "
        f"{plan.clip_duration_seconds}s"
    )
    batch_size = int(
        st.number_input(
            "New clips per checkpoint",
            min_value=1,
            max_value=max(1, min(16, plan.planned_clips)),
            value=1,
            step=1,
        )
    )
    retries = int(st.slider("TGRM retries per clip", 0, 6, 1))
    provider_budget = recommended_provider_call_budget(
        plan,
        retries_per_clip=retries,
        include_retry_capacity=True,
    )
    use_continuity = st.checkbox(
        "Chain verified final frames",
        value=True,
    )
    st.caption(
        f"Whole-production ceiling: **{provider_budget} calls**. This checkpoint can "
        f"create no more than **{batch_size} new clips**."
    )
    preview_clicked = st.button(
        "Build free screenplay, shot deck, and prompt ledger",
        type="primary",
        use_container_width=True,
        disabled=(
            bool(profile_error)
            or len(premise.strip()) < 12
            or (script_source == "authored" and not authored_script.strip())
        ),
    )

preview_key = "shot-director-preview"
result_key = "shot-director-result"

if preview_clicked:
    try:
        with st.spinner(
            "Building the screenplay, distinct shot deck, continuity variants, "
            "negative prompts, and quality gates..."
        ):
            preview = build_preproduction_preview(
                base_brief,
                target_runtime_seconds=plan.runtime_seconds,
                clip_duration_seconds=plan.clip_duration_seconds,
                max_shots=plan.planned_clips,
                max_prompt_previews=min(30, plan.planned_clips),
            )
        st.session_state[preview_key] = preview
        st.session_state.pop(result_key, None)
        for key in (
            "shot-approve-script",
            "shot-approve-prompts",
            "shot-approve-budget",
        ):
            st.session_state.pop(key, None)
    except (BriefValidationError, ValueError, TypeError) as exc:
        st.error(str(exc))

preview = st.session_state.get(preview_key)
current_fingerprint = None
try:
    current_fingerprint = request_fingerprint(
        base_brief,
        target_runtime_seconds=plan.runtime_seconds,
        clip_duration_seconds=plan.clip_duration_seconds,
        max_shots=plan.planned_clips,
    )
except Exception:
    pass

if preview:
    preview_current = preview.get("fingerprint") == current_fingerprint
    if not preview_current:
        st.warning(
            "The story, cast, exact script, creative direction, shot overrides, audio "
            "strategy, or render plan changed. Build a new free preview before approval."
        )

    screenplay_audit = preview.get("screenplayAudit") or {}
    prompt_gate = preview.get("promptGate") or {}
    coverage_audit = preview.get("promptSetAudit") or {}
    coverage_metrics = coverage_audit.get("metrics") or {}
    ledger = preview.get("promptLedger") or {}

    columns = st.columns(8)
    columns[0].metric(
        "Script score", f"{float(screenplay_audit.get('score', 0)):.1f}"
    )
    columns[1].metric(
        "Lowest prompt", f"{float(prompt_gate.get('score', 0)):.1f}"
    )
    columns[2].metric(
        "Prompt average", f"{float(prompt_gate.get('averageScore', 0)):.1f}"
    )
    columns[3].metric(
        "Distinct shots",
        f"{float(coverage_metrics.get('distinctShotRatio', 0)) * 100:.0f}%",
    )
    columns[4].metric(
        "Repetitive pairs",
        int(coverage_metrics.get("repetitivePairs", 0) or 0),
    )
    columns[5].metric(
        "Dialogue overflows",
        int(coverage_metrics.get("dialogueOverflows", 0) or 0),
    )
    columns[6].metric(
        "Planned clips",
        (preview.get("renderPlan") or {}).get("plannedShots", 0),
    )
    columns[7].metric("Provider calls", preview.get("providerCallsMade", 0))

    if preview.get("strictGatePassed"):
        st.success(
            "The screenplay, every planned provider prompt, and the shot-coverage "
            "deck pass the selected gates."
        )
    else:
        st.error(
            "Paid rendering is blocked. Review the screenplay, prompt, coverage, or "
            "dialogue-timing findings and build a new preview."
        )

    (
        screenplay_tab,
        deck_tab,
        prompt_tab,
        findings_tab,
        ledger_tab,
    ) = st.tabs(
        [
            "Screenplay",
            "Shot deck",
            "Provider prompts",
            "Quality findings",
            "Approved ledger",
        ]
    )
    with screenplay_tab:
        st.text_area(
            "Screenplay controlling the production",
            value=str(preview.get("screenplay") or ""),
            height=620,
            disabled=True,
        )
    with deck_tab:
        rows = prompt_ledger_rows(ledger)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.warning("No shot contracts were generated.")
    with prompt_tab:
        st.caption(
            f"Showing {preview.get('promptPreviewCount', 0)} of "
            f"{len(ledger.get('entries') or [])} approved clip prompts. "
            "The ledger still covers every planned clip."
        )
        for item in preview.get("prompts") or []:
            blueprint = item.get("blueprint") or {}
            audit = item.get("audit") or {}
            with st.expander(
                f"Clip {item.get('order')} | {str(blueprint.get('type') or '').title()} "
                f"| score {audit.get('score', 0)}/100",
                expanded=int(item.get("order", 0) or 0) == 1,
            ):
                st.write(f"**Objective:** {blueprint.get('description', '')}")
                if blueprint.get("override"):
                    st.write(
                        f"**Operator override:** {blueprint.get('override')}"
                    )
                st.write(
                    f"**Continuity variant used in preview:** "
                    f"{'Yes' if item.get('expectedContinuity') else 'No'}"
                )
                st.code(str(item.get("prompt") or ""))
                st.markdown("**Negative prompt sent to the provider**")
                st.code(str(item.get("negativePrompt") or ""))
                st.caption(
                    f"Prompt hash: `{str(item.get('promptHash') or '')}`  \n"
                    f"Negative hash: `{str(item.get('negativePromptHash') or '')}`"
                )
    with findings_tab:
        st.subheader("Screenplay audit")
        st.json(screenplay_audit)
        st.subheader("Provider-prompt gate")
        st.json(prompt_gate)
        st.subheader("Shot coverage and dialogue-fit gate")
        st.json(coverage_audit)
    with ledger_tab:
        st.write(f"**Ledger hash:** `{ledger.get('ledgerHash', '')}`")
        st.write(
            f"**Audio strategy:** `{ledger.get('audioStrategy', '')}`  \n"
            f"**Prompt contract version:** "
            f"`{ledger.get('promptContractVersion', '')}`"
        )
        ledger_bytes = json.dumps(
            ledger,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")
        st.download_button(
            "Download approved prompt ledger JSON",
            ledger_bytes,
            file_name="silver-screen-approved-prompt-ledger.json",
            mime="application/json",
            use_container_width=True,
        )
        with st.expander("Raw ledger", expanded=False):
            st.json(ledger)

    st.divider()
    st.header("Paid production approval gates")
    approve_script = st.checkbox(
        "I reviewed and approve this screenplay.",
        key="shot-approve-script",
    )
    approve_prompts = st.checkbox(
        (
            "I reviewed and approve the complete shot deck, positive prompts, "
            "negative prompts, audio strategy, and ledger hash."
        ),
        key="shot-approve-prompts",
    )
    approve_budget = st.checkbox(
        (
            f"I authorize paid generation up to the {provider_budget}-call safety "
            "ceiling."
        ),
        key="shot-approve-budget",
    )
    all_approved = approve_script and approve_prompts and approve_budget
    strict_ok = bool(preview.get("strictGatePassed")) or (
        not strict_gate and not coverage_audit.get("blocking")
    )
    start_disabled = bool(
        not replicate_ready
        or not preview_current
        or not all_approved
        or not strict_ok
        or profile_error
        or len(images) > MAX_REFERENCE_IMAGES
    )
    if not replicate_ready:
        st.error(
            "Add REPLICATE_API_TOKEN to Streamlit deployment secrets before paid rendering."
        )
    start_clicked = st.button(
        "Start ledger-locked paid production",
        type="primary",
        use_container_width=True,
        disabled=start_disabled,
    )
else:
    st.warning(
        "Build the free shot-level preview first. Paid generation remains unavailable "
        "until the screenplay, prompt ledger, and budget are approved."
    )
    start_clicked = False

if start_clicked and preview:
    ordered_images = reorder_primary_reference(images, primary_index)
    ledger = preview.get("promptLedger") or {}
    approved_creative = normalize_creative_direction(
        {
            **creative_direction,
            "enforceApprovalGates": True,
            "approvals": {
                "scriptApproved": True,
                "promptsApproved": True,
                "budgetApproved": True,
            },
        }
    )
    approved_shot_direction = normalize_shot_direction(
        {
            **shot_direction,
            "enforcePromptLedger": True,
            "approvedPromptLedger": ledger,
            "approvedLedgerHash": ledger.get("ledgerHash"),
        }
    )
    approved_brief = _brief(
        title=title,
        premise=premise,
        genre=genre,
        tone=tone,
        format_key=format_key,
        seed=seed,
        cast=cast,
        creative_direction=approved_creative,
        shot_direction=approved_shot_direction,
        authored_script=authored_script,
    )
    bar = st.progress(0, text="Starting ledger-locked production")
    status = st.empty()

    def progress(stage: str, percent: int, message: str) -> None:
        bar.progress(max(0, min(100, percent)), text=message)
        status.caption(f"{stage.replace('_', ' ').title()} | {percent}%")

    try:
        result = run_pipeline(
            approved_brief,
            images=ordered_images,
            output_root="runs",
            persist=True,
            render_media=True,
            video_mode="ai-video",
            max_chapters=12,
            target_runtime_seconds=plan.runtime_seconds,
            video_max_shots=plan.planned_clips,
            video_batch_size=batch_size,
            video_max_retries=retries,
            video_max_provider_calls=provider_budget,
            video_continuous=False,
            video_use_continuity=use_continuity,
            progress=progress,
        )
        run_id = str((result.get("run") or {}).get("id") or "")
        if run_id:
            result["starIdentity"] = persist_star_profile(
                run_id,
                star_profile,
                ordered_images,
                output_root="runs",
            )
        st.session_state[result_key] = result
        view = production_view(result.get("media") or {})
        bar.progress(view.progress_percent, text=view.headline)
    except (
        BriefValidationError,
        PipelineError,
        ValueError,
        TypeError,
    ) as exc:
        st.error(str(exc))

result = st.session_state.get(result_key)
if result:
    media = result.get("media") or {}
    state = result.get("state") or {}
    summary = dashboard_metrics(media)
    view = production_view(media)
    st.divider()
    st.header("Ledger-locked production result")
    if view.severity == "success":
        st.success(view.detail)
    elif view.severity == "warning":
        st.warning(view.detail)
    else:
        st.info(view.detail)

    columns = st.columns(6)
    columns[0].metric(
        "Clips", f"{summary['verified']}/{summary['planned']}"
    )
    columns[1].metric(
        "Runtime", _runtime(summary["verifiedSeconds"])
    )
    columns[2].metric(
        "Remaining", _runtime(summary["remainingSeconds"])
    )
    columns[3].metric(
        "Continuity", f"{summary['continuityPercent']:.0f}%"
    )
    columns[4].metric("Provider calls", summary["providerCalls"])
    shot_state = state.get("shotDirection") or {}
    columns[5].metric(
        "Prompt ledger",
        "Locked" if shot_state.get("enforcePromptLedger") else "Open",
    )

    video_tab, contract_tab, queue_tab = st.tabs(
        ["Video", "Shot contract", "Queue"]
    )
    with video_tab:
        playable = (
            media.get("final_video_path")
            or media.get("partial_video_path")
            or media.get("hero_path")
        )
        if playable:
            st.video(playable)
            _download_video(
                "Download ledger-locked cinematic MP4",
                playable,
                "shot-director-video",
            )
    with contract_tab:
        st.json(state.get("creativeDirection") or {})
        st.json(state.get("shotDirection") or {})
    with queue_tab:
        st.dataframe(
            queue_rows(media),
            use_container_width=True,
            hide_index=True,
        )
