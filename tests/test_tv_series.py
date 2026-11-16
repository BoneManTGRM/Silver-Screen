from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from silver_screen import comfyui, tv_series
from silver_screen.ai_video import PredictionPending, verify_mp4
from silver_screen.autonomous_studio import active_production, prepare_autonomous_plan
from silver_screen.screenplay_writer import EPISODE_KIND, screenplay_lengths

ROOT = Path(__file__).resolve().parents[1]
PREMISE = "Two siblings discover a radio receiver that plays the next day's emergency calls."
BIBLE = {
    "title": "Tomorrow Channel",
    "world": "A coastal town and its abandoned lighthouse radio station.",
    "arc": "The siblings learn which calls are real and warn the harbor before a winter storm.",
    "genre": "scifi",
    "tone": "cinematic",
    "lead": {"name": "Ada Quinn", "role": "Radio engineer", "appearance": "Short brown curls, brown eyes, red raincoat."},
    "support": {"name": "Sol Quinn", "role": "Harbor mechanic", "appearance": "Black hair, green jacket, silver glasses."},
    "episodes": [
        {"title": "The Signal", "goal": "Ada finds the source of a distress signal in the lighthouse.",
         "outcome": "Ada realizes the recording predicts a harbor rescue.", "hook": "The next voice is Sol's."},
        {"title": "A Second Voice", "goal": "Ada and Sol decode a new radio transmission at low tide.",
         "outcome": "They warn the harbor in time.", "hook": "A third voice answers from the empty lighthouse."},
    ],
}


def script(count: int, *, action: str = "Ada checks the receiver with Sol.") -> dict:
    scenes = []
    for i in range(count):
        scenes.append({
            "heading": f"INT. LIGHTHOUSE ROOM {i + 1} - NIGHT",
            "action": f"{action} She carries the brass dial to table {i + 1} and points to its active lamp.",
            "speaker": "lead" if i % 2 else "support",
            "line": "This voice is tomorrow.",
        })
    return {"scenes": scenes}


@pytest.fixture
def creator(monkeypatch, tmp_path):
    requests = []
    results = {}

    def submit(payload):
        requests.append(payload)
        job = f"writer-{len(requests)}"
        scope = json.loads(payload["messages"][1]["content"])
        if "episode count" in scope:
            results[job] = BIBLE
        else:
            results[job] = script(scope["shots"], action=f"Ada studies clue {scope['episode']['number']} beside Sol.")
        return job

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SILVER_SCREEN_MODAL_APP", raising=False)
    monkeypatch.setattr(tv_series, "submit_writer_request", submit)
    monkeypatch.setattr(tv_series, "poll_writer_result", results.get)
    return tv_series.SeriesStore(), requests


def test_season_writer_reuses_cast_and_prior_story_at_two_minute_length(creator):
    store, requests = creator
    season = tv_series.create_series(store, PREMISE, 2, 120, title="After the Signal")
    series_id = season["seriesId"]
    assert store.read(series_id)["writerJob"] == "writer-1"
    assert tv_series.check_series_script(store, series_id)
    assert store.read(series_id)["title"] == "After the Signal"
    for number in (1, 2):
        tv_series.start_episode_script(store, series_id, number)
        tv_series.start_episode_script(store, series_id, number)
        assert tv_series.check_series_script(store, series_id, number)
    season = store.read(series_id)
    first, second = [episode["draft"] for episode in season["episodes"]]
    assert first["cast"] == second["cast"]
    assert [cast["name"] for cast in second["cast"]] == ["Ada Quinn", "Sol Quinn"]
    assert len(first["shotDirection"]["shotPromptOverrides"]) == len(second["shotDirection"]["shotPromptOverrides"]) == 20
    assert set(first["shotDirection"]["shotPromptOverrides"].values()).isdisjoint(
        second["shotDirection"]["shotPromptOverrides"].values(),
    )
    assert len(requests) == 3
    prompt = json.loads(requests[-1]["messages"][1]["content"])
    assert prompt["prior outcomes"] == [BIBLE["episodes"][0]["outcome"]]
    assert "clue 1" in prompt["previous scripted ending"]
    assert first["screenplayWriter"] == second["screenplayWriter"] == EPISODE_KIND
    assert screenplay_lengths("ad") == (20, 30, 60)
    plan = prepare_autonomous_plan(
        second, target_runtime_seconds=120, clip_duration_seconds=6, max_shots=20,
        config={"projectId": series_id, "semanticQa": False},
    )
    assert plan["projectId"] == series_id
    assert plan["executionPlan"]["plannedShots"] == 20
    assert plan["approvedBrief"]["screenplayWriter"] == EPISODE_KIND


def test_sequel_inherits_edited_bible_photo_and_scripted_finale(creator):
    store, requests = creator
    season = tv_series.create_series(store, PREMISE, 2, 30)
    series_id = season["seriesId"]
    assert tv_series.check_series_script(store, series_id)
    season = store.read(series_id)
    season.pop("seasonNumber")
    store.write(season)
    cast = season["cast"]
    cast["lead"]["appearance"] = "Same red raincoat and brass watch in every dock scene."
    look = "Amber lamps, restrained camera movement, blue morning fog."
    tv_series.save_season_bible(store, series_id, world=season["world"], arc=season["arc"],
                              genre=season["genre"], tone=season["tone"], cast=cast, visual_direction=look)
    photo = BytesIO()
    Image.new("RGB", (96, 128), "red").save(photo, "JPEG")
    photo.seek(0)
    tv_series.set_series_photo(store, series_id, photo)

    outline = {**season["episodes"][1], "outcome": "The broadcast reveals a child on the island."}
    tv_series.save_episode_outline(store, series_id, 2, outline)
    tv_series.start_episode_script(store, series_id, 2)
    tv_series.save_season_bible(store, series_id, world=season["world"], arc=season["arc"],
                              genre=season["genre"], tone=season["tone"], cast=cast, visual_direction="Teal interiors.")
    assert tv_series.check_series_script(store, series_id, 2)
    draft = store.read(series_id)["episodes"][1]["draft"]
    assert draft["creativeDirection"]["globalVisualDirection"] == look
    shots = tv_series.episode_shots(draft)
    changed = {**shots[-1], "action": "Ada opens the cabinet and lifts Sol's wet photograph from the shelf.",
               "speaker": "lead", "line": "The island is next."}
    revision = str(draft["seed"])
    tv_series.save_episode_shot(store, series_id, 2, 5, changed, expected_revision=revision)
    with pytest.raises(tv_series.SeriesError, match="script was updated"):
        tv_series.save_episode_shot(store, series_id, 2, 5, changed, expected_revision=revision)
    edited = store.read(series_id)["episodes"][1]["draft"]
    assert "lifts Sol's wet photograph" in edited["shotDirection"]["shotPromptOverrides"]["5"]
    assert "ADA QUINN: The island is next." in tv_series.episode_subtitles(edited)

    premise = "Ada takes a boat to the island and searches for the missing child."
    sequel = tv_series.start_next_season(store, series_id, premise, 2, 60)
    assert sequel["seasonNumber"] == 2
    assert sequel["leadReference"] == store.read(series_id)["leadReference"]
    assert sequel["previousSeasonId"] == series_id
    assert tv_series.start_next_season(store, series_id, premise, 2, 60)["writerJob"] == sequel["writerJob"]
    payload = json.loads(requests[-1]["messages"][1]["content"])
    assert len(requests) == 3
    assert payload["previous finale"]["outcome"] == outline["outcome"]
    assert "The island is next." in payload["previous finale"]["scripted ending"]
    assert payload["cast"] == cast
    assert tv_series.check_series_script(store, sequel["seriesId"])
    assert store.read(sequel["seriesId"])["cast"] == cast
    tv_series.start_episode_script(store, sequel["seriesId"], 1)
    sequel_episode = json.loads(requests[-1]["messages"][1]["content"])
    assert sequel_episode["previous season finale"] == payload["previous finale"]
    assert sequel_episode["visual direction"] == "Teal interiors."


class MemoryVolume:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    @contextmanager
    def batch_upload(self, *, force):
        assert force
        yield SimpleNamespace(put_file=lambda file, path: self.files.__setitem__(path, file.read()))

    def read_file(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        yield self.files[path]

    def iterdir(self, path, *, recursive):
        assert recursive
        yield from (SimpleNamespace(path=name) for name in self.files if name.startswith(path + "/"))


def _fixture_clip(path: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("Episode export requires FFmpeg and FFprobe.")
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
        "-t", "6", "-threads", "2", "-c:v", "libx264", str(path),
    ], check=True, timeout=30)
    return path


def test_trailer_uses_multiple_episodes_omits_their_final_shots_and_reopens(creator, monkeypatch, tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Trailer export requires FFmpeg and FFprobe.")
    store = tv_series.SeriesStore(volume=MemoryVolume())
    season = tv_series.create_series(store, PREMISE, 2, 30)
    series_id = season["seriesId"]
    assert tv_series.check_series_script(store, series_id)
    for number, color in enumerate(("red", "blue"), 1):
        tv_series.start_episode_script(store, series_id, number)
        assert tv_series.check_series_script(store, series_id, number)
        source = tmp_path / f"source-{number}.mp4"
        audio = ["-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000"] if number == 2 else []
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:s=320x180:r=24", *audio,
            "-vf", "drawbox=color=lime:t=fill:enable='gte(t,24)'", "-t", "30",
            "-threads", "2", "-c:v", "libx264", str(source),
        ], check=True, timeout=30)
        store.archive_episode(series_id, number, source, 30)
        season = store.read(series_id)
        season["episodes"][number - 1]["render"] = {"runId": f"episode-source-{number}", "archived": True}
        store.write(season)

    with pytest.raises(tv_series.SeriesError, match="more finished episodes"):
        tv_series.cut_season_trailer(store, series_id, [1], seconds=30, frame_format="Widescreen")
    options = {"seconds": 30, "frame_format": "Widescreen", "tagline": "Tomorrow is calling."}
    trailer = tv_series.cut_season_trailer(store, series_id, [2, 1], **options)
    assert tv_series.cut_season_trailer(store, series_id, [1, 2], **options) == trailer
    new_root = tmp_path / "reopened"
    new_root.mkdir()
    monkeypatch.chdir(new_root)
    reopened = tv_series.SeriesStore(volume=store.volume)
    assert reopened.read(series_id)["trailers"][trailer["id"]] == trailer
    path = reopened.trailer_path(series_id, trailer["id"])
    video = verify_mp4(path)
    assert (video["durationSeconds"], video["width"], video["height"]) == (30, 1280, 720)

    def frame(seconds: int) -> list[tuple[int, int, int]]:
        data = subprocess.check_output([
            "ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(path), "-frames:v", "1",
            "-vf", "scale=320:180", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
        ], timeout=15)
        assert len(data) == 320 * 180 * 3
        return list(zip(data[::3], data[1::3], data[2::3]))

    center = 90 * 320 + 160
    assert frame(1)[center][0] > 200
    assert frame(5)[center][2] > 200
    assert max(pixel[1] for pixel in frame(25)) < 100
    assert any(min(pixel) > 220 for pixel in frame(28))


class Renderer:
    model = "comfyui/workflow"
    supports_reference = True

    def __init__(self, clip, interrupt=False):
        self.clip = clip
        self.calls = []
        self.recovered = []
        self.interrupt = interrupt

    def create_prediction(self, prompt, **settings):
        self.calls.append((prompt, settings))
        return {"id": f"shot-{len(self.calls)}", "status": "processing"}

    def wait(self, prediction, *, on_update=None):
        if self.interrupt and len(self.calls) == 2:
            self.interrupt = False
            raise PredictionPending("The second shot is still rendering.")
        return {**prediction, "status": "succeeded"}

    def get_prediction(self, prediction_id, prediction_url=None):
        self.recovered.append(prediction_id)
        return {"id": prediction_id, "status": "succeeded"}

    def download_output(self, prediction, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.clip, path)
        return path


def _configure_video(monkeypatch, renderer):
    monkeypatch.setenv("SILVER_SCREEN_VISUAL_QUALITY_GATE", "1")
    monkeypatch.setenv("SILVER_SCREEN_VISUAL_MIN_SHARPNESS", "0.02")
    monkeypatch.setenv("SILVER_SCREEN_VIDEO_BACKEND", "comfyui")
    monkeypatch.setenv("SILVER_SCREEN_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW", str(ROOT / "deploy/runpod/workflows/wan-video.json"))
    monkeypatch.setenv("SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW", str(ROOT / "deploy/runpod/workflows/wan-reference.json"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(comfyui, "make_comfyui_client", lambda: renderer)


def test_two_episodes_share_reference_resume_and_reopen_from_archive(creator, monkeypatch, tmp_path):
    _, _ = creator
    store = tv_series.SeriesStore(volume=MemoryVolume())
    season = tv_series.create_series(store, PREMISE, 2, 30)
    series_id = season["seriesId"]
    assert tv_series.check_series_script(store, series_id)
    assert series_id in store.list()
    photo = BytesIO()
    Image.new("RGB", (128, 96), "teal").save(photo, "JPEG")
    photo.seek(0)
    tv_series.set_series_photo(store, series_id, photo)

    renderer = Renderer(_fixture_clip(tmp_path / "source.mp4"), interrupt=True)
    _configure_video(monkeypatch, renderer)
    for number in (1, 2):
        tv_series.start_episode_script(store, series_id, number)
        assert tv_series.check_series_script(store, series_id, number)
        run_id = tv_series.start_episode_render(store, series_id, number, max_calls=5)
        pending = active_production(run_id)
        assert pending is not None
        assert tv_series.start_episode_render(store, series_id, number, max_calls=5) == run_id
        pending.result(timeout=120)
        episode = store.read(series_id)["episodes"][number - 1]
        if number == 1:
            assert tv_series.episode_status(store, episode)["status"] == "checkpoint"
            assert len(renderer.calls) == 2
            assert tv_series.start_episode_render(store, series_id, number, max_calls=5) == run_id
            active_production(run_id).result(timeout=120)
            episode = store.read(series_id)["episodes"][0]
            assert renderer.recovered == ["shot-2"]
        assert tv_series.episode_status(store, episode)["status"] == "ready"
        assert verify_mp4(store.episode_path(series_id, number))["durationSeconds"] == 30
        assert "00:00:00.120 --> 00:00:05.780" in tv_series.episode_subtitles(episode["draft"])

    assert len(renderer.calls) == 10
    assert renderer.calls[0][1]["image"] == renderer.calls[5][1]["image"]
    assert renderer.calls[0][1]["image"].startswith("data:image/jpeg;base64,")
    fresh_workspace = tmp_path / "new-workspace"
    fresh_workspace.mkdir()
    monkeypatch.chdir(fresh_workspace)
    moved = tv_series.SeriesStore(volume=store.volume)
    assert moved.list() == [series_id]
    assert moved.read(series_id)["episodes"][0]["render"]["archived"]
    assert verify_mp4(moved.episode_path(series_id, 2))["durationSeconds"] == 30


def test_tv_series_page_renders_and_opens_the_selected_episode(creator, monkeypatch, tmp_path):
    store, _ = creator
    renderer = Renderer(_fixture_clip(tmp_path / "source.mp4"))
    _configure_video(monkeypatch, renderer)
    monkeypatch.setattr("silver_screen.modal_renderer.screenplay_writer_configured", lambda: True)
    app = AppTest.from_file(ROOT / "streamlit_app.py").switch_page("pages/0_TV_Series.py")
    app.run()
    assert not app.exception
    app.text_area[0].set_value(PREMISE)
    next(item for item in app.selectbox if item.label == "Episodes in season 1").select(2)
    next(item for item in app.selectbox if item.label == "Episode length").select(30)
    next(item for item in app.button if item.label == "Write season").click().run()
    assert store.read(app.query_params["series"])["episodes"]

    look = "Deep indigo shadows and amber lighthouse windows."
    next(item for item in app.radio if item.label == "Season workspace").set_value("Series bible").run()
    next(item for item in app.text_area if item.label == "Visual direction").set_value(look)
    next(item for item in app.button if item.label == "Save series bible").click().run()
    assert store.read(app.query_params["series"])["visualDirection"] == look
    next(item for item in app.radio if item.label == "Season workspace").set_value("Episodes").run()

    next(item for item in app.button if item.label == "Write episode script").click().run()
    assert "LIGHTHOUSE" in next(item for item in app.text_area if item.label == "Script").value
    action = "Ada slides the brass receiver across the floor toward the broken doorway."
    next(item for item in app.text_area if item.label == "Visible action").set_value(action)
    next(item for item in app.button if item.label == "Save shot").click().run()
    assert action in next(item for item in app.text_area if item.label == "Script").value
    next(item for item in app.button if item.label == "Render this episode").click().run()
    series_id = app.query_params["series"]
    run_id = store.read(series_id)["episodes"][0]["render"]["runId"]
    future = active_production(run_id)
    assert future is not None
    future.result(timeout=120)
    app.run()
    assert not app.exception
    assert app.get("video")
    assert "Download episode" in {item.proto.label for item in app.get("download_button")}
    assert len(renderer.calls) == 5
    assert action in renderer.calls[0][0]
    assert look in renderer.calls[0][0]

    reopened = AppTest.from_file(ROOT / "streamlit_app.py").switch_page("pages/0_TV_Series.py")
    reopened.query_params["series"] = series_id
    reopened.run()
    assert not reopened.exception and reopened.get("video")
    assert len(renderer.calls) == 5

    next(item for item in reopened.radio if item.label == "Season workspace").set_value("Trailers").run()
    next(item for item in reopened.selectbox if item.label == "Trailer format").select("Vertical")
    next(item for item in reopened.button if item.label == "Cut trailer").click().run(timeout=90)
    assert not reopened.error and not reopened.exception
    assert reopened.get("video")
    assert "Download trailer" in {item.proto.label for item in reopened.get("download_button")}
    trailer = next(iter(store.read(series_id)["trailers"].values()))
    video = verify_mp4(store.trailer_path(series_id, trailer["id"]))
    assert (video["durationSeconds"], video["width"], video["height"]) == (15, 720, 1280)
    assert len(renderer.calls) == 5

    next(item for item in reopened.radio if item.label == "Season workspace").set_value("Next season").run()
    next(item for item in reopened.text_area if item.label == "Next season premise").set_value(
        "The siblings find a hidden island station and return to the lighthouse."
    )
    next(item for item in reopened.button if item.label == "Write next season").click().run()
    successor = store.read(reopened.query_params["series"])
    assert not reopened.error and not reopened.exception
    assert successor["previousSeasonId"] == series_id and successor["seasonNumber"] == 2
    assert successor["episodes"]
