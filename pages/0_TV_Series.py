"""Write a season and produce its episodes in Silver Screen."""

import base64
from io import BytesIO

import streamlit as st

from silver_screen.cast_library import cast_library_root, saved_cast_photos
from silver_screen.comfyui import comfyui_ready, video_backend, video_backend_ready
from silver_screen.modal_renderer import screenplay_writer_configured
from silver_screen.runtime import slugify
from silver_screen.science import GENRES, TONES
from silver_screen.screenplay_writer import EPISODE_LENGTHS, IDEA_LIMIT, SCREENPLAY_LENGTHS, ScreenplayStatusUnavailable
from silver_screen.tv_series import (
    CAST_LIMITS, GPU_BUDGET_STOP, OUTLINE_LIMITS, SEASON_COUNTS, STOPPED_RENDERS, VISUAL_DIRECTION_LIMIT,
    SeriesError, check_series_script, create_series, cut_season_trailer, episode_shots, episode_status,
    episode_subtitles, next_season_id, retry_season_writer, save_episode_outline, save_episode_shot,
    save_season_bible, set_series_photo, start_episode_render, start_episode_script, start_next_season,
    studio_series_store,
)
from silver_screen.tv_series_trailer import TRAILER_FORMATS, available_trailer_lengths
from silver_screen.voice_providers import provider_capabilities


st.title("TV Series")
store = studio_series_store()
writer_ready = screenplay_writer_configured()


def open_season(series_id: str) -> None:
    st.query_params["series"] = series_id
    st.rerun()


def length_label(seconds: int) -> str:
    return {60: "1 minute", 120: "2 minutes"}.get(seconds, f"{seconds} seconds")


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
                format_func=length_label,
            )
            submitted = st.form_submit_button("Write season", type="primary", disabled=not writer_ready)
        if not writer_ready:
            st.info("Connect the Modal screenplay writer to draft a season.")
        if submitted:
            series = create_series(store, idea, count, seconds, title=title)
            open_season(series["seriesId"])


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


def show_bible(season: dict) -> None:
    series_id = season["seriesId"]
    st.subheader("Series bible")
    st.caption("These settings guide new episode drafts and the next season.")
    if finale := season.get("previousFinale"):
        with st.expander("Previously on this series"):
            st.write(finale["outcome"])
            st.write(finale["hook"])
            if finale["scripted ending"]:
                st.write(finale["scripted ending"])
    with st.form(f"{series_id}-bible"):
        world = st.text_area("World and setting", season["world"], max_chars=260)
        arc = st.text_area("Season arc", season["arc"], max_chars=340)
        left, right = st.columns(2)
        genre = left.selectbox("Genre", list(GENRES), index=list(GENRES).index(season["genre"]))
        tone = right.selectbox("Tone", list(TONES), index=list(TONES).index(season["tone"]))
        visual = st.text_area("Visual direction", season.get("visualDirection", ""), max_chars=VISUAL_DIRECTION_LIMIT,
                              placeholder="Warm practical light, handheld close-ups, and the same red raincoat in every harbor scene.")
        cast = {}
        for column, role, label in zip(st.columns(2), ("lead", "support"), ("Lead", "Supporting character")):
            actor = season["cast"][role]
            with column:
                st.markdown(f"**{label}**")
                cast[role] = {
                    "name": st.text_input(f"{label} name", actor["name"], max_chars=CAST_LIMITS["name"]),
                    "role": st.text_input(f"{label} role", actor["role"], max_chars=CAST_LIMITS["role"]),
                    "appearance": st.text_area(f"{label} appearance", actor["appearance"], max_chars=CAST_LIMITS["appearance"]),
                }
        save = st.form_submit_button("Save series bible", type="primary")
    if save:
        save_season_bible(store, series_id, world=world, arc=arc, genre=genre, tone=tone, cast=cast, visual_direction=visual)
        st.success("Series bible saved.")

    st.subheader("Lead reference")
    if data_url := season.get("leadReference"):
        st.image(base64.b64decode(data_url.split(",", 1)[1]), width=160)
    with st.form(f"{series_id}-lead-photo"):
        photo = st.file_uploader("Lead photo (optional)", type=["png", "jpg", "jpeg", "webp"])
        root = cast_library_root()
        saved = saved_cast_photos(root).get("Your photo") if root else None
        use_saved = st.checkbox("Use my saved lead photo") if saved else False
        allowed = st.checkbox("Everyone shown in the photo agreed to this series and its audience.")
        new_photo = st.form_submit_button("Save series lead photo")
    if new_photo and not allowed:
        st.warning("Confirm permission to use the lead photo before saving.")
    elif new_photo:
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


def resume_with_budget(series_id: str, episode: dict, *, used: int, remaining: int, required: int, ready: bool) -> None:
    number = episode["number"]
    upper = 3 * required
    lower = max(required, used + 1)
    approved = episode["render"]["maxCalls"]
    recommended = min(upper, max(lower, approved, used + remaining))
    st.info("The GPU request limit paused this render. Increase the total limit to continue from the saved shots.")
    with st.form(f"{series_id}-episode-budget-{number}"):
        calls = st.number_input("Total GPU request limit", min_value=lower, max_value=upper, value=recommended)
        resume = st.form_submit_button("Resume saved render", type="primary", disabled=not ready)
    if resume:
        start_episode_render(store, series_id, number, max_calls=int(calls))
        st.rerun()


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
        done = metrics.get("verifiedShots", 0)
        used = metrics.get("providerCalls", 0)
        st.caption(f"Saved checkpoint · {done}/{required} shots · {used}/{render['maxCalls']} requests used")
        reason = status.get("stopReason")
        if reason == GPU_BUDGET_STOP and used < 3 * required:
            resume_with_budget(series_id, episode, used=used, remaining=required - done, required=required, ready=ready)
            return
        restart = reason in STOPPED_RENDERS
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


def edit_outline(series_id: str, episode: dict) -> None:
    number = episode["number"]
    with st.expander("Edit episode outline"):
        with st.form(f"{series_id}-outline-{number}"):
            outline = {"title": st.text_input("Episode title", episode["title"], max_chars=OUTLINE_LIMITS["title"])}
            for field, label in (("goal", "On-screen goal"), ("outcome", "Outcome"), ("hook", "Ending hook")):
                outline[field] = st.text_area(label, episode[field], max_chars=OUTLINE_LIMITS[field])
            if episode.get("draft"):
                st.caption("Write another draft to apply these outline changes to the screenplay.")
            save = st.form_submit_button("Save episode outline")
        if save:
            try:
                save_episode_outline(store, series_id, number, outline)
            except SeriesError as exc:
                st.error(str(exc))
            else:
                st.rerun()


def edit_shots(series_id: str, episode: dict) -> None:
    number, draft = episode["number"], episode["draft"]
    shots = episode_shots(draft)
    revision = str(draft.get("revision", draft["seed"]))
    with st.expander("Edit shots and dialogue"):
        shot_number = st.selectbox("Shot to edit", list(range(1, len(shots) + 1)),
                                   key=f"{series_id}-shot-choice-{number}", format_func=lambda value: f"Shot {value:02d}")
        shot = shots[shot_number - 1]
        names = {"none": "No dialogue", **{role: actor["name"] for role, actor in zip(("lead", "support"), draft["cast"])}}
        with st.form(f"{series_id}-{number}-{shot_number}-{revision}"):
            updated = {
                "heading": st.text_input("Scene heading", shot["heading"], max_chars=140),
                "action": st.text_area("Visible action", shot["action"], max_chars=520),
                "speaker": st.selectbox("Speaker", list(names), index=list(names).index(shot["speaker"]), format_func=names.get),
                "line": st.text_input("Dialogue", shot["line"], max_chars=180),
            }
            save = st.form_submit_button("Save shot")
        if save:
            try:
                save_episode_shot(store, series_id, number, shot_number, updated, expected_revision=revision)
            except SeriesError as exc:
                st.error(str(exc))
            else:
                st.rerun()


def show_episodes(series: dict, status_by_episode: dict[int, dict]) -> None:
    series_id = series["seriesId"]
    labels = {"planned": "Outline", "draft": "Draft", "rendering": "Rendering",
              "checkpoint": "Checkpoint", "checkpoint_missing": "Checkpoint unavailable", "ready": "Ready"}
    with st.expander("Episode guide", expanded=True):
        for ep in series["episodes"]:
            status = status_by_episode[ep["number"]]["status"]
            label = "Writing" if ep.get("writerJob") else labels[status]
            st.markdown(f"**{ep['number']:02d} · {ep['title']}** · {label}")
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
    if not episode.get("render"):
        edit_outline(series_id, episode)
    draft = episode.get("draft")
    if not episode.get("render") and st.button(
        "Write another draft" if draft else "Write episode script", type="primary" if not draft else "secondary",
        disabled=not writer_ready, key=f"{series_id}-write-{number}",
    ):
        start_episode_script(store, series_id, number, rewrite=bool(draft))
        st.rerun()
    if draft:
        if not episode.get("render"):
            edit_shots(series_id, episode)
        with st.expander("Episode screenplay", expanded=not episode.get("render")):
            st.text_area("Script", draft["authoredScript"], height=280, disabled=True,
                         key=f"{series_id}-{number}-{draft.get('revision', draft['seed'])}")
            st.download_button(
                "Download script", draft["authoredScript"], file_name=f"{slugify(draft['title'])}.txt",
                mime="text/plain",
            )
        render_controls(series_id, episode, series, status_by_episode[number])


def show_trailers(season: dict) -> None:
    series_id = season["seriesId"]
    completed = {ep["number"]: ep["title"] for ep in season["episodes"] if (ep.get("render") or {}).get("archived")}
    st.subheader("Trailer editor")
    if not completed:
        st.info("Finish an episode to start cutting your trailer.")
        return
    sources = st.multiselect("Finished episodes", list(completed), default=list(completed),
                             format_func=lambda number: f"Episode {number} · {completed[number]}",
                             key=f"{series_id}-trailer-episodes")
    shot_seconds = SCREENPLAY_LENGTHS[season["seconds"]]
    lengths = available_trailer_lengths([season["episodes"][number - 1]["draft"]["seconds"] for number in sources],
                                        shot_seconds)
    with st.form(f"{series_id}-trailer"):
        left, right = st.columns(2)
        seconds = left.selectbox("Trailer length", lengths, index=len(lengths) - 1 if lengths else None,
                                 format_func=length_label, disabled=not lengths)
        frame_format = right.selectbox("Trailer format", list(TRAILER_FORMATS))
        tagline = st.text_input("End card tagline (optional)", max_chars=80)
        cut = st.form_submit_button("Cut trailer", type="primary", disabled=not lengths)
    selected = st.session_state.get(f"{series_id}-latest-trailer")
    if cut:
        try:
            with st.spinner("Cutting your trailer from finished episodes…"):
                trailer = cut_season_trailer(store, series_id, sources, seconds=seconds, frame_format=frame_format, tagline=tagline)
        except SeriesError as exc:
            st.error(str(exc))
        else:
            selected = trailer["id"]
            st.session_state[f"{series_id}-latest-trailer"] = selected
            season = store.read(series_id)
    trailers = season.get("trailers") or {}
    if trailers:
        ids = sorted(trailers, key=lambda key: trailers[key]["createdAt"], reverse=True)
        def label(key: str) -> str:
            cut = trailers[key]
            numbers = ", ".join(str(number) for number in cut["episodeNumbers"])
            title = f" · {cut['tagline']}" if cut["tagline"] else ""
            return f"{cut['seconds']}s · {cut['frameFormat']} · Episodes {numbers}{title}"

        chosen = st.selectbox("Saved trailers", ids, index=ids.index(selected) if selected in trailers else 0,
                              format_func=label, key=f"{series_id}-saved-trailer-{selected or 'latest'}")
        path = store.trailer_path(series_id, chosen)
        st.video(str(path))
        trailer = trailers[chosen]
        name = slugify(f"{season['title']}-s{season.get('seasonNumber', 1)}-{trailer['frameFormat']}-trailer")
        st.download_button("Download trailer", path.read_bytes(), file_name=f"{name}.mp4", mime="video/mp4")


def show_next_season(season: dict, saved_ids: list[str]) -> None:
    series_id = season["seriesId"]
    number = season.get("seasonNumber", 1)
    successor = next_season_id(series_id)
    st.subheader(f"Season {number + 1}")
    if successor in saved_ids:
        if st.button(f"Open season {number + 1}", type="primary"):
            open_season(successor)
    else:
        st.caption("The new season starts from this season’s finale and inherits its cast and visual direction.")
        with st.form(f"{series_id}-next-season"):
            idea = st.text_area("Next season premise", max_chars=IDEA_LIMIT, height=140)
            left, right = st.columns(2)
            count = left.selectbox("Episodes in the next season", list(SEASON_COUNTS), index=season["episodeCount"] - 2)
            seconds = right.selectbox("Next season episode length", EPISODE_LENGTHS, index=EPISODE_LENGTHS.index(season["seconds"]),
                                      format_func=length_label)
            submit = st.form_submit_button("Write next season", type="primary", disabled=not writer_ready)
        if submit:
            sequel = start_next_season(store, series_id, idea, count, seconds)
            open_season(sequel["seriesId"])
    if previous := season.get("previousSeasonId"):
        if st.button(f"Open season {number - 1}"):
            open_season(previous)


def show_season(series: dict, saved_ids: list[str]) -> None:
    series_id = series["seriesId"]
    st.header(series.get("title") or "New series")
    st.caption(f"Season {series.get('seasonNumber', 1)} · {series['episodeCount']} episodes · {length_label(series['seconds'])} each")
    st.write(series["premise"])
    if error := series.get("writerError"):
        st.warning(error)
        if st.button("Retry season draft", type="primary", disabled=not writer_ready):
            retry_season_writer(store, series_id)
            st.rerun()
    if series.get("writerJob"):
        writer_progress(series_id)
        return
    if not series.get("episodes"):
        return
    status_by_episode = {ep["number"]: episode_status(store, ep) for ep in series["episodes"]}
    finished = sum(stage["status"] == "ready" for stage in status_by_episode.values())
    st.progress(finished / len(status_by_episode), text=f"{finished} of {len(status_by_episode)} episodes ready")
    section = st.radio("Season workspace", ("Episodes", "Series bible", "Trailers", "Next season"),
                       horizontal=True, key=f"{series_id}-workspace", label_visibility="collapsed")
    if section == "Episodes":
        show_episodes(series, status_by_episode)
    elif section == "Series bible":
        show_bible(series)
    elif section == "Trailers":
        show_trailers(series)
    else:
        show_next_season(series, saved_ids)


def main() -> None:
    ids = store.list()
    create_form(not ids)
    if not ids:
        return
    requested = st.query_params.get("series")
    titles = st.session_state.setdefault("tv-series-season-labels", {})
    for series_id in ids:
        if not titles.get(series_id):
            series = store.read(series_id)
            if title := series.get("title"):
                titles[series_id] = f"{title} · Season {series.get('seasonNumber', 1)}"
    if requested and requested not in ids:
        st.info("This saved series could not be found.")
    selected = st.selectbox(
        "Saved seasons", ids, index=ids.index(requested) if requested in ids else 0,
        format_func=lambda value: titles.get(value, "New series"), key=f"tv-series-{requested or 'home'}",
    )
    if selected != requested:
        st.query_params["series"] = selected
    show_season(store.read(selected), ids)


try:
    main()
except (ValueError, OSError) as exc:
    st.error(str(exc))
except Exception:
    st.error("Silver Screen could not load or save this series. Try opening it again.")
