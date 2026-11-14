"""Streamlit workspace for direct image and clip generation."""

from __future__ import annotations

import base64
import os
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
    ComfyUIClient, MOTION_REFERENCE_LIMIT, VIDEO_SIZES, WAN_14B_LANDSCAPE_HD, WAN_14B_MAX_SECONDS,
    WAN_14B_STEPS, WAN_14B_VIDEO_SIZES,
    WAN_LANDSCAPE_HD, WAN_MOTION_NEGATIVE_PROMPT, comfyui_ready, make_comfyui_client, video_controls_ready,
)
from silver_screen.direct_generation import (
    CLIP_MODEL,
    FILM_CANVASES,
    FILM_LIMIT,
    IMAGE_MODEL,
    assemble_direct_film,
    direct_video_prompt,
    finish_direct_clip,
    prepare_wan_reference,
    read_direct_output,
    submit_direct_generation,
    verify_direct_clip,
    verify_direct_output,
)
from silver_screen.hand_review import hand_review_message
from silver_screen.modal_clip_archive import MODAL_RENDER_ID
from silver_screen.modal_renderer import ModalComfyUIClient, modal_configured, saved_clips
from silver_screen.voice_config import VoiceStudioError
from silver_screen.voice_clone import LANGUAGES, render_cloned_line, voice_clone_ready


AUTO_FRAME = "Auto · match the photo’s proportions"
WAN_5B_LABEL = "Wan 2.2 5B · 24 fps"
WAN_14B_LABEL = "Wan 2.2 14B · 16 fps"


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


def recover_modal_job(render_id: str, seconds: str = "") -> dict:
    job = {
        "backend": "ComfyUI",
        "model": CLIP_MODEL,
        "recovered": True,
        "prediction": {"id": render_id, "status": "processing"},
    }
    if re.fullmatch(r"[2-9]|1[0-2]", seconds):
        job["requested_seconds"] = int(seconds)
    return job


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
        if isinstance(client, ModalComfyUIClient) and job.get("read_saved_clip"):
            prediction = client.get_saved_prediction(prediction["id"])
            save_prediction(prediction)
        elif wait:
            prediction = client.wait(prediction, on_update=save_prediction)
        elif prediction.get("status") not in TERMINAL_STATUSES:
            prediction = client.get_prediction(prediction["id"])
            save_prediction(prediction)
        if prediction.get("status") == "succeeded":
            if isinstance(client, ComfyUIClient):
                data = client.read_output(prediction)
                if job.get("recovered"):
                    is_image = data.startswith(b"\x89PNG\r\n\x1a\n")
                    job["model"] = IMAGE_MODEL if is_image else CLIP_MODEL
                try:
                    if is_image:
                        verify_direct_output(data, image=True)
                    else:
                        expected = job.get("requested_seconds") if prediction["id"].startswith("fc-") else None
                        job["actual_seconds"] = verify_direct_clip(data, expected_seconds=expected)
                except (VideoGenerationError, OSError, Image.DecompressionBombError) as exc:
                    save_prediction(dict(prediction, status="failed", error=str(exc)))
                    return
                job["data"] = data
            else:
                job["data"] = read_direct_output(prediction, image=is_image)
            if "started" in job:
                job["elapsed_seconds"] = monotonic() - job["started"]
            retain_film_shot(job, film_shots, job["data"])
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
        status.info("Waiting for Modal. Your result will appear here automatically when it’s ready.")
    if checked := job.get("last_manual_check"):
        seconds_ago = int(monotonic() - checked)
        st.caption(f"Last manual check · {seconds_ago}s ago")


st.set_page_config(page_title="Direct Create | Silver-Screen", page_icon="🎨")
st.title("Direct Create")
st.caption("Describe the subject, one clear action, and how the camera should frame it.")

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
if job is None and MODAL_RENDER_ID.fullmatch(render_id):
    job = recover_modal_job(render_id, st.query_params.get("render_seconds", ""))
    st.session_state["direct_create_job"] = job
elif job and not render_id and MODAL_RENDER_ID.fullmatch(job["prediction"].get("id") or ""):
    save_render_job(job)

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
modal_ready = modal_configured()
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

if use_modal and is_image:
    st.caption("FLUX.1 Schnell")
if not use_comfyui:
    st.caption("Each generation submits a paid Replicate prediction.")

if modal_ready:
    with st.expander("Saved clips", expanded=not job):
        refresh_clips = st.button("Refresh saved clips", key="direct-refresh-saved-clips")
        if refresh_clips or (not job and "saved_clips" not in st.session_state):
            try:
                st.session_state["saved_clips"] = dict(saved_clips())
            except VideoGenerationError as exc:
                st.warning(str(exc))
                st.session_state.setdefault("saved_clips", {})
        if clips := st.session_state.get("saved_clips"):
            clip_id = st.selectbox(
                "Recent clips", clips, index=None, placeholder="Choose a clip",
                format_func=lambda value: f"{datetime.fromtimestamp(clips[value], timezone.utc):%b %d, %H:%M} UTC · {value[-6:]}",
            )
            pending = bool(job and job["prediction"].get("status") not in TERMINAL_STATUSES and "data" not in job)
            if st.button("Open saved clip", disabled=clip_id is None or pending):
                saved_job = recover_modal_job(clip_id)
                saved_job["read_saved_clip"] = True
                save_render_job(saved_job)
                st.rerun()
        else:
            st.caption("Finished clips will appear here after they are saved.")

prepared_reference = None
prepared_end = None
reference_label = "No photo"
frame_size = None
reference = None
reference_error = None
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
motion_reference = None
if use_modal and not is_image:
    if video_controls and (reference is not None or os.getenv("SILVER_SCREEN_TEXT_14B") == "1"):
        wan_14b = st.selectbox(
            "Render model", (WAN_14B_LABEL, WAN_5B_LABEL),
            help="14B uses more Modal GPU time and supports clips up to five seconds.",
        ) == WAN_14B_LABEL
    else:
        st.caption(WAN_5B_LABEL)
    if wan_14b and reference is not None and os.getenv("SILVER_SCREEN_MOTION_REFERENCE") == "1":
        motion_reference = st.file_uploader(
            "Motion reference (optional)", type=["mp4", "mov"], key="wan-motion-reference",
            help="Use a clip at least as long as the shot. The starting photo guides the look. "
                 "The reference video guides movement and framing, and its audio is omitted.",
        )
        if motion_reference is not None and motion_reference.size > MOTION_REFERENCE_LIMIT:
            reference_error = "Choose a motion reference video up to 12 MiB."
            st.error(reference_error)
if video_controls:
    with st.expander("Starting frame and render size", expanded=reference is not None):
        sizes = WAN_14B_VIDEO_SIZES if wan_14b else VIDEO_SIZES
        size = st.selectbox(
            "Frame size", (AUTO_FRAME, *sizes) if reference is not None else tuple(sizes),
            help="Fixed frame sizes center-crop the photo to fill the frame." if reference is not None else None,
        )
        frame_size = sizes.get(size)
        if reference is not None:
            try:
                prepared_reference, frame_size = prepare_wan_reference(reference, frame_size, wan_14b=wan_14b)
            except VideoGenerationError as exc:
                reference_error = str(exc)
                st.error(reference_error)
            else:
                st.image(prepared_reference, caption=f"Starting frame · {frame_size[0]} × {frame_size[1]}", width=320)
                if wan_14b and motion_reference is None:
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
        placeholder=None if is_image else (
            "A runner takes three steady steps. The camera tracks alongside in a full-body shot. "
            "Keep the runner’s head, hands, and shoes visible."
        ),
    )
    duration = 8
    ratio = "16:9"
    audio = True
    video_options = {}
    seed_text = ""
    natural_pace = False
    if not use_comfyui:
        ratios = ("1:1", "16:9", "9:16", "4:3", "3:4") if is_image else ("16:9", "9:16")
        ratio = st.selectbox("Aspect ratio", ratios)
    if not is_image:
        if video_controls:
            maximum = WAN_14B_MAX_SECONDS if wan_14b else 12
            default_duration = 5 if use_modal else 8
            if wan_14b and reference is not None and motion_reference is None:
                default_duration = 3
            duration = st.number_input(
                "Clip length in seconds", min_value=2, max_value=maximum,
                value=min(shot.seconds if shot else default_duration, maximum),
                key=f"wan-clip-length-{shot_number}-{maximum}",
            )
            if use_modal and motion_reference is None:
                natural_pace = st.selectbox(
                    "Action timing", ("Natural real time", "Follow my prompt"),
                    index=1 if shot else 0,
                    help="Use Follow my prompt for intentional slow motion, loops, or other specified timing.",
                ) == "Natural real time"
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
                default_steps = WAN_14B_STEPS if wan_14b else (24 if use_modal else 20)
                steps = st.slider(
                    "Sampling steps", 12, WAN_14B_STEPS if wan_14b else 32, default_steps,
                    help="More steps increase render time. Compare shots using the same seed.",
                )
                seed_text = st.text_input("Seed (optional)", placeholder="Random each time")
                default_negative = WAN_MOTION_NEGATIVE_PROMPT if use_modal else ""
                negative = st.text_area("Things to avoid (optional)", placeholder=default_negative or None)
                negative = ", ".join(filter(None, (default_negative, negative.strip())))
                default_size = WAN_14B_LANDSCAPE_HD if wan_14b else WAN_LANDSCAPE_HD
                video_options = {"video_size": frame_size or default_size, "steps": steps, "negative_prompt": negative}
    active = bool(job and job["prediction"].get("status") not in TERMINAL_STATUSES and "data" not in job)
    generate = st.form_submit_button("Generate", type="primary", disabled=active or reference_error is not None)

if generate and not active:
    model = IMAGE_MODEL if is_image else CLIP_MODEL
    started = monotonic()
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
                if prepared_reference is not None:
                    image = "data:image/jpeg;base64," + base64.b64encode(prepared_reference).decode("ascii")
                else:
                    image = image_to_data_url(reference, max_bytes=1_500_000)
                if reference is not None and not image:
                    raise VideoGenerationError("The starting image could not be prepared.")
                if use_modal and not is_image:
                    video_options["save_clip"] = True
                if wan_14b:
                    video_options["wan_14b"] = True
                if motion_reference is not None:
                    video_options["motion_reference"] = motion_reference.getvalue()
                if prepared_end is not None:
                    video_options["end_image"] = "data:image/jpeg;base64," + base64.b64encode(prepared_end).decode("ascii")
                prediction = make_comfyui_client(kind=workflow_kind).create_prediction(
                    direct_video_prompt(prompt, natural_pace=natural_pace),
                    seed=seed, duration=duration, image=image, **video_options,
                )
            else:
                prediction = submit_direct_generation(
                    ReplicateVideoClient(model=model), prompt, aspect_ratio=ratio,
                    duration=duration, generate_audio=audio,
                )
        job = {
            "backend": backend, "model": model, "prediction": prediction,
            "started": started, "requested_seconds": duration,
        }
        if shot:
            job["filename"] = f"last-train-out-{shot_number:02d}.mp4"
            job["shot_number"] = shot_number
        if use_comfyui:
            job["seed"] = seed
        if use_modal and not is_image:
            job["render_model"] = WAN_14B_LABEL if wan_14b else WAN_5B_LABEL
        save_render_job(job)
        if use_comfyui and str(prediction.get("id", "")).startswith("fc-"):
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
            st.download_button("Download image", job["data"], "silver-screen.png", "image/png")
        else:
            filename = job.get("filename", "silver-screen.mp4")
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
