"""Direct BytePlus ModelArk video generation and resumable task polling."""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import NoReturn
from urllib.parse import urlsplit

from PIL import Image, ImageOps

from .ai_video import PredictionPending, ReplicateVideoClient, VideoGenerationError

SEEDANCE_MODEL = "dreamina-seedance-2-5-260628"
SEEDANCE_LABEL = "Seedance 2.5"
SEEDANCE_BACKEND = "seedance"
SEEDANCE_TASKS_URL = "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"
SEEDANCE_TASK_ID = re.compile(r"cgt-[A-Za-z0-9][A-Za-z0-9-]{1,87}")
SEEDANCE_ASSET_ID = re.compile(r"asset-[A-Za-z0-9][A-Za-z0-9-]{1,87}")
SEEDANCE_PORTRAIT_GUIDE = "https://docs.byteplus.com/en/docs/modelark/upload-real-person-portrait-assets"
SEEDANCE_SIZES = {"720p": (1280, 720), "1080p": (1920, 1080)}
_RATES = {"720p": 10.70, "1080p": 11.70}  # USD per million tokens, text or image input
MAX_SEEDANCE_MP4 = 64 * 1024 * 1024


class SeedanceRequestRejected(VideoGenerationError):
    """The provider declined the request, so repeating it requires a change."""


class SeedanceSubmissionUnknown(VideoGenerationError):
    """The provider may have accepted a request without returning a task ID."""


def seedance_configured() -> bool:
    return bool(os.getenv("ARK_API_KEY", "").strip())


def seedance_frame_size(resolution: str, ratio: str) -> tuple[int, int]:
    if resolution not in SEEDANCE_SIZES or ratio not in {"16:9", "9:16"}:
        raise SeedanceRequestRejected("Choose 720p or 1080p in 16:9 or 9:16 for Seedance.")
    size = SEEDANCE_SIZES[resolution]
    return size[::-1] if ratio == "9:16" else size


def seedance_cost_per_second(resolution: str) -> float:
    """Estimate from BytePlus's published 24 fps, pixel-based token formula."""
    width, height = SEEDANCE_SIZES[resolution]
    return width * height * 24 / 1024 * _RATES[resolution] / 1_000_000


def seedance_portrait_asset(value: str) -> str:
    asset_id = value.strip().removeprefix("asset://")
    if asset_id and not SEEDANCE_ASSET_ID.fullmatch(asset_id):
        raise SeedanceRequestRejected("Enter a ModelArk image asset ID beginning with asset-.")
    return asset_id


def _starting_image(image: str, size: tuple[int, int]) -> str:
    """Fit the composition without stretching it. Seedance follows the first frame's ratio."""
    try:
        _, payload = image.split(",", 1)
        with Image.open(BytesIO(base64.b64decode(payload, validate=True))) as source:
            source = ImageOps.exif_transpose(source).convert("RGB")
            frame = ImageOps.pad(source, size, Image.Resampling.LANCZOS, color="black")
        encoded = BytesIO()
        frame.save(encoded, format="JPEG", quality=94, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise SeedanceRequestRejected("The starting image could not be prepared for Seedance.") from exc
    return "data:image/jpeg;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")


def _error_code(error: object) -> str:
    value = error.get("code") if isinstance(error, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,120}", value) else ""


def _provider_error(code: str) -> str:
    lowered = code.casefold()
    suffix = f" ({code})" if code else ""
    if "realperson" in lowered or "portrait" in lowered or "face" in lowered:
        return "BytePlus portrait moderation requires an authorized ModelArk image asset for a real person." + suffix
    if any(part in lowered for part in ("sensitive", "security", "moderation", "policy")):
        return "BytePlus rejected the request under its content moderation rules." + suffix
    return "BytePlus returned a video generation error" + suffix + "."


def _delivery_allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        return (
            parts.scheme == "https"
            and not parts.username
            and not parts.password
            and parts.port in (443, None)
            and host.endswith((".bytepluses.com", ".volces.com", ".bytepluscdn.com", ".byteplus.com"))
        )
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DeliveryRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _delivery_allowed(newurl):
            raise VideoGenerationError("BytePlus redirected the clip outside its delivery service.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SeedanceVideoClient(ReplicateVideoClient):
    """Adapt ModelArk tasks to the studio's prediction and polling interface."""

    supports_reference = True
    model = SEEDANCE_MODEL

    def __init__(self, *, timeout_seconds: int = 180, poll_seconds: float = 5.0) -> None:
        self.token = os.getenv("ARK_API_KEY", "").strip()
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        if not self.token:
            raise SeedanceRequestRejected("Add ARK_API_KEY to Silver Screen's deployment to enable Seedance 2.5.")

    def _task_request(self, method: str, url: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=body, headers=self.headers, method=method)
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
                parsed = json.loads(response.read(1024 * 1024))
                if not isinstance(parsed, dict):
                    raise ValueError("Unexpected task response")
                return parsed
        except urllib.error.HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return {"status": "failed", "error": {"code": "TaskNotFound"}}
            if exc.code not in {408, 409} and 400 <= exc.code < 500:
                if exc.code in {401, 403}:
                    message = "BytePlus refused access. Check the API key, model activation, and account balance."
                elif exc.code == 429:
                    message = "BytePlus is rate limiting Seedance. Check again shortly."
                else:
                    try:
                        error = json.loads(exc.read(8192))
                        code = _error_code(error.get("error")) if isinstance(error, dict) else ""
                    except (OSError, ValueError):
                        code = ""
                    message = _provider_error(code) if code else f"BytePlus declined the request (HTTP {exc.code})."
                error_type = PredictionPending if method == "GET" else SeedanceRequestRejected
                raise error_type(message) from exc
            self._uncertain_request(method, exc)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            self._uncertain_request(method, exc)

    @staticmethod
    def _uncertain_request(method: str, cause: Exception | None = None) -> NoReturn:
        if method == "POST":
            raise SeedanceSubmissionUnknown(
                "BytePlus may have accepted the render, but did not return a task ID. "
                "Check the ModelArk task list before submitting again."
            ) from cause
        raise PredictionPending("BytePlus status is temporarily unavailable. Check the same render again.") from cause

    def create_prediction(
        self, prompt: str, *, seed: int, duration: int = 6, image: str | None = None,
        generate_audio: bool | None = None, ratio: str = "16:9", resolution: str = "720p",
        portrait_asset: str = "",
    ) -> dict:
        if type(duration) is not int or not 4 <= duration <= 30:
            raise SeedanceRequestRejected("Seedance 2.5 supports clips from 4 to 30 seconds.")
        if not prompt.strip() or len(prompt) > 14500:
            raise SeedanceRequestRejected("Enter a prompt of up to 14,500 characters for Seedance.")
        size = seedance_frame_size(resolution, ratio)
        asset_id = seedance_portrait_asset(portrait_asset)
        content = [{"type": "text", "text": prompt}]
        if asset_id:
            content[0]["text"] = (
                "When the lead appears, match the person in @image1 and preserve their appearance. " + prompt
            )
            content.append({"type": "image_url", "role": "reference_image", "image_url": {"url": "asset://" + asset_id}})
        if image:
            role = "reference_image" if asset_id else "first_frame"
            if asset_id:
                content[0]["text"] += " Use @image2 as the composition and lighting reference."
            else:
                ratio = "adaptive"
            content.append({"type": "image_url", "role": role, "image_url": {"url": _starting_image(image, size)}})
        payload = {
            "model": self.model, "content": content, "ratio": ratio, "resolution": resolution, "duration": duration,
            "seed": seed & 0x7FFFFFFF, "generate_audio": True if generate_audio is None else generate_audio,
            "output_format": "mp4",
        }
        result = self._task_request("POST", SEEDANCE_TASKS_URL, payload)
        task_id = result.get("id")
        if not isinstance(task_id, str) or not SEEDANCE_TASK_ID.fullmatch(task_id):
            self._uncertain_request("POST")
        return {"id": task_id, "status": "starting", "model": self.model}

    def get_prediction(self, prediction_id: str, prediction_url: str | None = None) -> dict:
        if not SEEDANCE_TASK_ID.fullmatch(prediction_id):
            raise SeedanceRequestRejected("Enter the full BytePlus task ID beginning with cgt-.")
        result = self._task_request("GET", f"{SEEDANCE_TASKS_URL}/{prediction_id}")
        if result.get("id", prediction_id) != prediction_id or result.get("model", self.model) != self.model:
            raise SeedanceRequestRejected("The task does not match a Seedance 2.5 render.")
        status = {
            "queued": "starting", "running": "processing", "succeeded": "succeeded",
            "failed": "failed", "cancelled": "canceled", "expired": "failed",
        }.get(result.get("status"))
        if status is None:
            raise PredictionPending("BytePlus did not return a recognized status. Check the same render again.")
        prediction = {"id": prediction_id, "model": self.model, "status": status}
        if status in {"failed", "canceled"}:
            code = _error_code(result.get("error"))
            prediction["error"] = (
                "This BytePlus task was not found or has expired." if code == "TaskNotFound" or result.get("status") == "expired"
                else _provider_error(code)
            )
        if status == "succeeded":
            content = result.get("content") or {}
            prediction["output"] = content.get("video_url") if isinstance(content, dict) else None
            usage = result.get("usage") or {}
            tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
            resolution = result.get("resolution")
            if type(tokens) is int and tokens > 0 and resolution in _RATES:
                prediction["metrics"] = {
                    "completionTokens": tokens, "estimatedCostUsd": tokens * _RATES[resolution] / 1_000_000,
                }
            created, updated = result.get("created_at"), result.get("updated_at")
            if type(created) is int and type(updated) is int and updated >= created > 0:
                prediction.setdefault("metrics", {})["providerSeconds"] = updated - created
        return prediction

    def download_output(self, prediction: dict, destination: Path) -> Path:
        output = prediction.get("output")
        if not isinstance(output, str) or not _delivery_allowed(output):
            raise VideoGenerationError("BytePlus did not return a supported clip download URL.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A download failure leaves the task ID available for another check.
        # Signed delivery URLs do not receive the ModelArk authorization header.
        opener = urllib.request.build_opener(_DeliveryRedirects())
        with NamedTemporaryFile(dir=destination.parent, prefix=".seedance-", delete=False) as file:
            staged = Path(file.name)
            try:
                try:
                    with opener.open(output, timeout=60) as response:
                        remaining = MAX_SEEDANCE_MP4
                        while chunk := response.read(1024 * 1024):
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise VideoGenerationError("The Seedance clip exceeds the studio's 64 MiB download limit.")
                            file.write(chunk)
                except urllib.error.HTTPError as exc:
                    if exc.code in {401, 403, 404, 410}:
                        raise VideoGenerationError("The BytePlus clip link has expired or is no longer available.") from exc
                    raise PredictionPending("The Seedance clip is ready. Check again to retry its download.") from exc
                except (OSError, http.client.HTTPException) as exc:
                    raise PredictionPending("The Seedance clip is ready. Check again to retry its download.") from exc
                file.close()
                staged.replace(destination)
            finally:
                staged.unlink(missing_ok=True)
        return destination
