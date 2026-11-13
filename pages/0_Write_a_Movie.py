"""Turn a short brief into a production-ready movie or ad."""

import streamlit as st

from silver_screen.cast_library import cast_library_root, saved_cast_photos
from silver_screen.modal_renderer import screenplay_writer_configured
from silver_screen.runtime import slugify
from silver_screen.screenplay_writer import (
    IDEA_LIMIT,
    LEAD_LIMIT,
    SCREENPLAY_LENGTHS,
    ScreenplayError,
    ScreenplayStatusUnavailable,
    check_screenplay,
    submit_screenplay,
)

st.title("Silver Screen")
st.caption("Write a movie or advertisement from one idea. Review the script and shots before rendering.")

draft = st.session_state.get("screenplay-draft")
job = st.session_state.get("screenplay-writer-job")
ready = screenplay_writer_configured()
kind = st.radio("What are you making?", ["Movie", "Advertisement"], horizontal=True)
is_ad = kind == "Advertisement"
with st.form(f"write-{kind}"):
    example = (
        "A 20-second ad for a reusable travel mug. Show the spillproof lid and end with Shop now."
        if is_ad else "Make me the action hero of a futuristic action movie."
    )
    idea = st.text_area("Product or campaign brief" if is_ad else "Your movie idea", placeholder=example,
                        max_chars=IDEA_LIMIT, height=118)
    seconds = st.radio("Length", list(SCREENPLAY_LENGTHS), horizontal=True, format_func=lambda value: f"{value} seconds")
    lead = st.text_input("Presenter name (optional)" if is_ad else "Lead name (optional)", max_chars=LEAD_LIMIT)
    end_card = None
    if is_ad:
        brand = st.text_input("Product or brand name (optional)", max_chars=36)
        cta = st.text_input("Call to action (optional)", max_chars=64, placeholder="Shop now")
        end_card = {"brand": brand, "cta": cta} if brand.strip() or cta.strip() else None
    generate = st.form_submit_button(
        "Write another version" if draft else "Write script", type="primary",
        disabled=not ready or bool(job),
    )

if not ready:
    st.info("Connect the Modal screenplay writer to generate drafts.")
if generate and not job:
    try:
        st.session_state["screenplay-writer-job"] = submit_screenplay(
            idea, lead, seconds, kind="ad" if is_ad else "film", end_card=end_card,
        )
        st.rerun()
    except ScreenplayError as exc:
        st.error(str(exc))

if job:
    @st.fragment(run_every=5)
    def show_writer_status():
        try:
            result = check_screenplay(job)
        except ScreenplayStatusUnavailable as exc:
            st.warning(str(exc))
            return
        except ScreenplayError as exc:
            st.session_state.pop("screenplay-writer-job", None)
            st.session_state["screenplay-writer-error"] = str(exc)
            st.rerun()
        if result is None:
            st.info("Writing your script. This can take a few minutes.")
            return
        st.session_state["screenplay-draft"] = result
        st.session_state.pop("screenplay-writer-job", None)
        st.rerun()

    show_writer_status()

if error := st.session_state.pop("screenplay-writer-error", None):
    st.error(error)

if draft:
    st.header(draft["title"])
    st.text(draft["premise"])
    if product := draft.get("adProduct"):
        st.text(" · ".join(part for part in (product["name"], product["appearance"]) if part))
    clip_seconds = SCREENPLAY_LENGTHS[draft["seconds"]]
    st.caption(f"{draft['seconds']}-second draft · 5 shots × {clip_seconds} seconds")
    st.text_area("Screenplay", draft["authoredScript"], height=450, disabled=True)
    if card := draft.get("adEndCard"):
        st.caption("End card · final 3 seconds")
        st.text("\n".join(value.strip() for value in (card["brand"], card["cta"]) if value.strip()))
    with st.expander("Timed shot list" if draft["kind"] == "ad" else "Timed shot list and cast"):
        for order, action in draft["shotDirection"]["shotPromptOverrides"].items():
            start = (int(order) - 1) * clip_seconds
            st.text(f"{start:02d}–{start + clip_seconds:02d}s  {action}")
        for member in draft["cast"]:
            st.text(f"{member['name']} · {member['role']}. {member['description']}")

    root = cast_library_root()
    can_star = draft["kind"] == "film" and root and "Your photo" in saved_cast_photos(root)
    use_saved_photo = st.checkbox("Use my saved lead photo", key="writer-use-lead-photo") if can_star else False
    if draft["kind"] == "film" and not can_star:
        st.page_link("pages/0_Cast_Library.py", label="Save a photo to star in future films")
    columns = st.columns(2)
    columns[0].download_button("Download script", f"{draft['title']}\n\n{draft['authoredScript']}\n",
                               file_name=f"{slugify(draft['title'])}.txt", mime="text/plain")
    if columns[1].button("Plan this draft", type="primary", disabled=bool(job)):
        if st.session_state.get("screenplay-production-started") != draft["seed"]:
            st.session_state.pop("autonomous_plan", None)
            st.session_state.pop("autonomous_result", None)
        st.session_state["screenplay-production"] = draft
        st.session_state["screenplay-production-photo"] = use_saved_photo
        st.switch_page("pages/9_Autonomous_Studio.py")

with st.expander("Studio tools"):
    st.page_link("pages/0_Direct_Create.py", label="Direct Create · render one clip or image")
    st.page_link("pages/0_Cast_Library.py", label="Cast Library · save a photo or record a voice take")
    st.page_link("pages/9_Autonomous_Studio.py", label="Autonomous Studio · plan, render, or continue a film")
    st.page_link("pages/7_Shot_Director.py", label="Shot Director · inspect and edit individual prompts")
    st.page_link("pages/2_Professional_Script_Sync.py", label="Professional Script Sync · add dialogue and captions")
