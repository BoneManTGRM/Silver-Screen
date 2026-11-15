"""Serve existing Modal renders over authenticated, same-origin HTTP."""

import re
from functools import lru_cache
from pathlib import Path
from string import Template
from threading import Lock
from time import monotonic

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .ai_video import PredictionPending, VideoGenerationError
from .modal_clip_archive import MODAL_RENDER_ID
from .modal_renderer import ModalComfyUIClient


_TEMPLATE = Template(Path(__file__).with_name("render_preview.html").read_text(encoding="utf-8"))
_PRIVATE = {"Cache-Control": "private, no-store", "Cross-Origin-Resource-Policy": "same-origin"}
_LOCK = Lock()
_RANGE = re.compile(r"bytes=(\d{0,20})-(\d{0,20})")


def render_preview_html(render_id: str, *, standalone: bool = False) -> str:
    if not MODAL_RENDER_ID.fullmatch(render_id):
        raise ValueError("Invalid Modal render ID.")
    return _TEMPLATE.substitute(
        render_id=render_id,
        mode="standalone" if standalone else "inline",
    )


@lru_cache(maxsize=1)
def _poll_modal_render(render_id: str, interval: int) -> tuple[dict, bytes | None]:
    # Share one short-lived result between the UI poll and the player's byte requests.
    client = ModalComfyUIClient()
    prediction = client.get_prediction(render_id, prefer_saved=True)
    if prediction["status"] == "succeeded":
        data = client.read_output(prediction)
        if data.startswith(b"\x89PNG\r\n\x1a\n") or data[4:8] == b"ftyp":
            return prediction, data
        return {"status": "failed", "error": "Modal returned an unreadable media file."}, None
    return prediction, None


def poll_modal_render(render_id: str) -> tuple[dict, bytes | None]:
    with _LOCK:
        return _poll_modal_render(render_id, int(monotonic() / 5))


def clear_render_preview() -> None:
    with _LOCK:
        _poll_modal_render.cache_clear()


def _render_page(request: Request) -> Response:
    render_id = request.path_params["render_id"]
    if not MODAL_RENDER_ID.fullmatch(render_id):
        return Response(status_code=404, headers=_PRIVATE)
    return HTMLResponse(render_preview_html(render_id, standalone=True), headers=_PRIVATE)


def _read_render(request: Request) -> tuple[dict, bytes | None]:
    render_id = request.path_params["render_id"]
    if not MODAL_RENDER_ID.fullmatch(render_id):
        return {"status": "missing", "error": "This render link is invalid."}, None
    try:
        return poll_modal_render(render_id)
    except PredictionPending as exc:
        return {"status": "processing", "notice": str(exc)}, None
    except (OSError, VideoGenerationError):
        return {"status": "processing", "notice": "Could not check Modal. Retrying shortly."}, None


def _render_status(request: Request) -> Response:
    prediction, data = _read_render(request)
    result = {
        key: prediction[key] for key in ("status", "error", "warning", "notice")
        if key in prediction
    }
    if data is not None:
        result["image"] = data.startswith(b"\x89PNG\r\n\x1a\n")
    return JSONResponse(result, status_code=404 if result["status"] == "missing" else 200, headers=_PRIVATE)


def _render_media(request: Request) -> Response:
    prediction, data = _read_render(request)
    if data is None:
        status = 404 if prediction["status"] in {"failed", "canceled", "missing"} else 409
        return Response(status_code=status, headers=_PRIVATE)
    image = data.startswith(b"\x89PNG\r\n\x1a\n")
    media_type = "image/png" if image else "video/mp4"
    headers = {**_PRIVATE, "Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff"}
    if request.query_params.get("download") == "1":
        filename = "silver-screen.png" if image else "silver-screen.mp4"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    size = len(data)
    if request.method == "HEAD":
        return Response(media_type=media_type, headers={**headers, "Content-Length": str(size)})
    if interval := _RANGE.fullmatch(request.headers.get("range", "")):
        start_text, end_text = interval.groups()
        if start_text or end_text:
            if start_text:
                start = int(start_text)
                end = min(int(end_text), size - 1) if end_text else size - 1
            else:
                start, end = max(0, size - int(end_text)), size - 1
            if not start <= end:
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            return Response(data[start:end + 1], 206, headers=headers, media_type=media_type)
    return Response(data, headers=headers, media_type=media_type)


render_preview_routes = [
    Route("/renders/{render_id}", _render_page),
    Route("/renders/{render_id}/status", _render_status),
    Route("/renders/{render_id}/media", _render_media),
]
