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

    next(item for item in app.button if item.label == "Write episode script").click().run()
    assert "LIGHTHOUSE" in next(item for item in app.text_area if item.label == "Script").value
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

    reopened = AppTest.from_file(ROOT / "streamlit_app.py").switch_page("pages/0_TV_Series.py")
    reopened.query_params["series"] = series_id
    reopened.run()
    assert not reopened.exception and reopened.get("video")
    assert len(renderer.calls) == 5
