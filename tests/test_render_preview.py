from unittest.mock import Mock
import json
from threading import Event
from time import monotonic

import pytest

pytest.importorskip("starlette")
from starlette.requests import Request

from silver_screen import render_preview
from silver_screen.face_review import summarize_face_review
from silver_screen.modal_clip_archive import read_saved_reviews, save_completed_clip


RENDER_ID = "fc-" + "1" * 26
MP4 = b"\x00\x00\x00\x20ftypisom" + b"frame data"


def request(method="GET", *, byte_range=None, query=b"", render_id=RENDER_ID):
    headers = [(b"range", byte_range.encode())] if byte_range else []
    return Request({
        "type": "http",
        "method": method,
        "path_params": {"render_id": render_id},
        "headers": headers,
        "query_string": query,
    })


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
        stalled = json.loads(render_preview._render_status(request()).body)
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
