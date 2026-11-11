"""Streamlit workspace for direct image and clip generation."""

from __future__ import annotations

from subprocess import SubprocessError

import streamlit as st
from PIL import Image

from silver_screen.ai_video import ReplicateVideoClient, TERMINAL_STATUSES, VideoGenerationError
from silver_screen.direct_generation import (
    CLIP_MODEL,
    IMAGE_MODEL,
    read_direct_output,
    submit_direct_generation,
)

st.set_page_config(page_title="Direct Create | Silver-Screen", page_icon="🎨")
st.title("Direct Create")
st.caption("Send your prompt to the model exactly as entered. Each new generation uses a paid Replicate prediction.")

kind = st.radio("Output", ("Image", "Video clip"), horizontal=True)
is_image = kind == "Image"
with st.form("direct_create"):
    prompt = st.text_area("Prompt", height=180, key=f"direct_prompt_{kind}")
    ratios = ("1:1", "16:9", "9:16", "4:3", "3:4") if is_image else ("16:9", "9:16")
    ratio = st.selectbox("Aspect ratio", ratios)
    duration = 8
    audio = True
    if not is_image:
        duration = st.selectbox("Clip length in seconds", (8, 6, 4))
        audio = st.checkbox("Generate audio", value=True)
    generate = st.form_submit_button("Generate", type="primary")

job = st.session_state.get("direct_create_job")
if generate:
    model = IMAGE_MODEL if is_image else CLIP_MODEL
    try:
        client = ReplicateVideoClient(model=model)
        with st.spinner("Submitting to Replicate…"):
            prediction = submit_direct_generation(
                client, prompt, aspect_ratio=ratio, duration=duration, generate_audio=audio
            )
        job = {"model": model, "prediction": prediction}
        st.session_state["direct_create_job"] = job
    except (VideoGenerationError, OSError) as exc:
        st.error(str(exc))
        generate = False

if job:
    prediction = job["prediction"]
    prediction_id = prediction.get("id")
    if prediction_id:
        st.caption(f"Replicate prediction {prediction_id}")

    check = False
    if not generate and "data" not in job and prediction.get("status") not in {"failed", "canceled"}:
        check = st.button("Check existing prediction")
    if "data" not in job and (generate or check):
        def save_prediction(update):
            job["prediction"] = update

        try:
            client = ReplicateVideoClient(model=job["model"])
            with st.spinner("Preparing your download…"):
                if check and prediction_id and prediction.get("status") not in TERMINAL_STATUSES:
                    prediction = client.get_prediction(prediction_id)
                    save_prediction(prediction)
                complete = client.wait(prediction, on_update=save_prediction)
                job["data"] = read_direct_output(complete, image=job["model"] == IMAGE_MODEL)
        except (VideoGenerationError, OSError, SubprocessError, Image.DecompressionBombError) as exc:
            if job["prediction"].get("status") not in {"failed", "canceled"}:
                st.error(str(exc))
    if job["prediction"].get("status") in {"failed", "canceled"}:
        st.error(job["prediction"].get("error") or "Replicate did not complete the prediction.")
    if "data" in job:
        if job["model"] == IMAGE_MODEL:
            st.image(job["data"])
            st.download_button("Download image", job["data"], "silver-screen.png", "image/png")
        else:
            st.video(job["data"], format="video/mp4")
            st.download_button("Download clip", job["data"], "silver-screen.mp4", "video/mp4")
