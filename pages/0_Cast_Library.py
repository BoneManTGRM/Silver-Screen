"""Save reference photos and recordings for future Silver Screen projects."""

from subprocess import SubprocessError

import streamlit as st

from silver_screen.ai_video import VideoGenerationError
from silver_screen.cast_library import (
    PHOTO_SLOTS,
    VOICE_ROLES,
    cast_library_root,
    save_cast_photo,
    save_clone_sample,
    save_voice_take,
    saved_cast_photos,
    saved_clone_samples,
    saved_voice_takes,
)
from silver_screen.voice_clone import LANGUAGES, render_cloned_line, voice_clone_ready

st.set_page_config(page_title="Cast Library | Silver Screen")
st.title("Cast Library")

root = cast_library_root()
if root is None:
    st.info("The Cast Library is available on the private Silver Screen deployment.")
    st.stop()

st.caption("Save your cast once and use it in future films.")
photos, voices, clones = st.tabs(["Reference photos", "Voice takes", "Voice clones"])

with photos:
    with st.form("save-cast-photo"):
        slot = st.selectbox("Cast photo", list(PHOTO_SLOTS))
        photo = st.file_uploader("Choose a reference photo", type=["jpg", "jpeg", "png", "webp"])
        if st.form_submit_button("Save photo", type="primary") and photo is not None:
            try:
                save_cast_photo(root, slot, photo.getvalue())
                st.success(f"Saved {slot.lower()}.")
            except (VideoGenerationError, OSError) as exc:
                st.error(str(exc))
    for label, path in saved_cast_photos(root).items():
        with st.expander(label):
            st.image(path.read_bytes(), width=240)
            if st.button("Remove photo", key=f"remove-photo-{label}"):
                path.unlink(missing_ok=True)
                st.rerun()

with voices:
    st.caption("Saved takes replay your actual recorded lines. Record a new take when the script changes.")
    with st.form("save-voice-take"):
        role = st.selectbox("Speaker", list(VOICE_ROLES))
        name = st.text_input("Take name", max_chars=48)
        recorded = st.audio_input("Record a take")
        uploaded = st.file_uploader("Or upload a recording", type=["wav", "m4a", "mp3", "aac", "ogg", "webm"])
        take = recorded if recorded is not None else uploaded
        if st.form_submit_button("Save take", type="primary"):
            if take is None:
                st.error("Record or upload a take first.")
            else:
                try:
                    save_voice_take(root, role, name, take.getvalue())
                    st.success(f"Saved {name.strip()}.")
                except (VideoGenerationError, OSError, SubprocessError) as exc:
                    st.error(str(exc))
    for role_label in VOICE_ROLES:
        takes = saved_voice_takes(root, role_label)
        if takes:
            with st.expander(role_label):
                name = st.selectbox("Saved take", list(takes), key=f"saved-{role_label}")
                path = takes[name]
                st.audio(path.read_bytes(), format="audio/wav")
                if st.button("Remove take", key=f"remove-{role_label}"):
                    path.unlink(missing_ok=True)
                    st.rerun()

with clones:
    st.caption("Record each speaker separately. Aim for 10 to 20 seconds of clear speech in a quiet room.")
    with st.form("enroll-cloned-voice", clear_on_submit=True):
        role = st.selectbox("Speaker", list(VOICE_ROLES))
        sample = st.audio_input("Record the speaker's sample")
        uploaded = st.file_uploader("Or upload a sample", type=["wav", "m4a", "mp3", "aac", "ogg", "webm"])
        approved = st.checkbox("The speaker recorded or provided this sample and agreed to clone their voice for our films.")
        if st.form_submit_button("Save voice sample", type="primary"):
            sample = sample if sample is not None else uploaded
            if sample is None or not approved:
                st.error("Add the speaker's sample and confirm their agreement.")
            else:
                try:
                    save_clone_sample(root, role, sample.getvalue())
                    st.session_state.pop("cast-clone-preview", None)
                    st.success(f"Saved the sample for {role.lower()}.")
                except (VideoGenerationError, OSError, SubprocessError) as exc:
                    st.error(str(exc))
    samples = saved_clone_samples(root)
    for label, path in samples.items():
        with st.expander(label):
            st.audio(path.read_bytes(), format="audio/wav")
            if st.button("Remove voice sample", key=f"remove-clone-{label}"):
                path.unlink(missing_ok=True)
                st.session_state.pop("cast-clone-preview", None)
                st.rerun()
    if samples and voice_clone_ready():
        with st.form("preview-cloned-voice"):
            role = st.selectbox("Voice to preview", list(samples))
            language = st.selectbox("Language", list(LANGUAGES))
            line = st.text_area("New line to speak", max_chars=300)
            if st.form_submit_button("Generate voice preview"):
                st.session_state.pop("cast-clone-preview", None)
                try:
                    with st.spinner("Generating the voice preview…"):
                        st.session_state["cast-clone-preview"] = render_cloned_line(role, language, line)
                except VideoGenerationError as exc:
                    st.error(str(exc))
        if preview := st.session_state.get("cast-clone-preview"):
            st.audio(preview, format="audio/wav")
            st.download_button("Download voice preview", preview, "voice-preview.wav", "audio/wav")
