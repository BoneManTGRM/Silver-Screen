"""Serve existing Modal renders over authenticated, same-origin HTTP."""

import re
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from string import Template
from threading import Lock
from time import monotonic

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .ai_video import PredictionPending, VideoGenerationError
from .face_review import face_review_message
from .hand_review import body_review_message
from .modal_clip_archive import MODAL_RENDER_ID
from .modal_renderer import ModalComfyUIClient


_TEMPLATE = Template(Path(__file__).with_name("render_preview.html").read_text(encoding="utf-8"))
_PRIVATE = {"Cache-Control": "private, no-store", "Cross-Origin-Resource-Policy": "same-origin"}
_LOCK = Lock()
_RANGE = re.compile(r"bytes=(\d{0,20})-(\d{0,20})")
_MAX_CHECKS = 2
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_CHECKS, thread_name_prefix="render-status")
_CHECKS: dict[str, Future] = {}
_CACHE: OrderedDict[str, tuple[float, tuple[dict, bytes | None]]] = OrderedDict()


def render_preview_html(render_id: str, *, standalone: bool = False) -> str:
    if not MODAL_RENDER_ID.fullmatch(render_id):
        raise ValueError("Invalid Modal render ID.")
    return _TEMPLATE.substitute(
        render_id=render_id,
        mode="standalone" if standalone else "inline",
    )


def _retrieve_render(render_id: str) -> tuple[dict, bytes | None]:
    try:
        client = ModalComfyUIClient()
        prediction = client.get_prediction(render_id, prefer_saved=True)
        if prediction["status"] == "succeeded":
            data = client.read_output(prediction)
            if data.startswith(b"\x89PNG\r\n\x1a\n") or data[4:8] == b"ftyp":
                return prediction, data
            return {"status": "failed", "error": "Modal returned an unreadable media file."}, None
        return prediction, None
    except PredictionPending as exc:
        return {"status": "processing", "notice": str(exc)}, None
    except (OSError, VideoGenerationError):
        return {"status": "processing", "notice": "Modal could not respond. The same render will be checked again."}, None


def _store_check(render_id: str, check: Future) -> None:
    with _LOCK:
        if _CHECKS.get(render_id) is not check:
            return
        del _CHECKS[render_id]
        if check.cancelled() or check.exception() is not None:
            return
        result = check.result()
        ttl = 60 if result[1] is not None else 3
        _CACHE[render_id] = (monotonic() + ttl, result)
        _CACHE.move_to_end(render_id)
        if len(_CACHE) > _MAX_CHECKS:
            _CACHE.popitem(last=False)


def poll_modal_render(render_id: str, *, refresh: bool = False) -> tuple[dict, bytes | None]:
    started = False
    with _LOCK:
        cached = _CACHE.get(render_id)
        if cached and monotonic() < cached[0] and (not refresh or cached[1][1] is not None):
            _CACHE.move_to_end(render_id)
            return cached[1]
        check = _CHECKS.get(render_id)
        if check is None and len(_CHECKS) < _MAX_CHECKS:
            check = _EXECUTOR.submit(_retrieve_render, render_id)
            _CHECKS[render_id] = check
            started = True
    if check is not None:
        if started:
            check.add_done_callback(lambda complete: _store_check(render_id, complete))
        try:
            result = check.result(timeout=1.5)
        except TimeoutError:
            pass
        else:
            _store_check(render_id, check)
            return result
    if cached and cached[1][0].get("progress"):
        return cached[1]
    return {"status": "processing", "notice": "Checking with Modal. You can leave this page open."}, None


def clear_render_preview() -> None:
    with _LOCK:
        _CACHE.clear()
        _CHECKS.clear()


def _render_page(request: Request) -> Response:
    render_id = request.path_params["render_id"]
    if not MODAL_RENDER_ID.fullmatch(render_id):
        return Response(status_code=404, headers=_PRIVATE)
    return HTMLResponse(render_preview_html(render_id, standalone=True), headers=_PRIVATE)


def _read_render(request: Request) -> tuple[dict, bytes | None]:
    render_id = request.path_params["render_id"]
    if not MODAL_RENDER_ID.fullmatch(render_id):
        return {"status": "missing", "error": "This render link is invalid."}, None
    return poll_modal_render(render_id, refresh=request.query_params.get("refresh") == "1")


def _render_status(request: Request) -> Response:
    prediction, data = _read_render(request)
    result = {
        key: prediction[key] for key in ("status", "error", "warning", "notice", "progress", "render_stats")
        if key in prediction
    }
    if data is not None:
        result["image"] = data.startswith(b"\x89PNG\r\n\x1a\n")
        if review := prediction.get("face_review"):
            result["face_notice"] = face_review_message(review)
            result["face_needs_review"] = review.get("status") in {"possible_drift", "inconclusive", "unavailable"}
        if review := prediction.get("body_review"):
            result["body_notice"] = body_review_message(review)
            result["body_needs_review"] = review.get("status") in {"possible_distortion", "inconclusive", "unavailable"}
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
