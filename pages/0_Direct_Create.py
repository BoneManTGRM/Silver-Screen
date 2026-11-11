"""Streamlit workspace for direct image and clip generation."""

from __future__ import annotations

import secrets
from subprocess import SubprocessError

import streamlit as st
from PIL import Image

from silver_screen.ai_video import (
    ReplicateVideoClient,
    TERMINAL_STATUSES,
    VideoGenerationError,
    image_to_data_url,
)
from silver_screen.comfyui import ComfyUIClient, comfyui_ready
from silver_screen.direct_generation import (
    CLIP_MODEL,
    IMAGE_MODEL,
    read_direct_output,
    submit_direct_generation,
    verify_direct_output,
)

st.set_page_config(page_title="Direct Create | Silver-Screen", page_icon="🎨")
st.title("Direct Create")
st.caption("Send your prompt to the selected model exactly as entered.")

kind = st.radio("Output", ("Image", "Video clip"), horizontal=True)
is_image = kind == "Image"
workflow_kind = "image" if is_image else "video"
backend = st.radio(
    "Generation backend", ("ComfyUI", "Replicate"),
    index=0 if comfyui_ready(workflow_kind) else 1,
    horizontal=True,
)
use_comfyui = backend == "ComfyUI"
if use_comfyui:
    st.caption("Your ComfyUI workflow controls the model, dimensions, and audio.")
else:
    st.caption("Each generation submits a paid Replicate prediction.")

with st.form("direct_create"):
    prompt = st.text_area("Prompt", height=180, key=f"direct_prompt_{kind}")
    duration = 8
    ratio = "16:9"
    audio = True
    reference = None
    if not use_comfyui:
        ratios = ("1:1", "16:9", "9:16", "4:3", "3:4") if is_image else ("16:9", "9:16")
        ratio = st.selectbox("Aspect ratio", ratios)
    if not is_image:
        duration = st.selectbox("Clip length in seconds", (8, 6, 4))
        if use_comfyui and comfyui_ready("reference"):
            reference = st.file_uploader("Starting image (optional)", type=["png", "jpg", "jpeg", "webp"])
        if not use_comfyui:
            audio = st.checkbox("Generate audio", value=True)
    generate = st.form_submit_button("Generate", type="primary")

job = st.session_state.get("direct_create_job")
if generate:
    model = IMAGE_MODEL if is_image else CLIP_MODEL
    try:
        with st.spinner(f"Submitting to {backend}…"):
            if use_comfyui:
                image = image_to_data_url(reference)
                if reference is not None and not image:
                    raise VideoGenerationError("The starting image could not be prepared.")
                prediction = ComfyUIClient(kind=workflow_kind).create_prediction(
                    prompt, seed=secrets.randbelow(2**31), duration=duration, image=image
                )
            else:
                prediction = submit_direct_generation(
                    ReplicateVideoClient(model=model), prompt, aspect_ratio=ratio,
                    duration=duration, generate_audio=audio,
                )
        job = {"backend": backend, "model": model, "prediction": prediction}
        st.session_state["direct_create_job"] = job
    except (VideoGenerationError, OSError) as exc:
        st.error(str(exc))
        generate = False

if job:
    prediction = job["prediction"]
    prediction_id = prediction.get("id")
    job_backend = job.get("backend", "Replicate")
    job_is_image = job["model"] == IMAGE_MODEL
    if prediction_id:
        st.caption(f"{job_backend} prediction {prediction_id}")

    check = False
    if not generate and "data" not in job and prediction.get("status") not in {"failed", "canceled"}:
        check = st.button("Check existing prediction")
    if "data" not in job and (generate or check):
        def save_prediction(update):
            job["prediction"] = update

        try:
            client = (
                ComfyUIClient(kind="image" if job_is_image else "video")
                if job_backend == "ComfyUI"
                else ReplicateVideoClient(model=job["model"])
            )
            with st.spinner("Preparing your download…"):
                if check and prediction_id and prediction.get("status") not in TERMINAL_STATUSES:
                    prediction = client.get_prediction(prediction_id)
                    save_prediction(prediction)
                complete = client.wait(prediction, on_update=save_prediction)
                if isinstance(client, ComfyUIClient):
                    job["data"] = verify_direct_output(client.read_output(complete), image=job_is_image)
                else:
                    job["data"] = read_direct_output(complete, image=job_is_image)
        except (VideoGenerationError, OSError, SubprocessError, Image.DecompressionBombError) as exc:
            if job["prediction"].get("status") not in {"failed", "canceled"}:
                st.error(str(exc))
    if job["prediction"].get("status") in {"failed", "canceled"}:
        st.error(job["prediction"].get("error") or f"{job_backend} did not complete the prediction.")
    if "data" in job:
        if job_is_image:
            st.image(job["data"])
            st.download_button("Download image", job["data"], "silver-screen.png", "image/png")
        else:
            st.video(job["data"], format="video/mp4")
            st.download_button("Download clip", job["data"], "silver-screen.mp4", "video/mp4")
