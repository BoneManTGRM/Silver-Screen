"""Reusable Streamlit page for generic films and authorized star vehicles."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import streamlit as st

from .pipeline import BriefValidationError, PipelineError, resume_video_run, run_pipeline
from .production_dashboard import dashboard_metrics, display_msil, production_view, queue_rows
from .render_planning import (
    build_render_plan,
    recommended_provider_call_budget,
    requires_continuous_confirmation,
)
from .runtime import list_resumable_runs
from .science import FORMATS, GENRES, SCIENCE, TONES
from .star_profile import (
    MAX_REFERENCE_IMAGES,
    build_star_cast,
    normalize_star_profile,
    persist_star_profile,
    reorder_primary_reference,
    starter_labels,
    starter_payload,
    validate_star_profile,
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


def _run_label(record: dict[str, Any]) -> str:
    brief = record.get("brief") or {}
    title = brief.get("title") or record.get("title") or "Untitled"
    return f"{record.get('runId')} | {record.get('status')} | {title}"


def _progress(bar, message):
    def callback(stage: str, percent: int, text: str) -> None:
        bar.progress(max(0, min(100, percent)), text=text)
        message.caption(f"{stage.replace('_', ' ').title()} | {percent}%")

    return callback


def _read(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _download(label: str, path: str | None, key: str) -> None:
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


def _profile_error(profile: dict[str, Any], images: list[Any]) -> str | None:
    try:
        validate_star_profile(profile, len(images))
        return None
    except (TypeError, ValueError) as exc:
        return str(exc)


def _show_result(result: dict[str, Any], *, prefix: str) -> None:
    state = result.get("state") or {}
    media = result.get("media") or {}
    view = production_view(media)
    summary = dashboard_metrics(media)
    title = state.get("title") or "Untitled"
    run_id = (result.get("run") or {}).get("id", "?")

    message = f"{view.headline}: **{title}** | `{run_id}`. {view.detail}"
    if view.severity == "success":
        st.success(message)
    elif view.severity == "warning":
        st.warning(message)
    else:
        st.info(message)

    st.progress(
        view.progress_percent,
        text=f"Film progress: {summary['verified']} of {summary['planned']} clips",
    )
    columns = st.columns(7)
    columns[0].metric("Video clips", f"{summary['verified']}/{summary['planned']}")
    columns[1].metric("Verified runtime", _runtime(summary["verifiedSeconds"]))
    columns[2].metric("Remaining", _runtime(summary["remainingSeconds"]))
    columns[3].metric("Continuity", f"{summary['continuityPercent']:.0f}%")
    columns[4].metric("Provider calls", summary["providerCalls"])
    columns[5].metric("Video state", display_msil(media))
    columns[6].metric(
        "Identity pack",
        "Saved" if (result.get("starIdentity") or {}).get("profilePath") else "Pending",
    )

    video_tab, queue_tab, identity_tab, story_tab = st.tabs(
        ["Video", "Production queue", "Star identity", "Story"]
    )
    with video_tab:
        playable = (
            media.get("final_video_path")
            or media.get("partial_video_path")
            or media.get("hero_path")
        )
        if playable:
            st.video(playable)
            _download("Download assembled MP4", playable, f"{prefix}-assembled")
        for index, path in enumerate(media.get("video_paths") or [], start=1):
            with st.expander(f"Verified source clip {index}"):
                st.video(path)
                _download(
                    f"Download clip {index}", path, f"{prefix}-clip-{index}"
                )
        if not playable and not media.get("video_paths"):
            st.warning("No verified MP4 is available yet.")
    with queue_tab:
        rows = queue_rows(media)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("The production queue has not been created yet.")
        st.json(
            {
                "status": media.get("status"),
                "stopReason": media.get("stopReason"),
                "fractures": media.get("fractures") or [],
            }
        )
    with identity_tab:
        identity = result.get("starIdentity") or {}
        if identity:
            profile = identity.get("profile") or {}
            st.write(
                f"**Star:** {profile.get('subjectName', 'Unknown')}  \n"
                f"**Mode:** {profile.get('mode', 'unknown')}  \n"
                f"**Authorized references:** {profile.get('referenceCount', 0)}"
            )
            st.caption(
                "Silver-Screen stores the authorized images as production assets. It "
                "does not create facial-recognition or biometric embeddings."
            )
            for image_path in identity.get("referencePaths") or []:
                try:
                    st.image(image_path, width=220)
                except Exception:
                    continue
        else:
            st.info("The identity pack is saved after the first production checkpoint.")
    with story_tab:
        st.subheader(state.get("logline") or title)
        st.json(state.get("storyBible") or {})
        st.text_area(
            "Generated screenplay blueprint",
            str(state.get("script") or ""),
            height=500,
            key=f"{prefix}-screenplay",
        )

    st.info(
        "After footage is verified, open **Professional Script Sync** to enter exact "
        "dialogue, assign voices, time the words, create subtitles, and export a "
        "synchronized professional cut."
    )
    try:
        st.page_link(
            "pages/2_Professional_Script_Sync.py",
            label="Open Professional Script Sync",
            icon="🎙️",
        )
    except Exception:
        pass


def render_star_vehicle_page(
    *,
    page_title: str,
    page_icon: str,
    default_starter: str,
    default_render_mode: str,
) -> None:
    """Render a complete generic film page with an optional authorized star lock."""

    st.set_page_config(
        page_title=f"Silver-Screen | {page_title}",
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.title(f"{page_icon} {page_title}")
    st.caption(SCIENCE["credit"])
    st.write(
        "Silver-Screen is a general-purpose film studio. Build an original project, "
        "use an authorized character, or upload your own reference images and make "
        "yourself the lead. Moonie Moo remains an optional example, not the product default."
    )

    replicate_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
    video_model = os.getenv("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3.1-fast")
    clip_duration = int(os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8") or 8)
    if clip_duration not in {4, 6, 8}:
        clip_duration = 8

    labels = starter_labels()
    starter_keys = list(labels)
    selected_default = (
        default_starter if default_starter in starter_keys else "blank_project"
    )

    with st.sidebar:
        st.header("Project")
        starter_key = st.selectbox(
            "Project starter",
            starter_keys,
            index=starter_keys.index(selected_default),
            format_func=lambda key: labels[key],
            help="Moonie Moo is available only as an optional example starter.",
        )
        starter = starter_payload(starter_key)
        key_suffix = starter_key

        title = st.text_input(
            "Title", value=str(starter["title"]), key=f"title-{key_suffix}"
        )
        premise = st.text_area(
            "Premise",
            str(starter["premise"]),
            height=150,
            max_chars=4000,
            key=f"premise-{key_suffix}",
        )
        genre_options = list(GENRES)
        starter_genre = (
            starter["genre"] if starter["genre"] in genre_options else "drama"
        )
        genre = st.selectbox(
            "Genre",
            genre_options,
            index=genre_options.index(starter_genre),
            key=f"genre-{key_suffix}",
        )
        tone_options = list(TONES)
        starter_tone = (
            starter["tone"] if starter["tone"] in tone_options else "cinematic"
        )
        tone = st.selectbox(
            "Tone",
            tone_options,
            index=tone_options.index(starter_tone),
            key=f"tone-{key_suffix}",
        )
        format_options = list(FORMATS)
        starter_format = (
            starter["format"] if starter["format"] in format_options else "trailer"
        )
        format_key = st.selectbox(
            "Story format",
            format_options,
            index=format_options.index(starter_format),
            format_func=lambda item: (
                f"{FORMATS[item]['label']} | {FORMATS[item]['minutes']} min blueprint"
            ),
            key=f"format-{key_suffix}",
        )
        seed = st.text_input(
            "Seed", placeholder="Derived automatically", key=f"seed-{key_suffix}"
        )

        st.divider()
        st.header("Lead star")
        mode_keys = list(MODE_LABELS)
        starter_mode = starter["mode"] if starter["mode"] in mode_keys else "fictional_character"
        mode = st.selectbox(
            "Lead type",
            mode_keys,
            index=mode_keys.index(starter_mode),
            format_func=lambda item: MODE_LABELS[item],
            key=f"mode-{key_suffix}",
        )
        lead_name = st.text_input(
            "Star name",
            value=str(starter["lead_name"]),
            placeholder="Your name or character name",
            key=f"lead-name-{key_suffix}",
        )
        lead_role = st.text_input(
            "Role",
            value=str(starter["lead_role"]),
            key=f"lead-role-{key_suffix}",
        )
        lead_appearance = st.text_area(
            "Appearance description",
            str(starter["lead_appearance"]),
            height=105,
            key=f"lead-appearance-{key_suffix}",
        )
        wardrobe = st.text_area(
            "Signature wardrobe",
            str(starter["wardrobe"]),
            height=85,
            key=f"wardrobe-{key_suffix}",
        )
        invariants = st.text_area(
            "Identity details that must never drift",
            str(starter["identity_invariants"]),
            height=95,
            help=(
                "Examples: hairstyle, facial hair, tattoos, glasses, body proportions, "
                "character markings, jewelry, or signature accessories."
            ),
            key=f"invariants-{key_suffix}",
        )
        performance = st.text_area(
            "Performance direction",
            str(starter["performance_style"]),
            height=75,
            key=f"performance-{key_suffix}",
        )

        images = (
            st.file_uploader(
                "Authorized star reference images",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
                help=(
                    "Upload up to six images. Put the strongest clean medium/full-body "
                    "image first or choose it below. The current Veo path uses one primary "
                    "image as the opening identity anchor and then chains verified frames."
                ),
                key=f"star-images-{key_suffix}",
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
                key=f"primary-image-{key_suffix}",
            )
        authorization_required = bool(images) or mode in {
            "authorized_person",
            "authorized_character",
        }
        authorization = st.checkbox(
            (
                "I am the person shown, or I have explicit permission to use this "
                "person, character, and uploaded likeness in this production."
            ),
            value=False,
            disabled=not authorization_required,
            key=f"authorization-{key_suffix}",
        )
        if mode == "self":
            st.caption(
                "Use recent, clear photos of yourself. Silver-Screen does not identify "
                "who is in them; it treats them only as authorized visual references."
            )

        with st.expander("Supporting character", expanded=False):
            support_name = st.text_input(
                "Supporting character name",
                value=str(starter["support_name"]),
                key=f"support-name-{key_suffix}",
            )
            support_role = st.text_input(
                "Supporting role",
                value=str(starter["support_role"]),
                key=f"support-role-{key_suffix}",
            )
            support_description = st.text_area(
                "Supporting appearance",
                str(starter["support_description"]),
                height=90,
                key=f"support-description-{key_suffix}",
            )

        profile = normalize_star_profile(
            {
                "mode": mode,
                "subjectName": lead_name,
                "role": lead_role,
                "appearance": lead_appearance,
                "wardrobe": wardrobe,
                "identityInvariants": invariants,
                "performanceStyle": performance,
                "authorizationConfirmed": authorization if authorization_required else True,
                "primaryReferenceIndex": primary_index,
            }
        )
        profile_error = _profile_error(profile, images)
        if profile_error:
            st.warning(profile_error)

        st.divider()
        st.header("Render plan")
        render_labels = ["Match the blueprint", "One-clip identity test", "Custom runtime"]
        default_label = {
            "match_blueprint": "Match the blueprint",
            "preview": "One-clip identity test",
            "custom": "Custom runtime",
        }.get(default_render_mode, "One-clip identity test")
        render_label = st.radio(
            "Render length",
            render_labels,
            index=render_labels.index(default_label),
            key=f"render-mode-{key_suffix}",
        )
        render_mode = {
            "Match the blueprint": "match_blueprint",
            "One-clip identity test": "preview",
            "Custom runtime": "custom",
        }[render_label]
        custom_runtime = None
        if render_mode == "custom":
            custom_runtime = st.number_input(
                "Custom runtime (seconds)",
                min_value=4,
                max_value=5400,
                value=60,
                step=clip_duration,
                key=f"custom-runtime-{key_suffix}",
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
            f"**Clips:** {plan.planned_clips} × about {plan.clip_duration_seconds}s"
        )
        if render_mode == "preview":
            st.caption(
                "The one-clip identity test is the safest way to confirm that your "
                "appearance and overall style work before authorizing the full film."
            )
        batch_size = int(
            st.number_input(
                "New clips per checkpoint",
                min_value=1,
                max_value=max(1, min(16, plan.planned_clips)),
                value=1,
                step=1,
                key=f"batch-{key_suffix}",
            )
        )
        retries = int(
            st.slider(
                "TGRM retries per clip",
                0,
                6,
                1,
                key=f"retries-{key_suffix}",
            )
        )
        provider_call_budget = recommended_provider_call_budget(
            plan, retries_per_clip=retries, include_retry_capacity=True
        )
        st.caption(
            f"Whole-production safety ceiling: **{provider_call_budget} calls**. "
            f"This checkpoint creates at most **{batch_size} new clips**."
        )
        use_continuity = st.checkbox(
            "Chain verified final frames",
            value=True,
            key=f"continuity-{key_suffix}",
        )
        continuous = st.checkbox(
            "Continue in this browser request until complete",
            value=False,
            help="Leave off on hosted Streamlit. Durable checkpoints are safer.",
            key=f"continuous-{key_suffix}",
        )
        needs_confirmation = requires_continuous_confirmation(plan, continuous=continuous)
        continuous_confirmed = False
        if needs_confirmation:
            continuous_confirmed = st.checkbox(
                f"I authorize continuous work up to {provider_call_budget} calls.",
                value=False,
                key=f"continuous-confirm-{key_suffix}",
            )

        with st.expander("Provider status", expanded=True):
            st.write(
                f"Replicate: **{'ready' if replicate_ready else 'missing'}** "
                "(`REPLICATE_API_TOKEN`)"
            )
            st.write(f"Video model: `{video_model}`")
            st.caption(
                "OpenAI or ElevenLabs is optional and is used later for speech. "
                "The visual star workflow itself requires Replicate."
            )
            if not replicate_ready:
                st.error("Add REPLICATE_API_TOKEN to Streamlit deployment secrets.")

        start_disabled = bool(
            not replicate_ready
            or profile_error
            or len(premise.strip()) < 12
            or len(images) > MAX_REFERENCE_IMAGES
            or (needs_confirmation and not continuous_confirmed)
        )
        start_clicked = st.button(
            "Start star production" if default_starter == "star_as_myself" else "Start production",
            type="primary",
            use_container_width=True,
            disabled=start_disabled,
            key=f"start-{key_suffix}",
        )

        st.divider()
        st.header("Continue a saved production")
        resumable = list_resumable_runs("runs", 50)
        resume_map = {_run_label(record): record for record in resumable}
        selected_resume = st.selectbox(
            "Saved checkpoint",
            list(resume_map) or ["No resumable productions"],
            disabled=not resume_map,
            key=f"resume-run-{page_title}",
        )
        resume_batch = int(
            st.number_input(
                "New clips this continuation",
                min_value=1,
                max_value=16,
                value=1,
                step=1,
                disabled=not resume_map,
                key=f"resume-batch-{page_title}",
            )
        )
        resume_clicked = st.button(
            "Continue selected production",
            use_container_width=True,
            disabled=not resume_map or not replicate_ready,
            key=f"resume-button-{page_title}",
        )

    result_key = f"star-result-{page_title}"
    if start_clicked:
        ordered_images = reorder_primary_reference(images, primary_index)
        cast = build_star_cast(
            profile,
            support_name=support_name,
            support_role=support_role,
            support_description=support_description,
        )
        brief: dict[str, Any] = {
            "title": title,
            "premise": premise,
            "genre": genre,
            "tone": tone,
            "format": format_key,
            "cast": cast,
        }
        if seed.strip():
            brief["seed"] = seed.strip()
        bar = st.progress(0, text="Starting production")
        message = st.empty()
        try:
            result = run_pipeline(
                brief,
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
                video_max_provider_calls=provider_call_budget,
                video_continuous=bool(continuous),
                video_use_continuity=bool(use_continuity),
                progress=_progress(bar, message),
            )
            run_id = str((result.get("run") or {}).get("id") or "")
            if run_id:
                identity = persist_star_profile(
                    run_id,
                    profile,
                    ordered_images,
                    output_root="runs",
                )
                result["starIdentity"] = identity
            st.session_state[result_key] = result
            view = production_view(result.get("media") or {})
            bar.progress(view.progress_percent, text=view.headline)
        except (BriefValidationError, PipelineError, ValueError, TypeError) as exc:
            st.error(str(exc))

    if resume_clicked and resume_map:
        record = resume_map[selected_resume]
        bar = st.progress(0, text="Opening checkpoint")
        message = st.empty()
        try:
            result = resume_video_run(
                str(record.get("runId")),
                output_root="runs",
                batch_size=resume_batch,
                continuous=False,
                use_continuity=True,
                progress=_progress(bar, message),
            )
            st.session_state[result_key] = result
            view = production_view(result.get("media") or {})
            bar.progress(view.progress_percent, text=view.headline)
        except PipelineError as exc:
            st.error(str(exc))

    result = st.session_state.get(result_key)
    if not result:
        st.info(
            "Start with one identity-test clip. When it looks right, extend or continue "
            "the same saved production so accepted footage is not wasted."
        )
        return
    _show_result(result, prefix=slug_for_key(page_title))


def slug_for_key(value: str) -> str:
    return "-".join(part for part in value.lower().replace("&", "and").split() if part)
