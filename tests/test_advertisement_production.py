from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from silver_screen import comfyui
from silver_screen.ai_video import PredictionPending, VideoGenerationError, verify_mp4
from silver_screen.autonomous_studio import (
    continue_autonomous_production,
    prepare_autonomous_plan,
    start_autonomous_production,
)
from silver_screen.screenplay_writer import SCREENPLAY_LENGTHS, screenplay_draft, screenplay_request


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="Requires FFmpeg and FFprobe",
)


def _draft(seconds: int) -> dict:
    actions = [
        "The camera glides beside the shoe as a runner ties its laces.",
        "The runner lifts the shoe from the starting block into a stripe of sunlight.",
        "The camera tracks the shoe as its white sole skims the painted lane.",
        "A low close-up follows the white sole of the shoe through a turn.",
        "The camera pushes closer as the runner places the shoe on the bench.",
    ]
    if seconds == 60:
        actions[4:4] = [
            "A runner in the teal shoe carefully steps over a shallow puddle.",
            "The camera tilts up as the shoe pushes against a low ramp.",
            "The runner lifts one foot to wipe dust from the white sole.",
            "The camera pans past the matching laces as both feet come to rest.",
            "Sunlight moves across the mesh as the runner sets the shoe upright.",
        ]
    return screenplay_draft(
        {
            "title": "First Light", "logline": "A runner starts before dawn.",
            "brand": "Acme", "product": "Acme running shoe",
            "appearance": "Teal mesh upper and a white sole.",
            "scenes": [{"setting": "running track", "action": action, "line": ""} for action in actions],
        },
        idea="Make an Acme running shoe commercial with teal mesh and a white sole.",
        lead="", seconds=seconds, seed=42, kind="ad", end_card={"cta": "Find your stride."},
    )


def _fixture_clip(path: Path, seconds: int, *, abrupt_cut: bool = False, near_still: bool = False) -> Path:
    filters = []
    if abrupt_cut:
        filters = ["-vf", "negate=enable='gte(t,2)'"]
    elif near_still:
        filters = ["-vf", "trim=end_frame=1,tpad=stop_mode=clone:stop_duration=10,"
                   "eq=brightness='0.012*sin(t*16)':eval=frame,noise=alls=3:allf=t+u"]
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=192x108:rate=24",
         *filters, "-frames:v", str(seconds * 24 + 1), "-c:v", "libx264", "-crf", "12", "-threads", "2", str(path)],
        check=True, timeout=30,
    )
    return path


class MockRenderer:
    model = "comfyui/workflow"
    supports_reference = True

    def __init__(self, clip: Path, cut: Path | None = None, *, first_take: Path | None = None):
        self.clip = clip
        self.cut = cut
        self.first_take = first_take
        self.calls = []
        self.recovered = []
        self.pending = cut is not None

    def create_prediction(self, prompt, **settings):
        self.calls.append((prompt, settings))
        return {"id": f"clip-{len(self.calls)}", "status": "processing"}

    def wait(self, prediction, *, on_update=None):
        if self.pending and prediction["id"] == "clip-2":
            self.pending = False
            raise PredictionPending("The second shot is still rendering.")
        return {**prediction, "status": "succeeded"}

    def get_prediction(self, prediction_id, prediction_url=None):
        self.recovered.append(prediction_id)
        return {"id": prediction_id, "status": "succeeded"}

    def download_output(self, prediction, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        source = self.cut if prediction["id"] == "clip-2" and self.cut else self.clip
        if prediction["id"] == "clip-1" and self.first_take:
            source = self.first_take
        shutil.copyfile(source, path)
        return path


@pytest.fixture
def configure_studio(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SILVER_SCREEN_MODAL_APP", raising=False)
    monkeypatch.setenv("SILVER_SCREEN_VIDEO_BACKEND", "comfyui")
    monkeypatch.setenv("SILVER_SCREEN_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW", str(ROOT / "deploy/runpod/workflows/wan-video.json"))
    monkeypatch.setenv("SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW", str(ROOT / "deploy/runpod/workflows/wan-reference.json"))
    monkeypatch.setenv("SILVER_SCREEN_VISUAL_QUALITY_GATE", "1")
    # Test patterns have flat regions. Keep the abrupt-cut detector active.
    monkeypatch.setenv("SILVER_SCREEN_VISUAL_MIN_SHARPNESS", "0.02")
    return monkeypatch


def _assert_finished(result: dict, seconds: int, renderer: MockRenderer, calls: int) -> None:
    media = result["result"]["media"]
    assert media["status"] == "complete"
    assert not media["partial_video_path"]
    assert media["metrics"]["verifiedShots"] == 5
    assert media["metrics"]["verifiedSeconds"] == seconds
    assert media["metrics"]["providerCalls"] == len(renderer.calls) == calls
    assert result["edl"]["durationSeconds"] == seconds
    assert [clip["timelineStartSeconds"] for clip in result["edl"]["tracks"]["video"]] == [
        order * SCREENPLAY_LENGTHS[seconds] for order in range(5)
    ]
    path = Path(result["job"]["artifacts"]["shortVideo"])
    assert path.name == "advertisement.mp4"
    metadata = verify_mp4(path)
    assert metadata["durationSeconds"] == seconds
    assert metadata["frameRate"] == 24
    assert all("Acme running shoe" in prompt and "Teal mesh upper" in prompt for prompt, _ in renderer.calls)


def test_render_button_finishes_all_five_advertisement_shots(tmp_path, configure_studio):
    renderer = MockRenderer(_fixture_clip(tmp_path / "shot.mp4", 4))
    configure_studio.setattr(comfyui, "make_comfyui_client", lambda **kwargs: renderer)
    app = AppTest.from_file(ROOT / "pages/9_Autonomous_Studio.py")
    app.session_state["screenplay-production"] = _draft(20)
    app.run()
    next(button for button in app.button if button.label == "Render advertisement").click().run(timeout=20)
    _, future = app.session_state["screenplay-render-task"]
    future.result(timeout=90)
    app.run(timeout=20)
    assert not app.exception
    assert [button.proto.label for button in app.get("download_button")] == ["Download advertisement"]
    result = app.session_state["autonomous_result"]
    _assert_finished(result, 20, renderer, 5)
    assert all(settings["duration"] == 4 and not settings["image"] for _, settings in renderer.calls)
    before = len(renderer.calls)
    reopened = AppTest.from_file(ROOT / "streamlit_app.py").switch_page("pages/9_Autonomous_Studio.py")
    reopened.query_params["production"] = result["runId"]
    reopened.run(timeout=15)
    _, saved = reopened.session_state["screenplay-render-task"]
    saved.result(timeout=90)
    reopened.run(timeout=15)
    assert not reopened.exception
    assert [button.proto.label for button in reopened.get("download_button")] == ["Download advertisement"]
    assert len(renderer.calls) == before


def test_resumed_advertisement_rejects_cut_and_keeps_same_product_photo(tmp_path, configure_studio):
    renderer = MockRenderer(
        _fixture_clip(tmp_path / "shot.mp4", 6),
        _fixture_clip(tmp_path / "cut.mp4", 6, abrupt_cut=True),
    )
    configure_studio.setattr(comfyui, "make_comfyui_client", lambda **kwargs: renderer)
    reference = tmp_path / "shoe.jpg"
    Image.new("RGB", (96, 64), "teal").save(reference)
    draft = _draft(30)
    plan = prepare_autonomous_plan(
        draft, target_runtime_seconds=30, clip_duration_seconds=6, max_shots=5,
        config={"continuous": True, "batchSize": 5, "maxProviderCalls": 6, "semanticQa": False},
    )
    first = start_autonomous_production(plan, images=[reference])
    first_media = first["result"]["media"]
    assert first_media["stopReason"] == "prediction_pending"
    assert (first_media["metrics"]["verifiedShots"], len(renderer.calls)) == (1, 2)
    assert not first["job"]["artifacts"].get("shortVideo")
    result = continue_autonomous_production(first["runId"])
    _assert_finished(result, 30, renderer, 6)
    assert renderer.recovered == ["clip-2"]
    assert "Retake direction" in renderer.calls[2][0]
    assert "one continuous camera move" in renderer.calls[2][0]
    photo = renderer.calls[0][1]["image"]
    assert photo.startswith("data:image/jpeg;base64,")
    assert all(settings["image"] == photo and settings["duration"] == 6 for _, settings in renderer.calls)
    assert len(list(Path(first["result"]["run"]["workspace"]).glob("media/visual_quality/rejected/*/*.mp4"))) == 1


@pytest.mark.parametrize("allow_still", [False, True])
def test_ad_handles_near_still_shots_by_operator_choice(tmp_path, configure_studio, allow_still):
    renderer = MockRenderer(
        _fixture_clip(tmp_path / "moving.mp4", 4),
        first_take=_fixture_clip(tmp_path / "near-still.mp4", 4, near_still=True),
    )
    configure_studio.setattr(comfyui, "make_comfyui_client", lambda **kwargs: renderer)
    draft = _draft(20)
    draft["shotDirection"]["allowStillProductShots"] = allow_still
    plan = prepare_autonomous_plan(
        draft, target_runtime_seconds=20, clip_duration_seconds=4, max_shots=5,
        config={"continuous": True, "batchSize": 5, "maxProviderCalls": 6, "semanticQa": False,
                "transitionMode": "off"},
    )
    result = start_autonomous_production(plan)
    _assert_finished(result, 20, renderer, 5 if allow_still else 6)
    if not allow_still:
        assert "Retake direction" in renderer.calls[1][0]
        assert "visible action in the approved shot" in renderer.calls[1][0]


def test_provider_policy_rejection_preserves_prompt_and_does_not_resubmit(tmp_path, configure_studio):
    renderer = MockRenderer(_fixture_clip(tmp_path / "shot.mp4", 4))
    error = "HTTP 422: Request declined by the provider content policy."

    def reject_prediction(prediction, *, on_update=None):
        raise VideoGenerationError(error)

    configure_studio.setattr(renderer, "wait", reject_prediction)
    configure_studio.setattr(comfyui, "make_comfyui_client", lambda **kwargs: renderer)
    plan = prepare_autonomous_plan(
        _draft(20), target_runtime_seconds=20, clip_duration_seconds=4, max_shots=5,
        config={"continuous": True, "batchSize": 5, "maxProviderCalls": 6, "semanticQa": False,
                "transitionMode": "off"},
    )
    result = start_autonomous_production(plan)
    for production in (result, continue_autonomous_production(result["runId"])):
        media = production["result"]["media"]
        assert media["stopReason"] == "provider_prompt_rejected"
        assert media["queue"]["shots"][0]["lastError"] == error
        assert "ties its laces" in media["queue"]["shots"][0]["prompt"]
    assert len(renderer.calls) == 1


def test_sixty_second_ad_has_ten_distinct_timed_prompts(configure_studio):
    draft = _draft(60)
    plan = prepare_autonomous_plan(
        draft, target_runtime_seconds=60, clip_duration_seconds=6, max_shots=10,
        config={"maxProviderCalls": 10, "transitionMode": "off", "semanticQa": False},
    )
    assert plan["executionPlan"]["plannedShots"] == 10
    assert plan["renderPlan"]["plannedRuntimeSeconds"] == 60
    assert len(set(draft["shotDirection"]["shotPromptOverrides"].values())) == 10
    assert draft["authoredScript"].endswith("NARRATOR: Find your stride.")


@pytest.mark.parametrize("kind,seconds", [("movie_trailer", 30), ("series_trailer", 60)])
def test_trailer_script_to_verified_export(tmp_path, configure_studio, kind, seconds):
    count = seconds // 6
    request = screenplay_request("A pilot finds a city inside a storm.", "Vale", seconds, 77, kind=kind)
    scenes = request["response_format"]["json_schema"]["schema"]["properties"]["scenes"]
    assert (scenes["minItems"], scenes["maxItems"]) == (count, count)
    locations = ["mountain", "bridge", "harbor", "cockpit", "forest", "hangar", "desert", "tower", "station", "river"]
    draft = screenplay_draft(
        {
            "title": "Storm City", "logline": "A pilot finds a city inside a storm.",
            "genre": "scifi", "tone": "cinematic",
            "lead": {"name": "Vale" if kind == "movie_trailer" else "", "role": "Pilot", "appearance": "Gray flight jacket"},
            "support": {"name": "Robyn", "role": "Dispatcher", "appearance": "Blue wool coat"},
            "scenes": [
                {"heading": f"EXT. {location.upper()} - NIGHT", "speaker": "none", "line": "",
                 "action": action} for location, action in zip(locations, [
                    "Vale hooks a line to a steel rail while fog rolls over the mountain.",
                    "From beneath the bridge, a flash briefly lights three windows in the cloud.",
                    "A rocking freighter cuts its engine as ripples turn toward the deserted harbor.",
                    "Robyn lifts a microphone as a pulse passes through the instrument panel behind Vale.",
                    "A distant pulse bends the pine trees midway down the ridge and the forest darkens.",
                    "Vale crosses the empty hangar carrying a dented flight recorder with a red light.",
                    "A wide pan follows a moving wall of dust across abandoned tracks in the desert.",
                    "A rotating beam exposes hanging cables above a crowded stairwell inside the tower.",
                    "The swing of a clock hand reveals a row of empty chairs along the platform.",
                    "Robyn steps onto a small boat, watching a door rise from the river.",
                ][:count])
            ],
        },
        idea="A pilot finds a city inside a storm.", lead="Vale" if kind == "movie_trailer" else "", seconds=seconds, seed=77, kind=kind,
    )
    renderer = MockRenderer(_fixture_clip(tmp_path / "shot.mp4", 6))
    assert draft["cast"][0]["name"] == ("Vale" if kind == "movie_trailer" else "Lead")
    configure_studio.setattr(comfyui, "make_comfyui_client", lambda **kwargs: renderer)
    plan = prepare_autonomous_plan(
        draft, target_runtime_seconds=seconds, clip_duration_seconds=6, max_shots=count,
        config={"continuous": True, "batchSize": count, "maxProviderCalls": count, "semanticQa": False,
                "transitionMode": "off"},
    )
    result = start_autonomous_production(plan)
    assert result["result"]["brief"]["screenplayWriter"] == kind
    assert result["result"]["media"]["status"] == "complete"
    assert len(renderer.calls) == count
    assert len(result["edl"]["tracks"]["video"]) == count
    path = Path(result["job"]["artifacts"]["shortVideo"])
    assert path.name == "trailer.mp4"
    assert verify_mp4(path)["durationSeconds"] == seconds
