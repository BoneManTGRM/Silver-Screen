"""Streamlit workspace for direct image and clip generation."""

from __future__ import annotations

import re
import secrets
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
from silver_screen.comfyui import ComfyUIClient, VIDEO_SIZES, comfyui_ready, make_comfyui_client, video_controls_ready
from silver_screen.direct_generation import (
    CLIP_MODEL,
    FILM_CANVASES,
    FILM_LIMIT,
    IMAGE_MODEL,
    assemble_direct_film,
    finish_direct_clip,
    read_direct_output,
    submit_direct_generation,
    verify_direct_output,
)
from silver_screen.voice_config import VoiceStudioError
from silver_screen.voice_clone import LANGUAGES, render_cloned_line, voice_clone_ready

st.set_page_config(page_title="Direct Create | Silver-Screen", page_icon="🎨")
st.title("Direct Create")
st.caption("Send your prompt to the selected model exactly as entered.")

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


kind = st.radio("Output", ("Image", "Video clip"), horizontal=True)
is_image = kind == "Image"
workflow_kind = "image" if is_image else "video"
backend = st.radio(
    "Generation backend", ("ComfyUI", "Replicate"),
    index=0 if comfyui_ready(workflow_kind) else 1,
    horizontal=True,
)
use_comfyui = backend == "ComfyUI"
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

if use_comfyui:
    st.caption("Your ComfyUI workflow controls the model, dimensions, and audio.")
else:
    st.caption("Each generation submits a paid Replicate prediction.")

with st.form("direct_create"):
    prompt = st.text_area(
        "Prompt", value=shot.prompt if shot else "", height=180,
        key=f"action-shot-prompt-{shot_number}" if shot else f"direct_prompt_{kind}",
    )
    duration = 8
    ratio = "16:9"
    audio = True
    reference = None
    reference_label = "No photo"
    video_options = {}
    seed_text = ""
    if not use_comfyui:
        ratios = ("1:1", "16:9", "9:16", "4:3", "3:4") if is_image else ("16:9", "9:16")
        ratio = st.selectbox("Aspect ratio", ratios)
    if not is_image:
        if video_controls:
            duration = st.number_input(
                "Clip length in seconds", min_value=2, max_value=12, value=shot.seconds if shot else 8,
                key=f"wan-clip-length-{shot_number}",
            )
        else:
            durations = (8, 6, 4)
            duration = st.selectbox(
                "Clip length in seconds", durations, index=durations.index(shot.seconds) if shot else 0,
                key=f"direct-clip-length-{shot_number}",
            )
        if reference_ready:
            if shot:
                choices = ["No photo", *(label for label, photo in cast_photos.items() if photo)]
                reference_label = st.selectbox(
                    "Starting frame", choices,
                    index=choices.index(shot.reference) if shot.reference in choices else 0,
                    key=f"action-reference-{shot_number}",
                )
                reference = cast_photos.get(reference_label)
            else:
                reference = st.file_uploader("Starting image (optional)", type=["png", "jpg", "jpeg", "webp"])
                if library_photos:
                    reference_label = st.selectbox("Or use a saved cast photo", ["No photo", *library_photos])
                    if reference_label != "No photo":
                        reference = library_photos[reference_label]
        if not use_comfyui:
            audio = st.checkbox("Generate audio", value=True)
        if video_controls:
            with st.expander("Camera format and render settings"):
                size = st.selectbox("Frame size", list(VIDEO_SIZES))
                steps = st.slider("Sampling steps", 12, 32, 20, help="More steps increase render time. Compare shots using the same seed.")
                seed_text = st.text_input("Seed (optional)", placeholder="Random each time")
                negative = st.text_area("Things to avoid (optional)")
                video_options = {"video_size": VIDEO_SIZES[size], "steps": steps, "negative_prompt": negative}
    job = st.session_state.get("direct_create_job")
    active = bool(job and job["prediction"].get("status") not in TERMINAL_STATUSES and "data" not in job)
    generate = st.form_submit_button("Generate", type="primary", disabled=active)

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
                image = image_to_data_url(reference, max_bytes=1_500_000)
                if reference is not None and not image:
                    raise VideoGenerationError("The starting image could not be prepared.")
                prediction = make_comfyui_client(kind=workflow_kind).create_prediction(
                    prompt, seed=seed, duration=duration, image=image, **video_options,
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
        st.session_state["direct_create_job"] = job
        if use_comfyui and str(prediction.get("id", "")).startswith("fc-"):
            st.rerun()
    except (VideoGenerationError, OSError) as exc:
        st.error(str(exc))
        generate = False

with st.expander("Recover a Modal render"):
    with st.form("recover-modal-render"):
        previous_id = st.text_input("ComfyUI prediction ID", placeholder="fc-…").strip()
        recover = st.form_submit_button("Find render", disabled=active)
    if recover:
        if not re.fullmatch(r"fc-[0-9A-Z]{26}", previous_id):
            st.error("Enter the full ComfyUI prediction ID shown after submitting the render.")
        else:
            job = {
                "backend": "ComfyUI",
                "model": IMAGE_MODEL if is_image else CLIP_MODEL,
                "prediction": {"id": previous_id, "status": "processing"},
            }
            st.session_state["direct_create_job"] = job

if job:
    prediction = job["prediction"]
    prediction_id = prediction.get("id")
    job_backend = job.get("backend", "Replicate")
    job_is_image = job["model"] == IMAGE_MODEL
    if prediction_id:
        st.caption(f"{job_backend} prediction {prediction_id}")

    modal_job = job_backend == "ComfyUI" and str(prediction_id).startswith("fc-")
    check = recover and modal_job and prediction_id == previous_id
    if "data" not in job and (modal_job or not generate) and prediction.get("status") not in {"failed", "canceled"}:
        check = st.button("Check existing prediction", key="direct-check-job") or check
    if "data" not in job and (check or (generate and not modal_job)):
        def save_prediction(update):
            job["prediction"] = update

        try:
            client = (
                make_comfyui_client(kind="image" if job_is_image else "video")
                if job_backend == "ComfyUI"
                else ReplicateVideoClient(model=job["model"])
            )
            with st.spinner("Checking the GPU render…" if check else "Rendering on the selected backend…"):
                if check and prediction_id and prediction.get("status") not in TERMINAL_STATUSES:
                    prediction = client.get_prediction(prediction_id)
                    save_prediction(prediction)
                if not check:
                    prediction = client.wait(prediction, on_update=save_prediction)
            if prediction.get("status") == "succeeded":
                with st.spinner("Verifying the downloaded media…"):
                    if isinstance(client, ComfyUIClient):
                        job["data"] = verify_direct_output(client.read_output(prediction), image=job_is_image)
                    else:
                        job["data"] = read_direct_output(prediction, image=job_is_image)
                if "started" in job:
                    job["elapsed_seconds"] = monotonic() - job["started"]
                if number := job.get("shot_number"):
                    existing_size = sum(len(data) for key, data in film_shots.items() if key != number)
                    if existing_size + len(job["data"]) <= FILM_LIMIT:
                        film_shots[number] = job["data"]
                        st.session_state.pop("action_film_export", None)
                    else:
                        st.warning("This shot exceeds the film workspace. Download it separately.")
        except (VideoGenerationError, OSError, SubprocessError, Image.DecompressionBombError) as exc:
            if job["prediction"].get("status") not in {"failed", "canceled"}:
                st.error(str(exc))
    if job["prediction"].get("status") in {"failed", "canceled"}:
        st.error(job["prediction"].get("error") or f"{job_backend} did not complete the prediction.")
    elif "data" not in job and job["prediction"].get("status") not in TERMINAL_STATUSES:
        st.info("Render submitted. The worker may be starting up, queued, or generating frames. Use Check existing prediction to update this job.")
    if "data" in job:
        if modal_job and check:
            st.rerun()
        if job_is_image:
            st.image(job["data"])
            st.download_button("Download image", job["data"], "silver-screen.png", "image/png")
        else:
            filename = job.get("filename", "silver-screen.mp4")
            st.video(job["data"], format="video/mp4")
            if "elapsed_seconds" in job:
                st.caption(
                    f"{job['requested_seconds']}-second clip · ready in {job['elapsed_seconds']:.1f} seconds"
                )
            if "seed" in job:
                st.caption(f"Seed {job['seed']}")
            st.download_button("Download original clip", job["data"], filename, "video/mp4")

            with st.expander("Record dialogue and smooth this clip"):
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
                    smooth_clip = st.checkbox("Smooth motion at 48 fps", value=True)
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
                        if number := job.get("shot_number"):
                            existing_size = sum(len(data) for key, data in film_shots.items() if key != number)
                            if existing_size + len(job["finished_data"]) <= FILM_LIMIT:
                                film_shots[number] = job["finished_data"]
                                st.session_state.pop("action_film_export", None)
                            else:
                                st.warning("This edit exceeds the film workspace. Download it separately.")
                    except (VideoGenerationError, VoiceStudioError, OSError, SubprocessError) as exc:
                        st.error(str(exc))
                if "finished_data" in job:
                    st.video(job["finished_data"], format="video/mp4")
                    st.download_button(
                        "Download edited clip", job["finished_data"], f"{Path(filename).stem}-edited.mp4", "video/mp4",
                    )
            if st.button("Add clip to custom film"):
                clip = job.get("finished_data", job["data"])
                clip_id = f"{job_backend}:{prediction_id}"
                previous = custom_shots.get(clip_id)
                used = sum(len(item["data"]) for key, item in custom_shots.items() if key != clip_id)
                if used + len(clip) > FILM_LIMIT:
                    st.error("The film workspace is full. Remove a clip before adding another.")
                else:
                    name = previous["name"] if previous else f"Clip {len(custom_shots) + 1} · {prediction_id}"
                    custom_shots[clip_id] = {"name": name, "data": clip, "seconds": job.get("requested_seconds", 4)}
                    current_order = st.session_state.get("custom_film_clip_order")
                    if previous is None and current_order is not None:
                        st.session_state["custom_film_clip_order"] = [*current_order, clip_id]
                    st.session_state.pop("custom_film_export", None)
                    st.success("Clip added to your custom film.")

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
            smooth_film = st.checkbox("Smooth motion at 48 fps", value=False, key="smooth-action-film")
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
            smooth_film = st.checkbox("Smooth custom film to 48 fps")
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
