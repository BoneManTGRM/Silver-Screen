from unittest.mock import Mock

import pytest

pytest.importorskip("starlette")
from starlette.requests import Request

from silver_screen import render_preview


RENDER_ID = "fc-" + "1" * 26
MP4 = b"\x00\x00\x00\x20ftypisom" + b"frame data"


def request(method="GET", *, byte_range=None, query=b""):
    headers = [(b"range", byte_range.encode())] if byte_range else []
    return Request({
        "type": "http",
        "method": method,
        "path_params": {"render_id": RENDER_ID},
        "headers": headers,
        "query_string": query,
    })


def test_render_can_finish_over_http_without_a_studio_session(monkeypatch):
    client = Mock()
    client.get_prediction.return_value = {"status": "processing"}
    client.read_output.return_value = MP4
    monkeypatch.setattr(render_preview, "ModalComfyUIClient", lambda: client)
    clock = iter((0, 0, 5, 5, 5, 5))
    monkeypatch.setattr(render_preview, "monotonic", lambda: next(clock))
    render_preview.clear_render_preview()
    try:
        assert render_preview._render_status(request()).body == b'{"status":"processing"}'
        assert render_preview._render_media(request()).status_code == 409

        client.get_prediction.return_value = {"status": "succeeded"}
        assert render_preview._render_status(request()).body == b'{"status":"succeeded","image":false}'
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
