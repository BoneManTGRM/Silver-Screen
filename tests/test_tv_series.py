from __future__ import annotations

import base64
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
from silver_screen.ai_video import PredictionPending, VideoGenerationError, verify_mp4
from silver_screen.modal_renderer import ModalComfyUIClient
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
    changed = {**shots[-1], "action": (
        "Ada opens the cabinet and lifts Sol's wet photograph from the shelf. For a moment the signal lamp "
        "lights her face and the wall behind her. She turns over the picture to study the date stamped by the "
        "harbor photographer and sets it beneath the receiver where Sol can see it from the doorway."
    ),
               "speaker": "lead", "line": "The island is next."}
    revision = draft["revision"]
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
        self.fail_suffix = None

    @contextmanager
    def batch_upload(self, *, force):
        assert force
        pending = {}

        def put_file(file, path):
            pending[path] = file.read_bytes() if isinstance(file, Path) else file.read()

        yield SimpleNamespace(put_file=put_file)
        if self.fail_suffix and any(path.endswith(self.fail_suffix) for path in pending):
            self.fail_suffix = None
            raise OSError("Injected archive outage")
        self.files.update(pending)

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


def test_budget_exhausted_episode_resumes_the_same_shots(creator, monkeypatch, tmp_path):
    class OneFailedShot(Renderer):
        def wait(self, prediction, *, on_update=None):
            if prediction["id"] == "shot-2":
                raise VideoGenerationError("The test renderer lost this shot.")
            return super().wait(prediction, on_update=on_update)

    store, _ = creator
    renderer = OneFailedShot(_fixture_clip(tmp_path / "source.mp4"))
    _configure_video(monkeypatch, renderer)
    season = tv_series.create_series(store, PREMISE, 2, 30)
    series_id = season["seriesId"]
    assert tv_series.check_series_script(store, series_id)
    tv_series.start_episode_script(store, series_id, 1)
    assert tv_series.check_series_script(store, series_id, 1)

    run_id = tv_series.start_episode_render(store, series_id, 1, max_calls=5)
    active_production(run_id).result(timeout=120)
    episode = store.read(series_id)["episodes"][0]
    status = tv_series.episode_status(store, episode)
    assert status["stopReason"] == "provider_call_budget_exhausted"
    assert status["metrics"]["verifiedShots"] == 4
    assert len(renderer.calls) == 5

    app = AppTest.from_file(ROOT / "streamlit_app.py").switch_page("pages/0_TV_Series.py")
    app.query_params["series"] = series_id
    app.run()
    limit = next(item for item in app.number_input if item.label == "Total GPU request limit")
    assert limit.value == 6
    next(item for item in app.button if item.label == "Resume saved render").click().run()
    active_production(run_id).result(timeout=120)
    episode = store.read(series_id)["episodes"][0]
    assert tv_series.episode_status(store, episode)["status"] == "ready"
    assert episode["render"]["runId"] == run_id
    assert episode["render"]["maxCalls"] == 6
    assert len(renderer.calls) == 6
    assert verify_mp4(store.episode_path(series_id, 1))["durationSeconds"] == 30


def test_cinema_opening_uses_the_14b_worker_and_keeps_vertical_reference_framing(creator, monkeypatch, tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Episode export requires FFmpeg and FFprobe.")
    source = tmp_path / "vertical.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=16",
        "-t", "6", "-threads", "2", "-c:v", "libx264", str(source),
    ], check=True, timeout=45)
    submitted = []

    def function(app, name):
        def spawn(kind, prompt, settings):
            submitted.append((name, settings))
            return SimpleNamespace(object_id="test-opening")
        return SimpleNamespace(spawn=spawn)

    class ModalRenderer(ModalComfyUIClient):
        def __init__(self):
            self.app = "test-renderer"
            self.kind = "video"
            self.supports_reference = True
            self.modal = SimpleNamespace(Function=SimpleNamespace(from_name=function))

        def wait(self, prediction, *, on_update=None):
            return {**prediction, "status": "succeeded"}

        def download_output(self, prediction, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            return destination

    store, _ = creator
    _configure_video(monkeypatch, ModalRenderer())
    monkeypatch.setattr(tv_series, "modal_configured", lambda: True)
    season = tv_series.create_series(store, PREMISE, 2, 30)
    series_id = season["seriesId"]
    assert tv_series.check_series_script(store, series_id)
    tv_series.start_episode_script(store, series_id, 1)
    assert tv_series.check_series_script(store, series_id, 1)
    reference = BytesIO()
    Image.new("RGB", (128, 64), "white").save(reference, "JPEG")
    reference.seek(0)
    tv_series.set_series_photo(store, series_id, reference)

    run_id = tv_series.start_episode_render(store, series_id, 1, max_calls=5, render_model="wan14",
                                            aspect="9:16", review_opening=True)
    active_production(run_id).result(timeout=120)
    assert len(submitted) == 1
    function_name, settings = submitted[0]
    assert function_name == "render_wan14_extended"
    assert settings["duration"] == 6 and settings["video_size"] == (720, 1280)
    assert settings["check_face"] and settings["check_body"] and settings["check_hands"]
    with Image.open(BytesIO(base64.b64decode(settings["image"].split(",", 1)[1]))) as frame:
        assert frame.size == (720, 1280)
        assert frame.getpixel((0, 0)) == (0, 0, 0)
        assert min(frame.getpixel((0, 640))) > 230
    episode = store.read(series_id)["episodes"][0]
    opening = Path(tv_series.episode_status(store, episode)["opening"]["current"])
    assert verify_mp4(opening)["durationSeconds"] == 6


def _jpeg(color):
    photo = BytesIO()
    Image.new("RGB", (96, 128), color).save(photo, "JPEG")
    photo.seek(0)
    return photo


def test_starting_frame_can_be_replaced_removed_and_is_tied_to_the_draft(creator):
    store, _ = creator
    series_id = tv_series.create_series(store, PREMISE, 2, 30)["seriesId"]
    assert tv_series.check_series_script(store, series_id)
    tv_series.start_episode_script(store, series_id, 1)
    assert tv_series.check_series_script(store, series_id, 1)
    episode = store.read(series_id)["episodes"][0]
    revision = episode["draft"]["revision"]
    with pytest.raises(tv_series.SeriesError, match="could not be read"):
        tv_series.save_episode_shot_reference(store, series_id, 1, 2, BytesIO(b"bad image"), expected_revision=revision)
    tv_series.save_episode_shot_reference(store, series_id, 1, 2, _jpeg("red"), expected_revision=revision)
    episode = store.read(series_id)["episodes"][0]
    original = tv_series.episode_shot_reference(store, series_id, episode, 2)
    tv_series.save_episode_shot_reference(store, series_id, 1, 2, _jpeg("blue"), expected_revision=revision)
    episode = store.read(series_id)["episodes"][0]
    replacement = tv_series.episode_shot_reference(store, series_id, episode, 2)
    assert original != replacement
    with Image.open(replacement) as photo:
        assert photo.size == (96, 128)
        assert photo.getpixel((48, 64))[2] > 240
    tv_series.save_episode_shot_reference(store, series_id, 1, 2, None, expected_revision=revision)
    episode = store.read(series_id)["episodes"][0]
    assert tv_series.episode_shot_reference(store, series_id, episode, 2) is None
    tv_series.save_episode_shot_reference(store, series_id, 1, 2, _jpeg("red"), expected_revision=revision)
    tv_series.start_episode_script(store, series_id, 1, rewrite=True)
    assert tv_series.check_series_script(store, series_id, 1)
    episode = store.read(series_id)["episodes"][0]
    assert tv_series.episode_shot_reference(store, series_id, episode, 2) is None
    with pytest.raises(tv_series.SeriesError, match="script was updated"):
        tv_series.save_episode_shot_reference(store, series_id, 1, 2, _jpeg("red"), expected_revision=revision)


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
        if number == 1:
            draft = store.read(series_id)["episodes"][0]["draft"]
            tv_series.save_episode_shot_reference(store, series_id, 1, 3, _jpeg("red"), expected_revision=draft["revision"])
        if number == 2:
            store.volume.fail_suffix = "/episode-2.mp4"
        run_id = tv_series.start_episode_render(store, series_id, number, max_calls=5)
        pending = active_production(run_id)
        assert pending is not None
        assert tv_series.start_episode_render(store, series_id, number, max_calls=5) == run_id
        if number == 2:
            with pytest.raises(OSError, match="archive outage"):
                pending.result(timeout=120)
        else:
            pending.result(timeout=120)
        episode = store.read(series_id)["episodes"][number - 1]
        assert episode["render"]["savedCheckpoint"]
        assert not episode["render"].get("archived")
        assert f"/tv-series/{series_id}/episode-{number}.mp4" not in store.volume.files
        shutil.rmtree(store.root / run_id)
        assert tv_series.episode_status(store, episode)["restorable"]
        if number == 1:
            assert len(renderer.calls) == 2
        assert tv_series.start_episode_render(store, series_id, number, max_calls=5) == run_id
        active_production(run_id).result(timeout=120)
        episode = store.read(series_id)["episodes"][number - 1]
        if number == 1:
            assert renderer.recovered == ["shot-2"]
        assert tv_series.episode_status(store, episode)["status"] == "ready"
        assert verify_mp4(store.episode_path(series_id, number))["durationSeconds"] == 30
        assert "00:00:00.120 --> 00:00:05.780" in tv_series.episode_subtitles(episode["draft"])

    assert len(renderer.calls) == 10
    third_prompt, third_settings = renderer.calls[2]
    assert "Start with the supplied shot image" in third_prompt
    assert "previous-frame image" not in third_prompt
    assert "Keep the appearance" in third_prompt
    with Image.open(BytesIO(base64.b64decode(third_settings["image"].split(",")[1]))) as starting_image:
        red, green, blue = starting_image.getpixel((48, 64))
        assert red > 240 and green < 10 and blue < 10
    assert third_settings["image"] != renderer.calls[7][1]["image"]
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
    class FailedOpeningRetake(Renderer):
        def wait(self, prediction, *, on_update=None):
            if prediction["id"] == "shot-2":
                raise VideoGenerationError("The alternate take failed.")
            return super().wait(prediction, on_update=on_update)

    store, _ = creator
    renderer = FailedOpeningRetake(_fixture_clip(tmp_path / "source.mp4"))
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
    next(item for item in app.text_input if item.label == "Scene heading").set_value("Wrong heading")
    next(item for item in app.button if item.label == "Save shot").click().run()
    assert "INT. or EXT." in app.error[0].value
    assert any(item.label == "Render this episode" for item in app.button)
    next(item for item in app.text_input if item.label == "Scene heading").set_value("INT. LIGHTHOUSE - NIGHT")
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
    assert "Download opening shot" in {item.proto.label for item in app.get("download_button")}
    assert len(renderer.calls) == 1
    assert action in renderer.calls[0][0]
    assert look in renderer.calls[0][0]
    original = tv_series.episode_status(store, store.read(series_id)["episodes"][0])["opening"]["current"]
    original_bytes = Path(original).read_bytes()
    next(item for item in app.text_input if item.label == "What should change in the retake?").set_value("Slow down the camera push.")
    next(item for item in app.button if item.label == "Retake opening shot").click().run()
    active_production(run_id).result(timeout=120)
    app.run()
    assert len(renderer.calls) == 2
    assert "Slow down the camera push." in renderer.calls[1][0]
    next(item for item in app.button if item.label == "Use previous opening take").click().run()
    assert Path(original).read_bytes() == original_bytes
    next(item for item in app.button if item.label == "Approve opening and render episode").click().run()
    active_production(run_id).result(timeout=120)
    app.run()
    assert "Download episode" in {item.proto.label for item in app.get("download_button")}
    assert len(renderer.calls) == 6

    reopened = AppTest.from_file(ROOT / "streamlit_app.py").switch_page("pages/0_TV_Series.py")
    reopened.query_params["series"] = series_id
    reopened.run()
    assert not reopened.exception and reopened.get("video")
    assert len(renderer.calls) == 6

    next(item for item in reopened.radio if item.label == "Season workspace").set_value("Trailers").run()
    next(item for item in reopened.selectbox if item.label == "Trailer format").select("Vertical")
    next(item for item in reopened.button if item.label == "Cut trailer").click().run(timeout=90)
    assert not reopened.error and not reopened.exception
    assert reopened.get("video")
    assert "Download trailer" in {item.proto.label for item in reopened.get("download_button")}
    trailer = next(iter(store.read(series_id)["trailers"].values()))
    video = verify_mp4(store.trailer_path(series_id, trailer["id"]))
    assert (video["durationSeconds"], video["width"], video["height"]) == (15, 720, 1280)
    assert len(renderer.calls) == 6

    next(item for item in reopened.multiselect if item.label == "Finished episodes").set_value([]).run()
    assert next(item for item in reopened.button if item.label == "Cut trailer").disabled
    assert reopened.get("video") and not reopened.error

    next(item for item in reopened.radio if item.label == "Season workspace").set_value("Next season").run()
    next(item for item in reopened.text_area if item.label == "Next season premise").set_value(
        "The siblings find a hidden island station and return to the lighthouse."
    )
    next(item for item in reopened.button if item.label == "Write next season").click().run()
    successor = store.read(reopened.query_params["series"])
    assert not reopened.error and not reopened.exception
    assert successor["previousSeasonId"] == series_id and successor["seasonNumber"] == 2
    assert successor["episodes"]


def test_seedance_episode_recovers_provider_task_and_keeps_its_model(creator, monkeypatch, tmp_path):
    from silver_screen.seedance import SEEDANCE_MODEL, SeedanceVideoClient

    fixture = tmp_path / "seedance-episode.mp4"
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Seedance episode export requires FFmpeg and FFprobe.")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24",
        "-t", "6", "-threads", "2", "-c:v", "libx264", "-preset", "ultrafast", str(fixture),
    ], check=True, timeout=30)
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("SILVER_SCREEN_VISUAL_MIN_SHARPNESS", "0.02")
    monkeypatch.setenv("SILVER_SCREEN_VIDEO_BACKEND", "comfyui")
    submissions = []
    unavailable = True

    def request(self, method, url, payload=None):
        if method == "POST":
            submissions.append(payload)
            return {"id": f"cgt-test-{len(submissions)}"}
        if unavailable:
            raise PredictionPending("BytePlus status is temporarily unavailable.")
        return {
            "status": "succeeded", "model": SEEDANCE_MODEL,
            "content": {"video_url": "https://result.tos-ap-southeast-1.volces.com/take.mp4"},
        }

    def download(self, prediction, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture, destination)
        return destination

    original_init = SeedanceVideoClient.__init__

    def init_without_wait(self):
        original_init(self, poll_seconds=0)

    monkeypatch.setattr(SeedanceVideoClient, "__init__", init_without_wait)
    monkeypatch.setattr(SeedanceVideoClient, "_task_request", request)
    monkeypatch.setattr(SeedanceVideoClient, "download_output", download)

    store = tv_series.SeriesStore(volume=MemoryVolume())
    series_id = tv_series.create_series(store, PREMISE, 2, 30)["seriesId"]
    assert tv_series.check_series_script(store, series_id)
    tv_series.set_series_photo(store, series_id, _jpeg("teal"))
    tv_series.start_episode_script(store, series_id, 1)
    assert tv_series.check_series_script(store, series_id, 1)
    draft = store.read(series_id)["episodes"][0]["draft"]
    tv_series.save_episode_shot_reference(store, series_id, 1, 2, _jpeg("red"), expected_revision=draft["revision"])

    run_id = tv_series.start_episode_render(store, series_id, 1, max_calls=5, render_model="seedance25",
                                            aspect="16:9", portrait_asset="asset-test-lead", review_opening=True)
    active_production(run_id).result(timeout=120)
    episode = store.read(series_id)["episodes"][0]
    assert tv_series.episode_status(store, episode)["stopReason"] == "prediction_pending"
    assert episode["render"]["savedCheckpoint"]
    assert len(submissions) == 1
    assert len(submissions[0]["content"]) == 2
    assert submissions[0]["content"][1]["image_url"] == {"url": "asset://asset-test-lead"}

    unavailable = False
    shutil.rmtree(store.root / run_id)
    monkeypatch.setenv("SILVER_SCREEN_VIDEO_BACKEND", "replicate")
    assert tv_series.start_episode_render(store, series_id, 1, max_calls=5) == run_id
    active_production(run_id).result(timeout=120)
    episode = store.read(series_id)["episodes"][0]
    opening = Path(tv_series.episode_status(store, episode)["opening"]["current"])
    assert len(submissions) == 1
    assert verify_mp4(opening)["durationSeconds"] == 6

    tv_series.start_episode_render(store, series_id, 1, max_calls=5, approve_opening=True)
    active_production(run_id).result(timeout=120)
    assert len(submissions) == 5
    second = submissions[1]
    assert second["ratio"] == "16:9" and second["resolution"] == "720p" and second["generate_audio"] is False
    assert [item["role"] for item in second["content"][1:]] == ["reference_image", "reference_image"]
    assert "@image2" in second["content"][0]["text"]
    assert all(len(request["content"]) == 2 for request in submissions[2:])
    episode = store.read(series_id)["episodes"][0]
    assert tv_series.episode_status(store, episode)["status"] == "ready"
    assert verify_mp4(store.episode_path(series_id, 1))["durationSeconds"] == 30
