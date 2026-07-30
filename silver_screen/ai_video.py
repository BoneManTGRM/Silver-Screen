"""Real AI video generation through Replicate official models.

This module creates actual model-generated MP4 clips. It does not silently
substitute title cards when the provider is unavailable or a prediction fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_MODEL = "google/veo-3-fast"
TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
ProgressCallback = Callable[[int, int, str], None]


class VideoGenerationError(RuntimeError):
    """Raised when a requested AI video cannot be generated or verified."""


class ReplicateVideoClient:
    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
        *,
        timeout_seconds: int = 900,
        poll_seconds: float = 3.0,
    ) -> None:
        self.token = (token or os.getenv("REPLICATE_API_TOKEN") or "").strip()
        self.model = (model or os.getenv("SILVER_SCREEN_VIDEO_MODEL") or DEFAULT_MODEL).strip()
        self.timeout_seconds = max(60, int(timeout_seconds))
        self.poll_seconds = max(0.25, float(poll_seconds))
        if not self.token:
            raise VideoGenerationError(
                "REPLICATE_API_TOKEN is not configured. Add it to the deployment secrets before requesting AI video."
            )
        if "/" not in self.model:
            raise VideoGenerationError("SILVER_SCREEN_VIDEO_MODEL must use owner/model format")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request_json(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        prefer_wait: bool = False,
    ) -> dict[str, Any]:
        headers = dict(self.headers)
        if prefer_wait:
            headers["Prefer"] = "wait=60"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=75) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise VideoGenerationError(f"Replicate returned HTTP {exc.code}: {detail[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise VideoGenerationError(f"Could not reach Replicate: {exc.reason}") from exc
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise VideoGenerationError("Replicate returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise VideoGenerationError("Replicate returned an unexpected response")
        return result

    def create_prediction(self, prompt: str, *, seed: int, duration: int = 8) -> dict[str, Any]:
        owner, name = self.model.split("/", 1)
        endpoint = f"https://api.replicate.com/v1/models/{owner}/{name}/predictions"
        payload = {
            "input": {
                "prompt": prompt,
                "duration": max(4, min(8, int(duration))),
                "resolution": os.getenv("SILVER_SCREEN_VIDEO_RESOLUTION", "720p"),
                "aspect_ratio": os.getenv("SILVER_SCREEN_VIDEO_ASPECT_RATIO", "16:9"),
                "generate_audio": os.getenv("SILVER_SCREEN_VIDEO_AUDIO", "1") != "0",
                "seed": int(seed) & 0x7FFFFFFF,
                "negative_prompt": (
                    "text overlays, subtitles, logos, watermarks, malformed hands, duplicate people, "
                    "flicker, abrupt identity changes, low detail"
                ),
            }
        }
        return self._request_json("POST", endpoint, payload, prefer_wait=True)

    def wait(self, prediction: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        current = prediction
        while str(current.get("status")) not in TERMINAL_STATUSES:
            if time.monotonic() - started > self.timeout_seconds:
                raise VideoGenerationError(
                    f"Prediction {current.get('id', '?')} exceeded {self.timeout_seconds} seconds"
                )
            get_url = str((current.get("urls") or {}).get("get") or "")
            if not get_url:
                prediction_id = str(current.get("id") or "")
                if not prediction_id:
                    raise VideoGenerationError("Prediction did not include an ID or polling URL")
                get_url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
            time.sleep(self.poll_seconds)
            current = self._request_json("GET", get_url)
        status = str(current.get("status"))
        if status != "succeeded":
            raise VideoGenerationError(
                f"Prediction {current.get('id', '?')} ended as {status}: {current.get('error') or 'no provider detail'}"
            )
        return current

    def download_output(self, prediction: dict[str, Any], destination: Path) -> Path:
        output = prediction.get("output")
        if isinstance(output, list):
            output = next((item for item in output if isinstance(item, str)), None)
        if isinstance(output, dict):
            output = output.get("url") or output.get("video")
        if not isinstance(output, str) or not output.startswith("https://"):
            raise VideoGenerationError("Prediction succeeded but did not return a downloadable video URL")
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            output,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "video/mp4,*/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        except (urllib.error.URLError, OSError) as exc:
            raise VideoGenerationError(f"Could not download generated video: {exc}") from exc
        verify_mp4(destination)
        return destination


def verify_mp4(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 16_384:
        raise VideoGenerationError(f"Generated video is missing or too small: {path}")
    with path.open("rb") as handle:
        head = handle.read(64)
    if b"ftyp" not in head:
        raise VideoGenerationError(f"Generated artifact is not a valid MP4 container: {path}")


def scene_prompt(state: dict[str, Any], scene: dict[str, Any]) -> str:
    characters = {
        str(character.get("id")): character
        for character in state.get("characters") or []
        if isinstance(character, dict)
    }
    names = [
        str(characters.get(str(character_id), {}).get("name") or "")
        for character_id in scene.get("characters") or []
    ]
    cast = ", ".join(name for name in names if name)
    bible = state.get("storyBible") or {}
    return " ".join(
        part
        for part in [
            "Cinematic live-action film shot, coherent motion, realistic lighting.",
            f"Genre: {state.get('genre', 'drama')}; tone: {state.get('tone', 'cinematic')}.",
            f"Setting: {scene.get('slugline', '')}.",
            f"Characters: {cast}." if cast else "",
            str(scene.get("action") or scene.get("summary") or ""),
            f"Conflict: {scene.get('conflict', '')}.",
            f"Visual motif: {bible.get('motif', '')}." if bible.get("motif") else "",
            "Single continuous shot, no titles, no captions, no watermark.",
        ]
        if part
    )[:3500]


def assemble_clips(clips: list[Path], destination: Path) -> Path:
    if not clips:
        raise VideoGenerationError("No generated clips were available for assembly")
    for clip in clips:
        verify_mp4(clip)
    if len(clips) == 1:
        shutil.copy2(clips[0], destination)
        verify_mp4(destination)
        return destination
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise VideoGenerationError("FFmpeg is required to assemble multiple generated scenes") from exc
    list_file = destination.parent / "generated-scenes.txt"
    list_file.write_text(
        "\n".join(f"file '{clip.resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for clip in clips),
        encoding="utf-8",
    )
    command = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        raise VideoGenerationError(f"FFmpeg assembly failed: {completed.stderr[-1500:]}")
    verify_mp4(destination)
    return destination


def generate_ai_video(
    state: dict[str, Any],
    out_dir: str | os.PathLike[str],
    *,
    scene_limit: int = 3,
    duration: int = 8,
    model: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    scenes = [scene for scene in state.get("scenes") or [] if isinstance(scene, dict)]
    selected = scenes[: max(1, min(int(scene_limit), len(scenes), 8))]
    if not selected:
        raise VideoGenerationError("The film state contains no scenes to generate")
    client = ReplicateVideoClient(model=model)
    clips: list[Path] = []
    predictions: list[dict[str, Any]] = []
    for index, scene in enumerate(selected, start=1):
        if progress:
            progress(index, len(selected), f"Generating AI scene {index} of {len(selected)}")
        prediction = client.wait(
            client.create_prediction(
                scene_prompt(state, scene),
                seed=int(state.get("seed") or 0) + index,
                duration=duration,
            )
        )
        clip_path = output / f"ai_scene_{index:02d}.mp4"
        client.download_output(prediction, clip_path)
        clips.append(clip_path)
        predictions.append(
            {
                "id": prediction.get("id"),
                "status": prediction.get("status"),
                "model": prediction.get("model") or client.model,
                "metrics": prediction.get("metrics") or {},
                "scene": scene.get("number"),
                "path": str(clip_path.resolve()),
            }
        )
    final_path = assemble_clips(clips, output / "final_ai_film.mp4")
    return {
        "ok": True,
        "status": "complete",
        "provider": "replicate",
        "model": client.model,
        "video_paths": [str(path.resolve()) for path in clips],
        "chapter_paths": [str(path.resolve()) for path in clips],
        "hero_path": str(final_path.resolve()),
        "final_video_path": str(final_path.resolve()),
        "predictions": predictions,
        "warnings": [],
        "error": None,
        "note": f"Generated {len(clips)} actual AI video clip(s) and assembled a verified MP4 film.",
    }
