"""Run operator-configured ComfyUI workflows for images and movie shots."""

from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.error
import urllib.request
from math import ceil, floor, log, sqrt
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

from .ai_video import ReplicateVideoClient, VideoGenerationError

COMFYUI_MODEL = "comfyui/workflow"
WORKFLOW_ENV = {
    "image": "SILVER_SCREEN_COMFYUI_IMAGE_WORKFLOW",
    "video": "SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW",
    "reference": "SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW",
}
MAX_RESPONSE = 2 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
WAN_LANDSCAPE_HD = (1280, 704)
WAN_PORTRAIT_HD = (704, 1280)
WAN_14B_LANDSCAPE_HD = (1280, 720)
WAN_14B_MAX_SECONDS = 5
MOTION_REFERENCE_LIMIT = 12 * 1024 * 1024
WAN_14B_FPS = 16
WAN_14B_STEPS = 40
WAN_MOTION_NEGATIVE_PROMPT = (
    "blurry subject, smeared movement, flicker, distorted anatomy, warped objects, "
    "missing hands, wrist stumps, detached hands, fused fingers, extra fingers, extra limbs, morphing faces"
)
COMPACT_VIDEO_SIZES = {
    "Landscape · 832 × 480": (832, 480),
    "Portrait · 480 × 832": (480, 832),
}
VIDEO_SIZES = {
    "Landscape HD · 1280 × 704": WAN_LANDSCAPE_HD,
    "Portrait HD · 704 × 1280": WAN_PORTRAIT_HD,
    **COMPACT_VIDEO_SIZES,
}
WAN_14B_VIDEO_SIZES = {
    "Landscape HD · 1280 × 720": WAN_14B_LANDSCAPE_HD,
    "Portrait HD · 720 × 1280": WAN_14B_LANDSCAPE_HD[::-1],
    **COMPACT_VIDEO_SIZES,
}
VIDEO_CONTROL_MARKERS = {"__WIDTH__", "__HEIGHT__", "__STEPS__", "__NEGATIVE_PROMPT__"}
WAN_14B_SPLIT_MARKER = "__WAN_14B_SPLIT_STEP__"


def _wan_frame_limits(wan_14b: bool) -> tuple[int, int]:
    width, height = WAN_14B_LANDSCAPE_HD if wan_14b else WAN_LANDSCAPE_HD
    return (16 if wan_14b else 32), width * height


def wan_reference_size(width: int, height: int, *, wan_14b: bool) -> tuple[int, int]:
    """Choose the nearest supported aspect ratio within the HD pixel budget."""
    stride, pixel_limit = _wan_frame_limits(wan_14b)
    scale = min(1280 / max(width, height), sqrt(pixel_limit / (width * height)))
    widths, heights = (
        {max(256, round_down_or_up(side * scale / stride) * stride)
         for round_down_or_up in (floor, ceil)}
        for side in (width, height)
    )
    candidates = (
        (w, h) for w in widths for h in heights if w * h <= pixel_limit and max(w, h) <= 1280
    )
    return min(
        candidates, key=lambda size: (abs(log((size[0] / size[1]) / (width / height))), -size[0] * size[1]),
    )


def _valid_wan_reference_size(size: tuple[int, int], *, wan_14b: bool) -> bool:
    width, height = size
    stride, pixel_limit = _wan_frame_limits(wan_14b)
    return width * height <= pixel_limit and all(256 <= side <= 1280 and side % stride == 0 for side in (width, height))


def _wan_14b_split_step(steps: int, *, boundary: float = 0.9) -> int:
    # ComfyUI's simple scheduler samples 1,000 shifted timesteps from high to low.
    # Switch experts at the first shifted timestep below the model's boundary.
    for step in range(1, steps):
        time = (1000 - step * 1000 // steps) / 1000
        if 5 * time / (1 + 4 * time) < boundary:
            return step
    return steps - 1


def video_backend() -> str:
    return os.getenv("SILVER_SCREEN_VIDEO_BACKEND", "replicate").strip().lower()


def comfyui_ready(kind: str) -> bool:
    from .modal_renderer import modal_configured

    endpoint_ready = modal_configured() if os.getenv("SILVER_SCREEN_MODAL_APP") else bool(
        os.getenv("SILVER_SCREEN_COMFYUI_URL", "").strip()
    )
    return endpoint_ready and bool(os.getenv(WORKFLOW_ENV[kind], "").strip())


def make_comfyui_client(*, kind: str = "video") -> ComfyUIClient:
    if os.getenv("SILVER_SCREEN_MODAL_APP"):
        from .modal_renderer import ModalComfyUIClient

        return ModalComfyUIClient(kind=kind)
    return ComfyUIClient(kind=kind)


def video_backend_ready(backend: str | None = None) -> bool:
    backend = backend or video_backend()
    if backend == "comfyui":
        return comfyui_ready("video")
    return backend == "replicate" and bool(os.getenv("REPLICATE_API_TOKEN", "").strip())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _workflow(kind: str) -> dict[str, Any]:
    setting = WORKFLOW_ENV[kind]
    configured = os.getenv(setting, "").strip()
    if not configured:
        raise VideoGenerationError(f"Set {setting} to a ComfyUI API workflow JSON object or file path.")
    try:
        source = configured if configured.startswith("{") else Path(configured).read_text(encoding="utf-8")
        workflow = json.loads(source)
    except (OSError, ValueError) as exc:
        raise VideoGenerationError(f"Could not read the API workflow configured in {setting}.") from exc
    if not isinstance(workflow, dict) or not workflow or any(
        not isinstance(node, dict)
        or not isinstance(node.get("class_type"), str)
        or not isinstance(node.get("inputs"), dict)
        for node in workflow.values()
    ):
        raise VideoGenerationError(f"Export {setting} in ComfyUI API format.")
    return workflow


def _markers(workflow: dict[str, Any]) -> set[str]:
    return {value for node in workflow.values() for value in node["inputs"].values() if isinstance(value, str)}


def video_controls_ready(kind: str) -> bool:
    if not comfyui_ready(kind):
        return False
    try:
        return VIDEO_CONTROL_MARKERS <= _markers(_workflow(kind))
    except VideoGenerationError:
        return False


class ComfyUIClient:
    model = COMFYUI_MODEL
    timeout_seconds = 1200
    poll_seconds = 3.0
    video_sizes = VIDEO_SIZES

    def __init__(self, *, kind: str = "video") -> None:
        self.kind = kind
        self.base_url = os.getenv("SILVER_SCREEN_COMFYUI_URL", "").strip().rstrip("/")
        url = urlsplit(self.base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.query or url.fragment:
            raise VideoGenerationError("Set SILVER_SCREEN_COMFYUI_URL to the ComfyUI server URL.")
        self.token = os.getenv("SILVER_SCREEN_COMFYUI_TOKEN", "").strip()
        if self.token and url.scheme != "https":
            raise VideoGenerationError("Use HTTPS when supplying a ComfyUI access token.")
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.supports_reference = kind == "video" and comfyui_ready("reference")

    def _request(self, route: str, data: bytes | None = None, *, content_type: str = "application/json", limit: int = MAX_RESPONSE) -> bytes:
        headers = {"Content-Type": content_type}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.base_url + route, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=75) as response:
                result = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise VideoGenerationError(f"ComfyUI returned HTTP {exc.code}: {exc.read(600).decode(errors='replace')}") from exc
        except OSError as exc:
            raise VideoGenerationError(f"Could not reach ComfyUI: {exc}") from exc
        if len(result) > limit:
            raise VideoGenerationError("ComfyUI returned more data than Silver Screen can retain.")
        return result

    def _json(self, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            result = json.loads(self._request(route, data))
        except ValueError as exc:
            raise VideoGenerationError("ComfyUI did not return valid JSON.") from exc
        if not isinstance(result, dict):
            raise VideoGenerationError("ComfyUI returned an unexpected response.")
        return result

    def _upload_image(self, image: str) -> str:
        header, _, encoded = image.partition(",")
        if not header.startswith("data:image/") or not header.endswith(";base64"):
            raise VideoGenerationError("ComfyUI requires an uploaded reference image.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise VideoGenerationError("The ComfyUI reference image is invalid.") from exc
        boundary = secrets.token_hex(24)
        filename = f"{uuid4().hex}.jpg"
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        body = prefix + data + f"\r\n--{boundary}--\r\n".encode()
        response = self._request("/upload/image", body, content_type=f"multipart/form-data; boundary={boundary}")
        try:
            upload = json.loads(response)
            name = upload["name"]
            if not isinstance(name, str) or not name:
                raise ValueError("Missing upload name")
            subfolder = upload.get("subfolder") or ""
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise VideoGenerationError("ComfyUI did not acknowledge the reference image.") from exc
        return f"{subfolder}/{name}" if subfolder else name

    def create_prediction(
        self,
        prompt: str,
        *,
        seed: int,
        duration: int = 8,
        image: str | None = None,
        end_image: str | None = None,
        generate_audio: bool | None = None,
        video_size: tuple[int, int] | None = None,
        steps: int | None = None,
        negative_prompt: str = "",
        control_video_name: str | None = None,
    ) -> dict[str, Any]:
        if not prompt.strip():
            raise VideoGenerationError("Enter a prompt before generating.")
        kind = "reference" if image else self.kind
        workflow = _workflow(kind)
        markers = _markers(workflow)
        wan_14b = WAN_14B_SPLIT_MARKER in markers
        if end_image:
            conditioning = [node for node in workflow.values() if node["class_type"] == "WanImageToVideo"]
            if not image or not wan_14b or len(conditioning) != 1:
                raise VideoGenerationError("An ending image requires the Wan 2.2 14B image-to-video workflow.")
            # The same A14B models support ComfyUI's start/end-frame conditioning.
            end_node = str(max((int(key) for key in workflow if key.isdecimal()), default=0) + 1)
            conditioning[0]["class_type"] = "WanFirstLastFrameToVideo"
            conditioning[0]["inputs"]["end_image"] = [end_node, 0]
            workflow[end_node] = {"class_type": "LoadImage", "inputs": {"image": "__END_IMAGE__"}}
        replacements: dict[str, Any] = {"__PROMPT__": prompt, "__SEED__": seed}
        required = {"__PROMPT__"}
        if control_video_name:
            required.add("__CONTROL_VIDEO__")
            replacements["__CONTROL_VIDEO__"] = control_video_name
        if kind != "image":
            if video_size is not None and not (
                video_size in self.video_sizes.values()
                or image and _valid_wan_reference_size(video_size, wan_14b=wan_14b)
            ):
                raise VideoGenerationError("Choose a supported Wan video size.")
            max_steps = WAN_14B_STEPS if wan_14b else 32
            if steps is not None and not 12 <= steps <= max_steps:
                raise VideoGenerationError(f"Choose between 12 and {max_steps} sampling steps.")
            try:
                fps = int(os.getenv("SILVER_SCREEN_COMFYUI_FPS", "24"))
                if fps not in range(4, 61, 4):
                    raise ValueError
            except ValueError as exc:
                raise VideoGenerationError("Set SILVER_SCREEN_COMFYUI_FPS to a multiple of 4 between 4 and 60.") from exc
            width, height = video_size or (832, 480)
            sampling_steps = steps or (WAN_14B_STEPS if wan_14b else 20)
            replacements.update(__FRAMES__=duration * fps + 1, __FPS__=fps,
                                __WIDTH__=width, __HEIGHT__=height, __STEPS__=sampling_steps,
                                __SPLIT_STEP__=sampling_steps // 2,
                                __NEGATIVE_PROMPT__=negative_prompt)
            if wan_14b:
                replacements[WAN_14B_SPLIT_MARKER] = _wan_14b_split_step(
                    sampling_steps, boundary=0.875 if kind == "video" else 0.9,
                )
            required.add("__FRAMES__")
            if video_size is not None or steps is not None or negative_prompt:
                required.update(VIDEO_CONTROL_MARKERS)
        if image:
            required.add("__IMAGE__")
        inputs = [node["inputs"] for node in workflow.values()]
        if missing := required - markers:
            raise VideoGenerationError("Missing ComfyUI workflow inputs " + ", ".join(sorted(missing)) + ".")
        if image:
            replacements["__IMAGE__"] = self._upload_image(image)
        if end_image:
            replacements["__END_IMAGE__"] = self._upload_image(end_image)
        for fields in inputs:
            for key, value in fields.items():
                if isinstance(value, str) and value in replacements:
                    fields[key] = replacements[value]
        result = self._json("/prompt", {"prompt": workflow})
        prediction_id = result.get("prompt_id")
        if not isinstance(prediction_id, str) or not prediction_id:
            raise VideoGenerationError("ComfyUI did not accept the workflow.")
        return {"id": prediction_id, "status": "starting", "model": self.model}

    def get_prediction(self, prediction_id: str, prediction_url: str | None = None) -> dict[str, Any]:
        history = self._json(f"/history/{quote(prediction_id, safe='')}")
        entry = history.get(prediction_id)
        result = {"id": prediction_id, "status": "processing", "model": self.model}
        if not isinstance(entry, dict):
            return result
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            messages = status.get("messages") or []
            detail = next(
                (item[1].get("exception_message") for item in messages
                 if isinstance(item, list) and len(item) == 2 and item[0] == "execution_error"
                 and isinstance(item[1], dict)),
                None,
            )
            return dict(result, status="failed", error=detail or "ComfyUI workflow failed.")
        if not status.get("completed"):
            return result

        extension = ".png" if self.kind == "image" else ".mp4"
        for output in (entry.get("outputs") or {}).values():
            for key in ("images", "videos", "gifs"):
                for artifact in output.get(key) or []:
                    if (
                        isinstance(artifact, dict)
                        and artifact.get("type") == "output"
                        and str(artifact.get("filename") or "").lower().endswith(extension)
                    ):
                        return dict(result, status="succeeded", output=artifact)
        return dict(result, status="failed", error=f"ComfyUI completed without a saved {extension} file.")

    # Both providers use the same prediction states and checkpoint callback.
    wait = ReplicateVideoClient.wait

    def read_output(self, prediction: dict[str, Any]) -> bytes:
        output = prediction["output"]
        query = urlencode({"filename": output["filename"], "subfolder": output.get("subfolder", ""), "type": "output"})
        return self._request(f"/view?{query}", limit=MAX_OUTPUT)

    def download_output(self, prediction: dict[str, Any], destination: Path) -> Path:
        data = self.read_output(prediction)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination
