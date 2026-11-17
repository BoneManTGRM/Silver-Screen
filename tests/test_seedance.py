from __future__ import annotations

import base64
import shutil
import subprocess
import urllib.error
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from silver_screen.ai_video import PredictionPending
from silver_screen.seedance import (
    SEEDANCE_MODEL, SEEDANCE_TASKS_URL, SeedanceSubmissionUnknown, SeedanceVideoClient,
)

ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "cgt-20261117120000-test01"
VIDEO_URL = "https://output.tos-ap-southeast-1.volces.com/clip.mp4"


@pytest.fixture
def seedance_key(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")


@pytest.mark.parametrize("portrait", ("", "asset://asset-20261117120000-ab123"))
def test_seedance_payload_preserves_framing_and_uses_the_correct_reference_roles(seedance_key, monkeypatch, portrait):
    def submit(self, method, url, payload):
        assert (method, url) == ("POST", SEEDANCE_TASKS_URL)
        assert payload["model"] == SEEDANCE_MODEL
        assert payload["duration"] == 30 and payload["resolution"] == "720p"
        assert payload["generate_audio"] is True and payload["seed"] == 42
        assert payload["output_format"] == "mp4"
        content = payload["content"]
        if portrait:
            assert payload["ratio"] == "9:16"
            assert content[1] == {"type": "image_url", "role": "reference_image", "image_url": {"url": portrait}}
            assert "@image1" in content[0]["text"] and "@image2" in content[0]["text"]
            assert content[-1]["role"] == "reference_image"
        else:
            assert payload["ratio"] == "adaptive"
            assert content[-1]["role"] == "first_frame"
        data = base64.b64decode(content[-1]["image_url"]["url"].split(",", 1)[1])
        with Image.open(BytesIO(data)) as reference:
            assert reference.size == (720, 1280)
            assert reference.getpixel((0, 0)) == (0, 0, 0)
            assert reference.getpixel((360, 640))[0] > 240
        return {"id": TASK_ID}

    monkeypatch.setattr(SeedanceVideoClient, "_task_request", submit)
    photo = BytesIO()
    Image.new("RGB", (640, 360), "red").save(photo, "JPEG")
    image = "data:image/jpeg;base64," + base64.b64encode(photo.getvalue()).decode()
    prediction = SeedanceVideoClient().create_prediction(
        "The camera slowly approaches a red doorway.", seed=42, duration=30, ratio="9:16", image=image,
        portrait_asset=portrait,
    )
    assert prediction == {"id": TASK_ID, "model": SEEDANCE_MODEL, "status": "starting"}


def test_seedance_ambiguous_submit_and_transient_poll_are_distinct(seedance_key, monkeypatch):
    class Offline:
        def open(self, request, timeout):
            assert request.get_header("Authorization") == "Bearer test-key"
            raise urllib.error.URLError("connection was interrupted")

    monkeypatch.setattr("silver_screen.seedance.urllib.request.build_opener", lambda _: Offline())
    client = SeedanceVideoClient()
    with pytest.raises(SeedanceSubmissionUnknown, match="may have accepted"):
        client.create_prediction("The camera moves through a station.", seed=42)
    with pytest.raises(PredictionPending, match="same render"):
        client.get_prediction(TASK_ID, "https://example.com/ignored")


def test_seedance_signed_download_does_not_receive_the_api_key(seedance_key, monkeypatch, tmp_path):
    class Delivery:
        def open(self, url, timeout):
            assert url == VIDEO_URL and isinstance(url, str)
            return BytesIO(b"video payload")

    monkeypatch.setattr("silver_screen.seedance.urllib.request.build_opener", lambda _: Delivery())
    path = SeedanceVideoClient().download_output({"output": VIDEO_URL}, tmp_path / "clip.mp4")
    assert path.read_bytes() == b"video payload"


def test_direct_create_submits_recovers_and_archives_a_seedance_clip(seedance_key, monkeypatch, tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("The clip archive test requires FFmpeg and FFprobe.")
    fixture = tmp_path / "motion.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24",
        "-t", "4", "-threads", "2", "-c:v", "libx264", "-preset", "ultrafast", str(fixture),
    ], check=True, timeout=30)
    data = fixture.read_bytes()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SILVER_SCREEN_MODAL_APP", raising=False)
    posts = []
    ready = False

    def task(self, method, url, payload=None):
        if method == "POST":
            posts.append(payload)
            return {"id": TASK_ID}
        assert url == f"{SEEDANCE_TASKS_URL}/{TASK_ID}"
        if not ready:
            return {"id": TASK_ID, "status": "running"}
        return {
            "id": TASK_ID, "model": SEEDANCE_MODEL, "status": "succeeded", "resolution": "720p",
            "content": {"video_url": VIDEO_URL}, "usage": {"completion_tokens": 86400},
            "created_at": 100, "updated_at": 132,
        }

    def download(self, prediction, destination):
        assert prediction["output"] == VIDEO_URL
        destination.write_bytes(data)
        return destination

    monkeypatch.setattr(SeedanceVideoClient, "_task_request", task)
    monkeypatch.setattr(SeedanceVideoClient, "download_output", download)
    page = ROOT / "pages/0_Direct_Create.py"
    app = AppTest.from_file(page).run()
    app.radio[0].set_value("Video clip").run()
    next(item for item in app.radio if item.label == "Generation backend").set_value("Seedance 2.5").run()
    next(item for item in app.number_input if item.label == "Clip length in seconds").set_value(4).run()
    next(item for item in app.text_area if item.label == "Prompt").set_value("The camera moves through a station.")
    next(item for item in app.button if item.label == "Generate").click().run()
    assert not app.exception
    assert app.query_params["render"] == TASK_ID
    assert len(posts) == 1 and posts[0]["duration"] == 4
    assert posts[0]["ratio"] == "16:9"

    ready = True
    reopened = AppTest.from_file(page)
    reopened.query_params["render"] = TASK_ID
    reopened.run()
    assert not reopened.exception and reopened.get("video")
    assert len(posts) == 1
    assert (tmp_path / "runs" / "seedance-clips" / f"{TASK_ID}.mp4").read_bytes() == data
    monkeypatch.delenv("ARK_API_KEY")
    from_archive = AppTest.from_file(page)
    from_archive.query_params["render"] = TASK_ID
    from_archive.run()
    assert not from_archive.exception and from_archive.get("video")
    assert "Download original clip" in {item.proto.label for item in from_archive.get("download_button")}
    assert len(posts) == 1
