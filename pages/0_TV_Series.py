"""Write a season and produce its episodes in Silver Screen."""

from io import BytesIO

import streamlit as st

from silver_screen.cast_library import cast_library_root, saved_cast_photos
from silver_screen.comfyui import comfyui_ready, video_backend, video_backend_ready
from silver_screen.modal_renderer import screenplay_writer_configured
from silver_screen.runtime import slugify
from silver_screen.screenplay_writer import EPISODE_LENGTHS, IDEA_LIMIT, SCREENPLAY_LENGTHS, ScreenplayStatusUnavailable
from silver_screen.tv_series import (
    SEASON_COUNTS, STOPPED_RENDERS, check_series_script, create_series, episode_status, episode_subtitles,
    set_series_photo, start_episode_render, start_episode_script, studio_series_store,
)
from silver_screen.voice_providers import provider_capabilities


st.title("TV Series")
store = studio_series_store()
writer_ready = screenplay_writer_configured()


def create_form(open_form: bool) -> None:
    with st.expander("Create a series", expanded=open_form):
        with st.form("new-season"):
            title = st.text_input("Series title (optional)", max_chars=100)
            idea = st.text_area(
                "Series premise", max_chars=IDEA_LIMIT, height=140,
                placeholder="Two siblings inherit a radio station that receives tomorrow’s news.",
            )
            left, right = st.columns(2)
            count = left.selectbox("Episodes in season 1", list(SEASON_COUNTS), index=1)
            seconds = right.selectbox(
                "Episode length", EPISODE_LENGTHS, index=1,
                format_func=lambda value: "2 minutes" if value == 120 else f"{value} seconds",
            )
            submitted = st.form_submit_button("Write season", type="primary", disabled=not writer_ready)
        if not writer_ready:
            st.info("Connect the Modal screenplay writer to draft a season.")
        if submitted:
            series = create_series(store, idea, count, seconds, title=title)
            st.query_params["series"] = series["seriesId"]
            st.rerun()


@st.fragment(run_every=5)
def writer_progress(series_id: str, number: int | None = None) -> None:
    try:
        if check_series_script(store, series_id, number):
            st.rerun()
        st.info("Writing the season bible and outlines…" if number is None else "Writing the episode…")
    except ScreenplayStatusUnavailable as exc:
        st.warning(str(exc))
    except Exception:
        st.warning("The saved writer job could not be checked. The page will check again.")


@st.fragment(run_every=5)
def video_progress(episode: dict) -> None:
    status = episode_status(store, episode)
    if status["status"] != "rendering":
        st.rerun()
    metrics = status.get("metrics") or {}
    done, total = metrics.get("verifiedShots", 0), metrics.get("plannedShots", 0)
    if total:
        st.progress(done / total, text=f"{done} of {total} shots finished" if done < total else "Assembling and saving the episode")
        st.caption(f"GPU requests used · {metrics.get('providerCalls', 0)}")
    else:
        st.info("Preparing the episode render…")


def show_cast(series_id: str, season: dict) -> None:
    with st.expander("Series bible and recurring cast"):
        st.markdown("**World**")
        st.write(season["world"])
        st.markdown("**Season arc**")
        st.write(season["arc"])
        for role in ("lead", "support"):
            actor = season["cast"][role]
            st.markdown(f"**{actor['name']}** · {actor['role']}")
            st.write(actor["appearance"])
        with st.form("series-lead-photo"):
            st.caption("Save a lead photo to share across future episode renders.")
            photo = st.file_uploader("Lead photo (optional)", type=["png", "jpg", "jpeg", "webp"])
            root = cast_library_root()
            saved = saved_cast_photos(root).get("Your photo") if root else None
            use_saved = st.checkbox("Use my saved lead photo") if saved else False
            allowed = st.checkbox("Everyone shown in the photo agreed to this series and its audience.")
            new_photo = st.form_submit_button("Save series lead photo", disabled=not allowed)
        if season.get("leadReference"):
            st.caption("A shared lead photo is saved for this series.")
        if new_photo:
            set_series_photo(store, series_id, photo if photo is not None else saved if use_saved else None)
            st.rerun()


def finished_episode(series_id: str, episode: dict) -> None:
    number = episode["number"]
    path = store.episode_path(series_id, number)
    draft = episode["draft"]
    subtitles = episode_subtitles(draft)
    player = {"subtitles": BytesIO(subtitles.encode())} if subtitles else {}
    st.video(str(path), **player)
    name = slugify(draft["title"])
    st.download_button("Download episode", path.read_bytes(), file_name=f"{name}.mp4", mime="video/mp4")
    if subtitles:
        st.download_button("Download subtitles", subtitles, file_name=f"{name}.vtt", mime="text/vtt")
        if not episode["render"].get("audioFinished"):
            st.caption("Dialogue is available as subtitles.")


def render_controls(series_id: str, episode: dict, season: dict, status: dict) -> None:
    number, draft = episode["number"], episode["draft"]
    stage = status["status"]
    render = episode.get("render") or {}
    required = draft["seconds"] // SCREENPLAY_LENGTHS[draft["seconds"]]
    ready = video_backend_ready()
    if stage == "rendering":
        video_progress(episode)
        return
    if stage == "ready":
        finished_episode(series_id, episode)
        return
    if error := status.get("error") or render.get("error"):
        st.warning(error)
    restart = stage == "checkpoint_missing"
    if stage == "checkpoint":
        metrics = status.get("metrics") or {}
        st.caption(f"Saved checkpoint · {metrics.get('verifiedShots', 0)}/{required} shots · {metrics.get('providerCalls', 0)}/{render['maxCalls']} requests used")
        restart = status.get("stopReason") in STOPPED_RENDERS
        if not restart:
            if st.button("Resume episode", type="primary", disabled=not ready and status.get("runStatus") != "complete"):
                start_episode_render(store, series_id, number, max_calls=render["maxCalls"])
                st.rerun()
            return
        st.warning("This episode render stopped. Its completed shots are saved in Autonomous Studio.")
    if restart:
        st.caption("A fresh render uses a new GPU request budget.")
    with st.form(f"episode-render-{number}"):
        calls = st.number_input("Maximum GPU requests", min_value=required, max_value=3 * required,
                                value=required + 2)
        speech_available = provider_capabilities().get("openai")
        speak = st.checkbox("Add spoken dialogue", value=False) if speech_available else False
        if not speech_available:
            st.caption("Scripted dialogue will be available as subtitles.")
        if not ready:
            st.info(f"Configure the {video_backend()} video backend to render episodes.")
        reference_unavailable = (season.get("leadReference") and video_backend() == "comfyui"
                                 and not comfyui_ready("reference"))
        if reference_unavailable:
            st.info("Connect a reference workflow to use the saved lead photo.")
        start = st.form_submit_button(
            "Start a fresh render" if restart else "Render this episode",
            type="primary", disabled=not ready or bool(reference_unavailable),
        )
    if start:
        start_episode_render(store, series_id, number, max_calls=int(calls),
                             voice_provider="openai" if speak else "", restart=restart)
        st.rerun()


def show_season(series: dict) -> None:
    series_id = series["seriesId"]
    st.header(series.get("title") or "New series")
    st.write(series["premise"])
    if error := series.get("writerError"):
        st.warning(error)
    if series.get("writerJob"):
        writer_progress(series_id)
        return
    if not series.get("episodes"):
        return
    show_cast(series_id, series)
    status_by_episode = {ep["number"]: episode_status(store, ep) for ep in series["episodes"]}
    labels = {"planned": "Outline", "draft": "Draft", "rendering": "Rendering",
              "checkpoint": "Checkpoint", "checkpoint_missing": "Checkpoint unavailable", "ready": "Ready"}
    with st.expander("Season 1 episode guide", expanded=True):
        for ep in series["episodes"]:
            status = status_by_episode[ep["number"]]["status"]
            st.markdown(f"**{ep['number']:02d} · {ep['title']}** · {labels[status]}")
            st.write(ep["goal"])
    number = st.selectbox(
        "Episode", list(range(1, len(series["episodes"]) + 1)),
        format_func=lambda value: f"Episode {value} · {series['episodes'][value - 1]['title']}",
        key=f"{series_id}-episode",
    )
    episode = series["episodes"][number - 1]
    st.subheader(episode["title"])
    st.write(episode["goal"])
    with st.expander("Episode outcome and next episode hook"):
        st.write(episode["outcome"])
        st.write(episode["hook"])
    if episode.get("writerError"):
        st.warning(episode["writerError"])
    if episode.get("writerJob"):
        writer_progress(series_id, number)
        return
    draft = episode.get("draft")
    if not episode.get("render") and st.button(
        "Write another draft" if draft else "Write episode script", type="primary" if not draft else "secondary",
        disabled=not writer_ready, key=f"{series_id}-write-{number}",
    ):
        start_episode_script(store, series_id, number, rewrite=bool(draft))
        st.rerun()
    if draft:
        with st.expander("Episode screenplay", expanded=not episode.get("render")):
            st.text_area("Script", draft["authoredScript"], height=280, disabled=True,
                         key=f"{series_id}-{number}-{draft['seed']}")
            st.download_button(
                "Download script", draft["authoredScript"], file_name=f"{slugify(draft['title'])}.txt",
                mime="text/plain",
            )
        render_controls(series_id, episode, series, status_by_episode[number])


def main() -> None:
    ids = store.list()
    create_form(not ids)
    if not ids:
        return
    requested = st.query_params.get("series")
    titles = st.session_state.setdefault("tv-series-titles", {})
    for series_id in ids:
        if not titles.get(series_id):
            series = store.read(series_id)
            if title := series.get("title"):
                titles[series_id] = title
    if requested and requested not in ids:
        st.info("This saved series could not be found.")
    selected = st.selectbox(
        "Saved series", ids, index=ids.index(requested) if requested in ids else 0,
        format_func=lambda value: titles.get(value, "New series"), key=f"tv-series-{requested or 'home'}",
    )
    if selected != requested:
        st.query_params["series"] = selected
    show_season(store.read(selected))


try:
    main()
except (ValueError, OSError) as exc:
    st.error(str(exc))
except Exception:
    st.error("Silver Screen could not load or save this series. Try opening it again.")
