from io import BytesIO
import json
from pathlib import Path
from threading import Event
from time import monotonic
from unittest.mock import Mock

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

pytest.importorskip("starlette")
from starlette.requests import Request

from silver_screen import comfyui, modal_renderer, render_preview
from silver_screen.ai_video import PredictionPending
from silver_screen.direct_generation import CLIP_MODEL, IMAGE_MODEL
from silver_screen.face_review import summarize_face_review
from silver_screen.modal_clip_archive import RecentRender, read_saved_reviews, save_completed_clip


RENDER_ID = "fc-" + "1" * 26
MP4 = b"\x00\x00\x00\x20ftypisom" + b"frame data"
DIRECT_CREATE = Path(__file__).resolve().parents[1] / "pages" / "0_Direct_Create.py"


def request(method="GET", *, byte_range=None, query=b"", render_id=RENDER_ID):
    headers = [(b"range", byte_range.encode())] if byte_range else []
    return Request({
        "type": "http",
        "method": method,
        "path_params": {"render_id": render_id},
        "headers": headers,
        "query_string": query,
    })


@pytest.mark.parametrize("image", (False, True))
def test_direct_create_shows_completed_render_outside_progress_frame(monkeypatch, image):
    monkeypatch.setenv("SILVER_SCREEN_HTTP_RENDER_PREVIEW", "1")
    monkeypatch.delenv("SILVER_SCREEN_MODAL_APP", raising=False)
    data = MP4
    if image:
        with BytesIO() as png:
            Image.new("RGB", (16, 16), "orange").save(png, format="PNG")
            data = png.getvalue()
    app = AppTest.from_file(DIRECT_CREATE)
    app.query_params["render"] = RENDER_ID
    app.session_state["direct_create_job"] = {
        "backend": "ComfyUI",
        "model": IMAGE_MODEL if image else CLIP_MODEL,
        "prediction": {"id": RENDER_ID, "status": "succeeded", "archived": True},
        "data": data,
    }

    app.run()

    assert not app.exception
    assert len(app.get("image" if image else "video")) == 1
    downloads = {button.proto.label for button in app.get("download_button")}
    assert ("Download image" if image else "Download original clip") in downloads
    assert not app.get("iframe")


def test_direct_create_shows_failed_modal_render_outside_progress_frame(monkeypatch):
    monkeypatch.setenv("SILVER_SCREEN_HTTP_RENDER_PREVIEW", "1")
    monkeypatch.delenv("SILVER_SCREEN_MODAL_APP", raising=False)
    app = AppTest.from_file(DIRECT_CREATE)
    app.query_params["render"] = RENDER_ID
    app.session_state["direct_create_job"] = {
        "backend": "ComfyUI",
        "model": CLIP_MODEL,
        "prediction": {"id": RENDER_ID, "status": "failed", "error": "The GPU worker reached its time limit."},
    }

    app.run()

    assert not app.exception
    assert [error.value for error in app.error] == ["The GPU worker reached its time limit."]
    assert not app.get("iframe")


def test_recent_render_can_be_opened_directly(monkeypatch):
    other = "fc-" + "2" * 26
    entries = [RecentRender(render_id, timestamp, None, True, True)
               for timestamp, render_id in enumerate((RENDER_ID, other), start=1)]
    with BytesIO() as png:
        Image.new("RGB", (16, 16), "orange").save(png, format="PNG")
        data = png.getvalue()
    client = Mock(spec=modal_renderer.ModalComfyUIClient)
    monkeypatch.setenv("SILVER_SCREEN_HTTP_RENDER_PREVIEW", "1")
    monkeypatch.setattr(modal_renderer, "modal_configured", lambda: True)
    monkeypatch.setattr(modal_renderer, "recent_modal_renders", lambda: entries)
    monkeypatch.setattr(comfyui, "make_comfyui_client", lambda **kwargs: client)
    monkeypatch.setattr(render_preview, "poll_modal_render", lambda render_id, **kwargs: (
        {"id": render_id, "status": "succeeded", "archived": True}, data,
    ))

    app = AppTest.from_file(DIRECT_CREATE).run()
    assert not app.exception
    for entry in entries:
        app.button(key=f"open-recent-render-{entry.render_id}").click().run()
        assert not app.exception
        assert app.session_state["direct_create_job"]["prediction"]["id"] == entry.render_id
        assert len(app.get("image")) == 1
    client.create_prediction.assert_not_called()


def test_deleting_one_render_keeps_another_preview_available(monkeypatch):
    other = "fc-" + "2" * 26
    client = Mock()
    client.get_prediction.side_effect = lambda render_id, **kwargs: {"id": render_id, "status": "succeeded"}
    client.read_output.return_value = MP4
    monkeypatch.setattr(render_preview, "ModalComfyUIClient", lambda: client)
    monkeypatch.setenv("SILVER_SCREEN_HTTP_RENDER_PREVIEW", "1")
    monkeypatch.delenv("SILVER_SCREEN_MODAL_APP", raising=False)
    delete = Mock()
    monkeypatch.setattr(modal_renderer, "delete_modal_render", delete)
    render_preview.clear_render_preview()
    try:
        assert render_preview._render_media(request(render_id=other)).body == MP4
        assert render_preview._render_media(request()).body == MP4
        app = AppTest.from_file(DIRECT_CREATE)
        app.query_params["render"] = RENDER_ID
        app.session_state["direct_create_job"] = {
            "backend": "ComfyUI",
            "model": CLIP_MODEL,
            "prediction": {"id": RENDER_ID, "status": "succeeded", "archived": True},
            "data": MP4,
        }
        app.run()

        def after_delete(render_id, **kwargs):
            if render_id == RENDER_ID:
                return {"id": render_id, "status": "failed", "error": "This render was deleted from Silver Screen."}
            raise OSError("Archive temporarily unavailable.")

        client.get_prediction.side_effect = after_delete
        next(button for button in app.button if button.label == "Delete this clip").click().run()

        assert not app.exception
        delete.assert_called_once_with(RENDER_ID, image=False)
        assert render_preview._render_media(request()).status_code == 404
        response = render_preview._render_media(request(render_id=other, byte_range="bytes=0-1"))
        assert response.status_code == 206 and response.body == MP4[:2]
    finally:
        render_preview.clear_render_preview()


def test_render_can_finish_over_http_without_a_studio_session(monkeypatch):
    client = Mock()
    client.get_prediction.return_value = {"status": "processing"}
    client.read_output.return_value = MP4
    monkeypatch.setattr(render_preview, "ModalComfyUIClient", lambda: client)
    clock = [0]
    monkeypatch.setattr(render_preview, "monotonic", lambda: clock[0])
    render_preview.clear_render_preview()
    try:
        assert render_preview._render_status(request()).body == b'{"status":"processing"}'
        assert render_preview._render_media(request()).status_code == 409

        clock[0] = 5
        client.get_prediction.return_value = {"status": "succeeded", "face_review": {"status": "possible_drift"}}
        ready = json.loads(render_preview._render_status(request()).body)
        assert ready["status"] == "succeeded" and not ready["image"]
        assert ready["face_needs_review"] and "face may have drifted" in ready["face_notice"]
        partial = render_preview._render_media(request(byte_range="bytes=0-1"))
        assert partial.status_code == 206
        assert partial.body == MP4[:2]
        assert partial.headers["content-range"] == f"bytes 0-1/{len(MP4)}"
        download = render_preview._render_media(request(query=b"download=1"))
        assert download.body == MP4
        assert download.headers["content-disposition"] == 'attachment; filename="silver-screen.mp4"'
        head = render_preview._render_media(request("HEAD"))
        assert not head.body
        assert head.headers["content-length"] == str(len(MP4))
        assert client.get_prediction.call_count == 2
    finally:
        render_preview.clear_render_preview()


def test_manual_status_rechecks_cached_progress(monkeypatch):
    client = Mock()
    client.get_prediction.side_effect = [
        {"status": "processing", "progress": {"complete": 0, "total": 2}},
        {"status": "processing", "progress": {"complete": 1, "total": 2}},
    ]
    monkeypatch.setattr(render_preview, "ModalComfyUIClient", lambda: client)
    render_preview.clear_render_preview()
    try:
        first = json.loads(render_preview._render_status(request()).body)
        assert first["progress"]["complete"] == 0
        assert json.loads(render_preview._render_status(request()).body) == first
        updated = json.loads(render_preview._render_status(request(query=b"refresh=1")).body)
        assert updated["progress"] == {"complete": 1, "total": 2}
        assert client.get_prediction.call_count == 2
    finally:
        render_preview.clear_render_preview()


@pytest.mark.parametrize("error", [PredictionPending("Connection interrupted."), OSError("Archive unavailable.")])
def test_transient_render_lookup_retains_the_job_id(monkeypatch, error):
    client = Mock()
    client.get_prediction.side_effect = error
    monkeypatch.setattr(render_preview, "ModalComfyUIClient", lambda: client)
    render_preview.clear_render_preview()
    try:
        prediction, data = render_preview.poll_modal_render(RENDER_ID)
        assert prediction["id"] == RENDER_ID
        assert prediction["status"] == "processing"
        assert prediction["notice"] and data is None
    finally:
        render_preview.clear_render_preview()


def test_unreadable_render_keeps_its_id_when_reporting_failure(monkeypatch):
    client = Mock()
    client.get_prediction.return_value = {"id": RENDER_ID, "status": "succeeded"}
    client.read_output.return_value = b"Not a clip or an image."
    monkeypatch.setattr(render_preview, "ModalComfyUIClient", lambda: client)
    render_preview.clear_render_preview()
    try:
        prediction, data = render_preview.poll_modal_render(RENDER_ID)
        assert prediction["id"] == RENDER_ID
        assert prediction["status"] == "failed" and "unreadable media" in prediction["error"]
        assert data is None
    finally:
        render_preview.clear_render_preview()


def test_slow_status_check_does_not_block_another_render(monkeypatch):
    other = "fc-" + "2" * 26
    release = Event()
    client = Mock()

    def lookup(render_id, **kwargs):
        if render_id == RENDER_ID:
            assert release.wait(6)
            return {"status": "processing"}
        return {"status": "failed", "error": "Render canceled by the operator."}

    client.get_prediction.side_effect = lookup
    monkeypatch.setattr(render_preview, "ModalComfyUIClient", lambda: client)
    render_preview.clear_render_preview()
    try:
        start = monotonic()
        stalled, data = render_preview.poll_modal_render(RENDER_ID)
        assert stalled["id"] == RENDER_ID and data is None
        assert stalled["status"] == "processing" and "Checking with Modal" in stalled["notice"]
        fresh = json.loads(render_preview._render_status(request(render_id=other)).body)
        assert fresh["status"] == "failed" and "canceled" in fresh["error"]
        assert monotonic() - start < 4
    finally:
        release.set()
        render_preview.clear_render_preview()


@pytest.mark.parametrize(("scores", "status"), [
    ([0.95, 0.79, 0.64, 0.83], "matched"),
    ([0.95, 0.64, 0.61, 0.70, None], "possible_drift"),
    ([None, 0.89, None, None], "inconclusive"),
    ([], "inconclusive"),
])
def test_reference_review_distinguishes_drift_from_a_brief_unreadable_face(scores, status):
    review = summarize_face_review(scores, reference_faces=1)
    assert review["status"] == status
    if status == "possible_drift":
        assert review["first_issue_seconds"] == 0.125


def test_saved_face_review_round_trips_with_legacy_hand_only_clips(tmp_path):
    volume = Mock()
    sidecar = tmp_path / f"{RENDER_ID}.json"
    volume.read_file.side_effect = lambda name: iter([(tmp_path / name).read_bytes()])
    hand = {"status": "tracked"}
    face = {"status": "possible_drift", "sampled_frames": 4}
    sidecar.write_text(json.dumps(hand))
    assert read_saved_reviews(volume, RENDER_ID) == {"hand_review": hand}
    save_completed_clip(volume, tmp_path, RENDER_ID, MP4, hand_review=hand, face_review=face)
    assert read_saved_reviews(volume, RENDER_ID) == {"hand_review": hand, "face_review": face}
    assert (tmp_path / f"{RENDER_ID}.mp4").read_bytes() == MP4
    volume.commit.assert_called_once()
