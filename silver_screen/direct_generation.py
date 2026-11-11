"""Standalone image and clip generation using the creator's prompt."""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from .ai_video import DEFAULT_MODEL, ReplicateVideoClient, VideoGenerationError, verify_mp4

IMAGE_MODEL = "black-forest-labs/flux-schnell"
CLIP_MODEL = DEFAULT_MODEL
OUTPUT_LIMIT = 64 * 1024 * 1024


def submit_direct_generation(
    client: ReplicateVideoClient,
    prompt: str,
    *,
    aspect_ratio: str,
    duration: int = 8,
    generate_audio: bool = True,
) -> dict[str, Any]:
    if not prompt.strip():
        raise VideoGenerationError("Enter a prompt before generating.")

    inputs: dict[str, Any] = {"prompt": prompt, "aspect_ratio": aspect_ratio}
    if client.model == IMAGE_MODEL:
        inputs["output_format"] = "png"
    elif client.model == CLIP_MODEL:
        inputs.update(duration=duration, resolution="720p", generate_audio=generate_audio)
    else:
        raise VideoGenerationError("This model is unavailable in Direct Create.")

    endpoint = f"https://api.replicate.com/v1/models/{client.model}/predictions"
    return client._request_json("POST", endpoint, {"input": inputs})


def _delivery_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "replicate.delivery" or host.endswith(".replicate.delivery")
    )


class _DeliveryRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _delivery_url(newurl):
            raise VideoGenerationError("Replicate redirected the output outside its delivery service.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_direct_output(prediction: dict[str, Any], *, image: bool) -> bytes:
    output = prediction.get("output")
    if isinstance(output, list):
        output = next((item for item in output if isinstance(item, str)), None)
    if not isinstance(output, str) or not _delivery_url(output):
        raise VideoGenerationError("Replicate did not return a downloadable output.")

    with urllib.request.build_opener(_DeliveryRedirects()).open(output, timeout=60) as response:
        data = response.read(OUTPUT_LIMIT + 1)
    if not data or len(data) > OUTPUT_LIMIT:
        raise VideoGenerationError("The generated file is empty or exceeds the 64 MiB download limit.")

    if image:
        with Image.open(io.BytesIO(data), formats=["PNG"]) as generated:
            generated.verify()
    else:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(data)
            verify_mp4(path)
    return data
