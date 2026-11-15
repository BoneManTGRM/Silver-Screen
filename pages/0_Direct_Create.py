"""Streamlit workspace for direct image and clip generation."""

from __future__ import annotations

import base64
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from subprocess import SubprocessError
from time import monotonic

import streamlit as st
from PIL import Image

from silver_screen.action_short import SHOTS
from silver_screen.ai_video import (
    ReplicateVideoClient,
    TERMINAL_STATUSES,
    VideoGenerationError,
    image_to_data_url,
)
from silver_screen.cast_library import cast_library_root, saved_cast_photos, saved_clone_samples, saved_voice_takes
from silver_screen.comfyui import (
    IMAGE_SIZES, ComfyUIClient, VIDEO_SIZES, WAN_14B_LANDSCAPE_HD, WAN_14B_MAX_SECONDS, WAN_14B_STEPS, WAN_14B_VIDEO_SIZES,
    WAN_LANDSCAPE_HD, comfyui_ready, image_size_ready, make_comfyui_client, video_controls_ready, wan_aspect_size,
)
from silver_screen.direct_generation import (
    CLIP_MODEL,
    FILM_CANVASES,
    FILM_LIMIT,
    IMAGE_MODEL,
    assemble_direct_film,
    direct_prompt_aspect,
    direct_prompt_seconds,
    finish_direct_clip,
    prepare_edit_reference,
    prepare_wan_reference,
    read_direct_output,
    submit_direct_generation,
    verify_direct_clip,
    verify_direct_output,
)
from silver_screen.hand_review import hand_review_message
from silver_screen.modal_clip_archive import MODAL_RENDER_ID, RecentRender
from silver_screen.modal_renderer import (
    ModalComfyUIClient, delete_modal_render, modal_configured, recent_modal_renders, remember_modal_render,
)
from silver_screen.voice_config import VoiceStudioError
from silver_screen.voice_clone import LANGUAGES, render_cloned_line, voice_clone_ready


AUTO_FRAME = "Auto"
WAN_5B_LABEL = "Wan 2.2 5B · 24 fps"
WAN_14B_LABEL = "Wan 2.2 14B · 16 fps"
WAN_14B_DEFAULT_STEPS = 28


def save_render_job(job: dict) -> None:
    st.session_state["direct_create_job"] = job
    render_id = job["prediction"].get("id") or ""
    if job["backend"] == "ComfyUI" and MODAL_RENDER_ID.fullmatch(render_id):
        seconds = job.get("requested_seconds") if job["model"] != IMAGE_MODEL else None
        if seconds is None:
            st.query_params.pop("render_seconds", None)
            st.query_params["render"] = render_id
        else:
            st.query_params.update({"render": render_id, "render_seconds": str(seconds)})
    else:
        st.query_params.pop("render", None)
        st.query_params.pop("render_seconds", None)


def recover_modal_job(render_id: str, seconds: str = "", *, image: bool = False) -> dict:
    job = {
        "backend": "ComfyUI",
        "model": IMAGE_MODEL if image else CLIP_MODEL,
        "recovered": True,
        "prediction": {"id": render_id, "status": "processing"},
    }
    if re.fullmatch(r"[2-9]|1[0-2]", seconds):
        job["requested_seconds"] = int(seconds)
    return job


def open_recent_render(entry: RecentRender) -> dict:
    job = recover_modal_job(entry.render_id, str(entry.requested_seconds or ""), image=entry.image)
    job["read_saved_render"] = entry.saved
    job["history_saved"] = True
    save_render_job(job)
    return job


def load_recent_renders() -> None:
    try:
        st.session_state["recent_modal_renders"] = {
            entry.render_id: entry for entry in recent_modal_renders()
        }
        st.session_state.pop("recent_render_error", None)
    except VideoGenerationError as exc:
        st.session_state["recent_render_error"] = str(exc)


def needs_hand_review(job: dict) -> bool:
    return job["prediction"].get("hand_review", {}).get("status") == "possible_loss" and not job.get("hand_reviewed")


def retain_film_shot(job: dict, film_shots: dict, data: bytes) -> None:
    number = job.get("shot_number")
    if not number or needs_hand_review(job):
        return
    existing_size = sum(len(clip) for key, clip in film_shots.items() if key != number)
    if existing_size + len(data) > FILM_LIMIT:
        job["film_warning"] = "This shot exceeds the film workspace. Download it separately."
        return
    film_shots[number] = data
    job.pop("film_warning", None)
    st.session_state.pop("action_film_export", None)


def refresh_render(job, film_shots, *, wait=False):
    def save_prediction(update):
        if update.get("status") != "succeeded":
            job["prediction"] = update

    prediction = job["prediction"]
    is_image = job["model"] == IMAGE_MODEL
    job.pop("check_error", None)
    try:
        client = (
            make_comfyui_client(kind="image" if is_image else "video")
            if job.get("backend") == "ComfyUI"
            else ReplicateVideoClient(model=job["model"])
        )
        if isinstance(client, ModalComfyUIClient) and job.get("read_saved_render"):
            prediction = client.get_saved_prediction(prediction["id"])
        elif wait:
            prediction = client.wait(prediction, on_update=save_prediction)
        elif prediction.get("status") not in TERMINAL_STATUSES:
            prediction = (
                client.get_prediction(prediction["id"], prefer_saved=True)
                if isinstance(client, ModalComfyUIClient)
                else client.get_prediction(prediction["id"])
            )
        if prediction.get("status") != "succeeded":
            save_prediction(prediction)
            return
        if isinstance(client, ComfyUIClient):
            data = client.read_output(prediction)
            if job.get("recovered"):
                is_image = data.startswith(b"\x89PNG\r\n\x1a\n")
                job["model"] = IMAGE_MODEL if is_image else CLIP_MODEL
            try:
                if is_image:
                    verify_direct_output(data, image=True, expected_size=job.get("requested_size"))
                else:
                    expected = job.get("requested_seconds") if prediction["id"].startswith("fc-") else None
                    job["actual_seconds"] = verify_direct_clip(
                        data, expected_seconds=expected, expected_size=job.get("requested_size"),
                    )
            except (VideoGenerationError, OSError, Image.DecompressionBombError) as exc:
                save_prediction(dict(prediction, status="failed", error=str(exc)))
                return
        else:
            data = read_direct_output(prediction, image=is_image)
        job.update(prediction=prediction, data=data)
        if isinstance(client, ModalComfyUIClient) and prediction.get("archived") and not job.get("read_saved_render"):
            st.session_state.pop("recent_modal_renders", None)
        if "started" in job:
            job["elapsed_seconds"] = monotonic() - job["started"]
        retain_film_shot(job, film_shots, data)
    except (VideoGenerationError, OSError, SubprocessError, Image.DecompressionBombError) as exc:
        job["check_error"] = str(exc)


@st.fragment(run_every=5)
def show_modal_progress(job, film_shots):
    status = st.empty()
    if "started" in job:
        minutes, seconds = divmod(int(monotonic() - job["started"]), 60)
        st.caption(f"Time since submission · {minutes}:{seconds:02d}")
    if st.button("Check render status", key="direct-check-job"):
        with st.spinner("Checking Modal…"):
            refresh_render(job, film_shots)
        job["last_manual_check"] = monotonic()
    else:
        refresh_render(job, film_shots)
    if job["prediction"].get("status") in TERMINAL_STATUSES:
        st.rerun()
    if error := job.get("check_error"):
        status.warning(error)
    else:
        if job.get("history_saved"):
            recovery = "You can return to this job from Recent renders."
        else:
            recovery = "Keep this page’s link to reopen the same render."
        status.info(f"Waiting for Modal. {recovery}")
    if checked := job.get("last_manual_check"):
        seconds_ago = int(monotonic() - checked)
        st.caption(f"Last manual check · {seconds_ago}s ago")


st.set_page_config(page_title="Direct Create | Silver-Screen", page_icon="🎨")
st.title("Direct Create")
st.caption("Describe the subject, one clear action, and how the camera should frame it.")
if notice := st.session_state.pop("media_delete_notice", None):
    st.success(notice)

with st.expander("Create offline on iPhone"):
    st.write(
        "Use the Silver Screen Offline script in Draw Things to generate on your iPhone. "
        "Download a local model first, then enter your prompt in Draw Things."
    )
    script = Path(__file__).resolve().parents[1] / "deploy" / "iphone" / "silver-screen-offline.js"
    st.download_button(
        "Download Silver Screen Offline",
        script.read_bytes(),
        file_name=script.name,
        mime="application/javascript",
    )
    st.markdown(
        "Open the Draw Things script editor, import or paste the script, and run it after "
        "selecting a downloaded image or video model."
    )


job = st.session_state.get("direct_create_job")
render_id = st.query_params.get("render", "")
modal_ready = modal_configured()
if modal_ready and "recent_modal_renders" not in st.session_state:
    load_recent_renders()

if job is None and MODAL_RENDER_ID.fullmatch(render_id):
    job = recover_modal_job(render_id, st.query_params.get("render_seconds", ""))
    st.session_state["direct_create_job"] = job
elif job and not render_id and MODAL_RENDER_ID.fullmatch(job["prediction"].get("id") or ""):
    save_render_job(job)

if modal_ready and "recent_modal_renders" in st.session_state and not st.session_state.get("initial_render_checked"):
    st.session_state["initial_render_checked"] = True
    if job is None:
        latest = max(
            (entry for entry in st.session_state["recent_modal_renders"].values() if entry.submitted_at is not None),
            key=lambda entry: (entry.submitted_at, entry.render_id), default=None,
        )
        if latest is not None:
            job = open_recent_render(latest)

if job and job.get("requested_seconds") and job["model"] == CLIP_MODEL:
    st.session_state.setdefault("direct_create_output", "Video clip")
kind = st.radio("Output", ("Image", "Video clip"), horizontal=True, key="direct_create_output")
is_image = kind == "Image"
workflow_kind = "image" if is_image else "video"
backend = st.radio(
    "Generation backend", ("ComfyUI", "Replicate"),
    index=0 if comfyui_ready(workflow_kind) else 1,
    horizontal=True,
)
use_comfyui = backend == "ComfyUI"
use_modal = use_comfyui and modal_ready
reference_ready = use_comfyui and comfyui_ready("reference")
video_controls = (
    not is_image and use_comfyui and video_controls_ready("video")
    and (not reference_ready or video_controls_ready("reference"))
)
library_root = cast_library_root()
library_photos = saved_cast_photos(library_root) if library_root and reference_ready else {}
shot = None
shot_number = 0
cast_photos = dict(library_photos)
cast_permission = False
intimacy_permission = False
film_shots = st.session_state.setdefault("action_film_shots", {})
custom_shots = st.session_state.setdefault("custom_film_shots", {})
if not is_image:
    with st.expander("Last Train Out · 60-second action short"):
        if st.checkbox("Use this shot list"):
            shot_number = st.selectbox(
                "Shot", range(1, len(SHOTS) + 1),
                format_func=lambda number: f"{number}. {SHOTS[number - 1].title}",
            )
            shot = SHOTS[shot_number - 1]
            st.caption("Render the shots you want, record dialogue, then export a cut below.")
            if reference_ready:
                st.caption("Wan uses one starting frame per shot. For a shot with both leads, use a photo showing both of you.")
                for label in ("Your photo", "Co-star photo", "Both of you in one photo"):
                    upload = st.file_uploader(
                        label, type=["png", "jpg", "jpeg", "webp"], key=f"action-cast-{label}",
                        help="Uses your saved photo if you leave this empty." if label in library_photos else None,
                    )
                    if upload is not None:
                        cast_photos[label] = upload
            else:
                st.caption("Select ComfyUI with a reference workflow to cast from photos.")
    if any(label in cast_photos for label in ("Co-star photo", "Both of you in one photo")):
        cast_permission = st.checkbox("My co-star agreed to use their likeness in this film.")
        if shot and shot.intimate:
            intimacy_permission = st.checkbox("My co-star agreed to this intimate scene and who can see it.")

if not use_comfyui:
    st.caption("Each generation submits a paid Replicate prediction.")

if modal_ready:
    with st.expander("Recent renders", expanded=not job):
        if st.button("Refresh recent renders", key="direct-refresh-saved-clips"):
            load_recent_renders()
        if history_error := st.session_state.get("recent_render_error"):
            st.warning(history_error)
        if renders := st.session_state.get("recent_modal_renders"):
            selected_id = st.selectbox(
                "Render", renders, index=None, placeholder="Choose a render",
                format_func=lambda value: (
                    f"{'Saved' if renders[value].saved else 'Submitted'} "
                    f"{'image' if renders[value].image else 'clip'} · "
                    f"{datetime.fromtimestamp(renders[value].timestamp, timezone.utc):%b %d, %H:%M} UTC · {value[-6:]}"
                ),
            )
            if st.button("Open render", disabled=selected_id is None):
                open_recent_render(renders[selected_id])
                st.rerun()
        else:
            st.caption("New images and clips appear here after submission.")

prepared_reference = None
prepared_end = None
prepared_edit = None
end_image = None
reference_label = "No photo"
frame_size = None
frame_format = AUTO_FRAME
reference = None
reference_error = None
if is_image and use_comfyui and comfyui_ready("image_edit"):
    source_image = st.file_uploader("Image to edit (optional)", type=["png", "jpg", "jpeg", "webp"], key="direct-edit-image")
    if source_image is not None:
        try:
            prepared_edit = prepare_edit_reference(source_image)
        except VideoGenerationError as exc:
            reference_error = str(exc)
            st.error(reference_error)
        else:
            st.image(prepared_edit, caption="Source image", width=320)
image_kind = "image_edit" if prepared_edit else "image"
image_controls = is_image and use_comfyui and image_size_ready(image_kind)
if use_modal and is_image:
    st.caption("FLUX.2 Klein" if prepared_edit or image_controls else "FLUX.1 Schnell")
if reference_ready and not is_image:
    if shot:
        choices = ["No photo", *(label for label, photo in cast_photos.items() if photo)]
        reference_label = st.selectbox(
            "Starting frame", choices,
            index=choices.index(shot.reference) if shot.reference in choices else 0,
            key=f"action-reference-{shot_number}",
        )
        reference = cast_photos.get(reference_label)
    else:
        reference = st.file_uploader(
            "Starting image (optional)", type=["png", "jpg", "jpeg", "webp"],
            help="Use a photo that already shows the framing you want. Auto follows the photo’s orientation.",
        )
        if library_photos:
            reference_label = st.selectbox("Or use a saved cast photo", ["No photo", *library_photos])
            if reference_label != "No photo":
                reference = library_photos[reference_label]
wan_14b = False
if use_modal and not is_image:
    if video_controls:
        model_choices = (WAN_14B_LABEL, WAN_5B_LABEL) if reference is not None else (WAN_5B_LABEL, WAN_14B_LABEL)
        wan_14b = st.selectbox(
            "Render model", model_choices, key="wan-model-with-photo" if reference is not None else "wan-model-from-text",
            help="14B is available from a prompt alone or with a starting photo. It uses more Modal GPU time and supports clips up to five seconds.",
        ) == WAN_14B_LABEL
    else:
        st.caption(WAN_5B_LABEL)
if video_controls:
    with st.expander("Starting frame and render size", expanded=reference is not None):
        sizes = WAN_14B_VIDEO_SIZES if wan_14b else VIDEO_SIZES
        frame_format = st.selectbox(
            "Frame size", (AUTO_FRAME, *sizes),
            help="Auto uses the format in your prompt or the starting photo’s proportions. Fixed sizes crop the photo to fill the frame.",
        )
        frame_size = sizes.get(frame_format)
        if reference is not None:
            try:
                prepared_reference, frame_size = prepare_wan_reference(reference, frame_size, wan_14b=wan_14b)
            except VideoGenerationError as exc:
                reference_error = str(exc)
                st.error(reference_error)
            else:
                st.image(prepared_reference, caption=f"Starting frame · {frame_size[0]} × {frame_size[1]}", width=320)
                if wan_14b:
                    end_image = st.file_uploader(
                        "Ending image (optional)", type=["png", "jpg", "jpeg", "webp"], key="wan-ending-image",
                        help="Guide the final pose with a second photo of the same subject and scene.",
                    )
                    if end_image is not None:
                        try:
                            prepared_end, _ = prepare_wan_reference(end_image, frame_size, wan_14b=True)
                        except VideoGenerationError as exc:
                            reference_error = f"Ending image. {exc}"
                            st.error(reference_error)
                        else:
                            st.image(prepared_end, caption="Ending frame", width=320)

with st.form("direct_create"):
    prompt = st.text_area(
        "Prompt", value=shot.prompt if shot else "", height=180,
        key=f"action-shot-prompt-{shot_number}" if shot else f"direct_prompt_{kind}",
        placeholder="Describe what to change and what to keep." if prepared_edit else None if is_image else (
            "A runner takes three steady steps. The camera tracks alongside in a full-body shot. "
            "Keep the runner’s head, hands, and shoes visible."
        ),
    )
    duration = 8
    ratio = "16:9"
    audio = True
    render_options = {}
    image_format = AUTO_FRAME
    seed_text = ""
    if image_controls:
        image_format = st.selectbox(
            "Frame format", (AUTO_FRAME, *IMAGE_SIZES),
            help="Auto uses the format in your prompt. Otherwise an edit follows the source photo and a new image is square.",
        )
    if not use_comfyui:
        ratios = ("1:1", "16:9", "9:16", "4:3", "3:4") if is_image else ("16:9", "9:16")
        ratio = st.selectbox("Aspect ratio", ratios)
    if not is_image:
        if video_controls:
            maximum = WAN_14B_MAX_SECONDS if wan_14b else 12
            default_duration = 3 if wan_14b else (5 if use_modal else 8)
            duration = st.number_input(
                "Clip length in seconds", min_value=2, max_value=maximum,
                value=min(shot.seconds if shot else default_duration, maximum),
                key=f"wan-clip-length-{shot_number}-{maximum}",
                help="A total clip length stated in the prompt sets the render duration.",
            )
        else:
            durations = (8, 6, 4)
            duration = st.selectbox(
                "Clip length in seconds", durations, index=durations.index(shot.seconds) if shot else 0,
                key=f"direct-clip-length-{shot_number}",
            )
        if not use_comfyui:
            audio = st.checkbox("Generate audio", value=True)
        if video_controls:
            with st.expander("Advanced render settings"):
                default_steps = WAN_14B_DEFAULT_STEPS if wan_14b else (24 if use_modal else 20)
                steps = st.slider(
                    "Sampling steps", 12, WAN_14B_STEPS if wan_14b else 32, default_steps,
                    help=("28 balances detail and render time. Choose 40 for more sampling."
                          if wan_14b else "More steps increase render time. Compare shots using the same seed."),
                )
                seed_text = st.text_input("Seed (optional)", placeholder="Random each time")
                negative = st.text_area("Things to avoid (optional)")
                default_size = WAN_14B_LANDSCAPE_HD if wan_14b else WAN_LANDSCAPE_HD
                render_options = {"video_size": frame_size or default_size, "steps": steps, "negative_prompt": negative}
    active = bool(job and job["prediction"].get("status") not in TERMINAL_STATUSES and "data" not in job)
    generate = st.form_submit_button("Generate", type="primary", disabled=active or reference_error is not None)

if generate and not active:
    model = IMAGE_MODEL if is_image else CLIP_MODEL
    started = monotonic()
    requested_size = None
    try:
        if reference_label in ("Co-star photo", "Both of you in one photo"):
            if not cast_permission:
                raise VideoGenerationError("Confirm your co-star agreed to this use of their likeness.")
            if shot and shot.intimate and not intimacy_permission:
                raise VideoGenerationError("Confirm your co-star agreed to this intimate scene and who can see it.")
        with st.spinner(f"Submitting to {backend}…"):
            if use_comfyui:
                seed = secrets.randbelow(2**31)
                if seed_text.strip():
                    try:
                        seed = int(seed_text.strip())
                        if not 0 <= seed < 2**53:
                            raise ValueError
                    except ValueError as exc:
                        raise VideoGenerationError("Use a whole-number seed from 0 to 9007199254740991.") from exc
                    if use_modal:
                        render_options["allow_retake"] = False
                if image_controls:
                    requested_format = direct_prompt_aspect(prompt) if image_format == AUTO_FRAME else image_format
                    if requested_format:
                        requested_size = IMAGE_SIZES[requested_format]
                        render_options["image_size"] = requested_size
                if video_controls:
                    requested_duration = direct_prompt_seconds(prompt)
                    if requested_duration is not None:
                        if not 2 <= requested_duration <= maximum:
                            raise VideoGenerationError(f"This model supports clips from 2 to {maximum} seconds. Your prompt asks for {requested_duration}.")
                        duration = requested_duration
                    if frame_format == AUTO_FRAME and (requested_format := direct_prompt_aspect(prompt)):
                        requested_size = wan_aspect_size(requested_format, wan_14b=wan_14b)
                        render_options["video_size"] = requested_size
                        if reference is not None:
                            prepared_reference, _ = prepare_wan_reference(
                                reference, requested_size, wan_14b=wan_14b, preserve_framing=True,
                            )
                        if end_image is not None:
                            prepared_end, _ = prepare_wan_reference(
                                end_image, requested_size, wan_14b=True, preserve_framing=True,
                            )
                if prepared_edit is not None or prepared_reference is not None:
                    photo = prepared_edit if prepared_edit is not None else prepared_reference
                    image = "data:image/jpeg;base64," + base64.b64encode(photo).decode("ascii")
                else:
                    image = image_to_data_url(reference, max_bytes=1_500_000)
                if reference is not None and not image:
                    raise VideoGenerationError("The starting image could not be prepared.")
                if use_modal:
                    render_options["save_image" if is_image else "save_clip"] = True
                if wan_14b:
                    render_options["wan_14b"] = True
                if prepared_end is not None:
                    render_options["end_image"] = "data:image/jpeg;base64," + base64.b64encode(prepared_end).decode("ascii")
                prediction = make_comfyui_client(kind=image_kind if is_image else workflow_kind).create_prediction(
                    prompt, seed=seed, duration=duration, image=image, **render_options,
                )
            else:
                prediction = submit_direct_generation(
                    ReplicateVideoClient(model=model), prompt, aspect_ratio=ratio,
                    duration=duration, generate_audio=audio,
                )
        job = {
            "backend": backend, "model": model, "prediction": prediction,
            "started": started, "requested_seconds": duration,
            "submitted_prompt": prompt,
        }
        if requested_size is not None:
            job["requested_size"] = requested_size
        if negative_prompt := render_options.get("negative_prompt"):
            job["submitted_negative_prompt"] = negative_prompt
        if shot:
            job["filename"] = f"last-train-out-{shot_number:02d}.mp4"
            job["shot_number"] = shot_number
        if use_comfyui:
            job["seed"] = seed
        if use_modal and not is_image:
            job["render_model"] = WAN_14B_LABEL if wan_14b else WAN_5B_LABEL
        save_render_job(job)
        if use_comfyui and str(prediction.get("id", "")).startswith("fc-"):
            if use_modal:
                try:
                    remember_modal_render(prediction["id"], None if is_image else duration)
                    job["history_saved"] = True
                    st.session_state.pop("recent_modal_renders", None)
                except VideoGenerationError as exc:
                    job["history_warning"] = str(exc)
            st.rerun()
    except (VideoGenerationError, OSError) as exc:
        st.error(str(exc))
        generate = False

if job:
    prediction = job["prediction"]
    prediction_id = prediction.get("id")
    job_backend = job.get("backend", "Replicate")
    if prediction_id:
        st.caption(f"{job_backend} prediction {prediction_id}")
    if submitted_prompt := job.get("submitted_prompt"):
        with st.expander("Prompt used"):
            st.code(submitted_prompt, language=None)
            if submitted_negative := job.get("submitted_negative_prompt"):
                st.caption("Things to avoid")
                st.code(submitted_negative, language=None)
            if output_size := job.get("requested_size"):
                st.caption(f"Frame size · {output_size[0]} × {output_size[1]}")
            if job["model"] == CLIP_MODEL:
                st.caption(f"Clip length · {job['requested_seconds']} seconds")
    if warning := job.get("history_warning"):
        st.warning(warning)

    modal_job = job_backend == "ComfyUI" and str(prediction_id).startswith("fc-")
    auto_check = modal_job and active
    if auto_check:
        show_modal_progress(job, film_shots)
    elif "data" not in job and prediction.get("status") not in {"failed", "canceled"}:
        check = not generate and st.button("Check render status", key="direct-check-job")
        if check or generate:
            with st.spinner("Checking the render…" if check else "Rendering on the selected backend…"):
                refresh_render(job, film_shots, wait=generate)
            if check and active and job["prediction"].get("status") in TERMINAL_STATUSES:
                st.rerun()
    if job["prediction"].get("status") in {"failed", "canceled"}:
        st.error(job["prediction"].get("error") or f"{job_backend} did not complete the prediction.")
    elif not auto_check and (error := job.get("check_error")):
        st.error(error)
    elif not auto_check and "data" not in job and job["prediction"].get("status") not in TERMINAL_STATUSES:
        st.info("Render submitted. Use Check render status to update this job.")
    if "data" in job:
        if warning := job["prediction"].get("warning"):
            st.warning(warning)
        if warning := job.get("film_warning"):
            st.warning(warning)
        if job["model"] == IMAGE_MODEL:
            st.image(job["data"])
            if job["prediction"].get("archived"):
                st.caption("Saved in your studio")
            st.download_button("Download image", job["data"], "silver-screen.png", "image/png")
        else:
            filename = job.get("filename", "silver-screen.mp4")
            st.success("Your clip is ready.")
            st.video(job["data"], format="video/mp4")
            if review := job["prediction"].get("hand_review"):
                if message := hand_review_message(review):
                    if review.get("status") == "possible_loss":
                        st.warning(message)
                        if needs_hand_review(job) and st.button("Use this take", key=f"hand-review-{prediction_id}"):
                            job["hand_reviewed"] = True
                            retain_film_shot(job, film_shots, job.get("finished_data", job["data"]))
                            st.rerun()
                    else:
                        st.caption(message)
            if seconds := job.get("requested_seconds") or job.get("actual_seconds"):
                elapsed = job.get("elapsed_seconds")
                timing = f" · ready in {elapsed:.1f} seconds" if elapsed is not None else ""
                st.caption(f"{seconds:g}-second clip{timing}")
            seed = job["prediction"].get("hand_review", {}).get("selected_seed", job.get("seed"))
            if seed is not None:
                st.caption(f"Seed {seed}")
            if render_model := job.get("render_model"):
                st.caption(render_model)
            if job["prediction"].get("archived"):
                st.caption("Saved in your studio")
            st.download_button("Download original clip", job["data"], filename, "video/mp4")

            with st.expander("Record dialogue and export this clip"):
                samples = saved_clone_samples(library_root) if library_root and voice_clone_ready() else {}
                st.caption("Set when each line should start in this clip.")
                with st.form(f"finish-clip-{prediction_id}"):
                    recordings = []
                    cloned_lines = []
                    for label, start in (("Your voice", 0.0), ("Co-star voice", 1.0)):
                        line = ""
                        language = "English"
                        if label in samples:
                            line = st.text_area(
                                f"Type a new line for {label.lower()}", max_chars=300,
                                help="A typed line uses this voice clone in place of a recording for this speaker.",
                            ).strip()
                            language = st.selectbox(f"Language for {label.lower()}", list(LANGUAGES))
                        recorded = st.audio_input(f"Record {label.lower()}")
                        uploaded = st.file_uploader(
                            f"Or upload {label.lower()}", type=["wav", "m4a", "mp3", "aac", "ogg", "webm"],
                        )
                        offset = st.number_input(f"{label} start (seconds)", min_value=0.0, value=start, step=0.1)
                        take = recorded if recorded is not None else uploaded
                        takes = saved_voice_takes(library_root, label) if library_root else {}
                        saved = st.selectbox(f"Saved take for {label.lower()}", list(takes), index=None) if takes else None
                        if line:
                            cloned_lines.append((label, language, line, offset))
                        elif saved is not None:
                            recordings.append((takes[saved], offset))
                        elif take is not None:
                            recordings.append((take.getvalue(), offset))
                    voice_permission = "Co-star voice" not in samples or st.checkbox(
                        "My co-star approved using their cloned voice for these lines."
                    )
                    preserve_audio = st.checkbox("Keep background audio under dialogue", value=True)
                    smooth_clip = st.checkbox(
                        "Export at 48 fps", help="Repeats source frames as needed to preserve hands and other fine detail.",
                    )
                    finish = st.form_submit_button("Preview edited clip")
                if finish:
                    try:
                        if not voice_permission and any(line[0] == "Co-star voice" for line in cloned_lines):
                            raise VideoGenerationError("Confirm your co-star approved this use of their cloned voice.")
                        with st.spinner("Mixing audio and preparing the edit…"):
                            tracks = [(take.read_bytes() if isinstance(take, Path) else take, offset)
                                      for take, offset in recordings]
                            for label, language, line, offset in cloned_lines:
                                tracks.append((render_cloned_line(label, language, line), offset))
                            job["finished_data"] = finish_direct_clip(
                                job["data"], tracks, smooth=smooth_clip, preserve_audio=preserve_audio,
                            )
                        retain_film_shot(job, film_shots, job["finished_data"])
                    except (VideoGenerationError, VoiceStudioError, OSError, SubprocessError) as exc:
                        st.error(str(exc))
                if "finished_data" in job:
                    st.video(job["finished_data"], format="video/mp4")
                    st.download_button(
                        "Download edited clip", job["finished_data"], f"{Path(filename).stem}-edited.mp4", "video/mp4",
                    )
            if st.button("Add clip to custom film", disabled=needs_hand_review(job)):
                clip = job.get("finished_data", job["data"])
                clip_id = f"{job_backend}:{prediction_id}"
                previous = custom_shots.get(clip_id)
                used = sum(len(item["data"]) for key, item in custom_shots.items() if key != clip_id)
                if used + len(clip) > FILM_LIMIT:
                    st.error("The film workspace is full. Remove a clip before adding another.")
                else:
                    name = previous["name"] if previous else f"Clip {len(custom_shots) + 1} · {prediction_id}"
                    seconds = job.get("requested_seconds") or int(round(job.get("actual_seconds") or 4))
                    custom_shots[clip_id] = {"name": name, "data": clip, "seconds": max(1, seconds)}
                    current_order = st.session_state.get("custom_film_clip_order")
                    if previous is None and current_order is not None:
                        st.session_state["custom_film_clip_order"] = [*current_order, clip_id]
                    st.session_state.pop("custom_film_export", None)
                    st.success("Clip added to your custom film.")

        if modal_job and MODAL_RENDER_ID.fullmatch(prediction_id):
            image_result = job["model"] == IMAGE_MODEL
            media_label = "image" if image_result else "clip"
            with st.popover(f"Delete {media_label}"):
                st.write(f"Remove this {media_label} from Silver Screen?")
                if st.button(f"Delete this {media_label}", key=f"delete-render-{prediction_id}"):
                    try:
                        delete_modal_render(prediction_id, image=image_result)
                    except VideoGenerationError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop("direct_create_job", None)
                        st.session_state.pop("recent_modal_renders", None)
                        st.query_params.pop("render", None)
                        st.query_params.pop("render_seconds", None)
                        st.session_state["media_delete_notice"] = f"{media_label.capitalize()} deleted."
                        st.rerun()

with st.expander("Recover a Modal render"):
    with st.form("recover-modal-render"):
        previous_id = st.text_input("ComfyUI prediction ID", placeholder="fc-…").strip()
        recover = st.form_submit_button("Find render", disabled=active)
    if recover:
        if not MODAL_RENDER_ID.fullmatch(previous_id):
            st.error("Enter the full ComfyUI prediction ID shown after submitting the render.")
        else:
            save_render_job(recover_modal_job(previous_id))
            st.rerun()

if not is_image and (shot or film_shots):
    with st.expander("Export Last Train Out", expanded=bool(film_shots)):
        st.caption("Choose the shots to include. Silver Screen cuts them in story order and trims the final shot to fit.")
        with st.form("action-film-export"):
            selected = st.multiselect(
                "Shots to include", sorted(film_shots), default=sorted(film_shots),
                format_func=lambda number: f"{number}. {SHOTS[number - 1].title}",
            )
            runtime = st.number_input("Film runtime (seconds)", min_value=1, max_value=12 * len(SHOTS), value=60)
            canvas = st.selectbox("Export frame size", list(FILM_CANVASES))
            smooth_film = st.checkbox("Export at 48 fps", value=False, key="smooth-action-film", help="Keeps the original motion and repeats source frames as needed.")
            export = st.form_submit_button("Export film", disabled=not film_shots)
        if export:
            try:
                with st.spinner("Assembling your film…"):
                    cut = assemble_direct_film([film_shots[number] for number in sorted(selected)], runtime,
                                               smooth=smooth_film, canvas=FILM_CANVASES[canvas])
                st.session_state["action_film_export"] = cut
            except (VideoGenerationError, OSError, SubprocessError) as exc:
                st.error(str(exc))
        if cut := st.session_state.get("action_film_export"):
            st.video(cut, format="video/mp4")
            st.download_button("Download film", cut, "last-train-out.mp4", "video/mp4")

if not is_image and custom_shots:
    with st.expander("Export your custom film", expanded=True):
        st.caption("Clips play in the order selected below. Deselect and reselect a clip to move it to the end.")
        default_order = list(custom_shots) if "custom_film_clip_order" not in st.session_state else None
        selected = st.multiselect("Clips in playback order", list(custom_shots), default=default_order,
                                  format_func=lambda key: custom_shots[key]["name"], key="custom_film_clip_order")
        seconds = sum(custom_shots[key]["seconds"] for key in selected)
        with st.form("custom-film-export"):
            runtime = st.number_input("Custom film runtime (seconds)", min_value=1, value=max(1, seconds))
            canvas = st.selectbox("Custom film frame size", list(FILM_CANVASES))
            smooth_film = st.checkbox("Export custom film at 48 fps", help="Keeps the original motion and repeats source frames as needed.")
            export = st.form_submit_button("Export custom film", disabled=not selected)
        if export:
            try:
                with st.spinner("Assembling your custom film…"):
                    cut = assemble_direct_film([custom_shots[key]["data"] for key in selected], runtime,
                                               smooth=smooth_film, canvas=FILM_CANVASES[canvas])
                st.session_state["custom_film_export"] = cut
            except (VideoGenerationError, OSError, SubprocessError) as exc:
                st.error(str(exc))
        if cut := st.session_state.get("custom_film_export"):
            st.video(cut, format="video/mp4")
            st.download_button("Download custom film", cut, "silver-screen-film.mp4", "video/mp4")
        if len(selected) < len(custom_shots) and st.button("Remove unselected clips from the custom film"):
            for key in set(custom_shots) - set(selected):
                del custom_shots[key]
            st.session_state.pop("custom_film_export", None)
            st.rerun()
