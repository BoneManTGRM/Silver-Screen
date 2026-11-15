"""Turn a short brief into a movie, advertisement, or trailer."""

import streamlit as st

from silver_screen.cast_library import cast_library_root, saved_cast_photos
from silver_screen.modal_renderer import screenplay_writer_configured
from silver_screen.runtime import slugify
from silver_screen.screenplay_writer import (
    IDEA_LIMIT,
    LEAD_LIMIT,
    PRODUCTION_LABELS,
    SCREENPLAY_LENGTHS,
    TRAILER_KINDS,
    ScreenplayError,
    ScreenplayStatusUnavailable,
    check_screenplay,
    screenplay_lengths,
    submit_screenplay,
)

st.title("Silver Screen")
st.caption("Review a script and shot list before rendering a movie, advertisement, or trailer.")

draft = st.session_state.get("screenplay-draft")
job = st.session_state.get("screenplay-writer-job")
ready = screenplay_writer_configured()
kind = st.radio("What are you making?", list(PRODUCTION_LABELS), format_func=PRODUCTION_LABELS.get, horizontal=True)
is_ad = kind == "ad"
with st.form(f"write-{kind}"):
    label, example = {
        "ad": ("Product or campaign brief", "Show a reusable travel mug in use on a morning commute. End with Shop now."),
        "film": ("Your movie idea", "Make me the action hero of a futuristic action movie."),
        "movie_trailer": ("Movie title and story", "A pilot finds a city hidden inside a storm. Tease what she risks by trying to save it."),
        "series_trailer": ("Series title and story", "Two siblings inherit a radio station that receives tomorrow’s news. Hint at mysteries across the season."),
    }[kind]
    idea = st.text_area(label, placeholder=example, max_chars=IDEA_LIMIT, height=118)
    seconds = st.radio("Length", screenplay_lengths(kind), horizontal=True, format_func=lambda value: f"{value} seconds")
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
            idea, lead, seconds, kind=kind, end_card=end_card,
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
    draft_kind = draft["kind"]
    st.header(draft["title"])
    st.text(draft["premise"])
    if product := draft.get("adProduct"):
        st.text(" · ".join(part for part in (product["name"], product["appearance"]) if part))
    clip_seconds = SCREENPLAY_LENGTHS[draft["seconds"]]
    st.caption(f"{PRODUCTION_LABELS[draft_kind]} · {draft['seconds']} seconds · {draft['seconds'] // clip_seconds} shots")
    st.text_area("Screenplay", draft["authoredScript"], height=450, disabled=True)
    if card := draft.get("adEndCard"):
        st.caption("End card · final 3 seconds")
        st.text("\n".join(value.strip() for value in (card["brand"], card["cta"]) if value.strip()))
    elif draft_kind in TRAILER_KINDS:
        st.caption(f"Final title · {draft['title']}")
    with st.expander("Timed shot list" if draft_kind == "ad" else "Timed shot list and cast"):
        for order, action in draft["shotDirection"]["shotPromptOverrides"].items():
            start = (int(order) - 1) * clip_seconds
            st.text(f"{start:02d}–{start + clip_seconds:02d}s  {action}")
        for member in draft["cast"]:
            st.text(f"{member['name']} · {member['role']}. {member['description']}")

    root = cast_library_root()
    can_star = draft_kind != "ad" and root and "Your photo" in saved_cast_photos(root)
    use_saved_photo = st.checkbox("Use my saved lead photo", key="writer-use-lead-photo") if can_star else False
    if draft_kind != "ad" and not can_star:
        st.page_link("pages/0_Cast_Library.py", label="Save a lead photo in Cast Library")
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
