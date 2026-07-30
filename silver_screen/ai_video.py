"""Resumable long-form AI video generation with TGRM repair loops.

Replicate produces short model clips. Silver-Screen turns those clips into a
persistent film production: plan, generate, detect, minimally repair, verify,
checkpoint, reinforce successful repairs, and continue until the runtime target
or an explicit budget gate is reached.
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .runtime import utc_now
from .video_runtime import (
    VideoProductionConfig,
    budget_stop_reason,
    choose_tgrm_repair,
    create_video_queue,
    detect_video_fractures,
    extend_video_queue,
    load_video_queue,
    normalize_video_config,
    queue_paths,
    record_video_event,
    reinforce_video_scar,
    save_video_queue,
    update_video_metrics,
)

DEFAULT_MODEL = "google/veo-3.1-fast"
TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
ProgressCallback = Callable[[int, int, str], None]
QueueUpdateCallback = Callable[[dict[str, Any]], None]


class VideoGenerationError(RuntimeError):
    """Raised when a requested AI video cannot be generated or verified."""


class ReplicateVideoClient:
    """Small official-model HTTP client with resumable prediction polling."""

    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
        *,
        timeout_seconds: int = 1200,
        poll_seconds: float = 3.0,
    ) -> None:
        self.token = (token or os.getenv("REPLICATE_API_TOKEN") or "").strip()
        self.model = (
            model or os.getenv("SILVER_SCREEN_VIDEO_MODEL") or DEFAULT_MODEL
        ).strip()
        self.timeout_seconds = max(60, int(timeout_seconds))
        self.poll_seconds = max(0.25, float(poll_seconds))
        if not self.token:
            raise VideoGenerationError(
                "REPLICATE_API_TOKEN is not configured. Add it to deployment secrets "
                "before requesting AI video."
            )
        if "/" not in self.model:
            raise VideoGenerationError(
                "SILVER_SCREEN_VIDEO_MODEL must use owner/model format"
            )

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
        cancel_after = os.getenv("SILVER_SCREEN_VIDEO_CANCEL_AFTER", "20m").strip()
        if method == "POST" and cancel_after:
            headers["Cancel-After"] = cancel_after
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=75) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise VideoGenerationError(
                f"Replicate returned HTTP {exc.code}: {detail[:1200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise VideoGenerationError(
                f"Could not reach Replicate: {exc.reason}"
            ) from exc
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise VideoGenerationError("Replicate returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise VideoGenerationError("Replicate returned an unexpected response")
        return result

    def create_prediction(
        self,
        prompt: str,
        *,
        seed: int,
        duration: int = 8,
        image: str | None = None,
        generate_audio: bool | None = None,
    ) -> dict[str, Any]:
        owner, name = self.model.split("/", 1)
        endpoint = (
            f"https://api.replicate.com/v1/models/{owner}/{name}/predictions"
        )
        inputs: dict[str, Any] = {
            "prompt": prompt,
            "duration": max(4, min(8, int(duration))),
            "resolution": os.getenv(
                "SILVER_SCREEN_VIDEO_RESOLUTION", "720p"
            ),
            "aspect_ratio": os.getenv(
                "SILVER_SCREEN_VIDEO_ASPECT_RATIO", "16:9"
            ),
            "generate_audio": (
                os.getenv("SILVER_SCREEN_VIDEO_AUDIO", "1") != "0"
                if generate_audio is None
                else bool(generate_audio)
            ),
            "seed": int(seed) & 0x7FFFFFFF,
            "negative_prompt": (
                "text overlays, subtitles, logos, watermarks, malformed hands, "
                "duplicate people, flicker, abrupt identity changes, identity drift, "
                "wardrobe drift, low detail"
            ),
        }
        if image:
            inputs["image"] = image
        return self._request_json(
            "POST", endpoint, {"input": inputs}, prefer_wait=True
        )

    def get_prediction(
        self, prediction_id: str, prediction_url: str | None = None
    ) -> dict[str, Any]:
        url = (
            prediction_url
            or f"https://api.replicate.com/v1/predictions/{prediction_id}"
        )
        return self._request_json("GET", url)

    def wait(
        self,
        prediction: dict[str, Any],
        *,
        on_update: QueueUpdateCallback | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        current = prediction
        if on_update:
            on_update(current)
        while str(current.get("status")) not in TERMINAL_STATUSES:
            if time.monotonic() - started > self.timeout_seconds:
                raise VideoGenerationError(
                    f"Prediction {current.get('id', '?')} exceeded "
                    f"{self.timeout_seconds} seconds"
                )
            prediction_id = str(current.get("id") or "")
            get_url = str((current.get("urls") or {}).get("get") or "")
            if not prediction_id:
                raise VideoGenerationError(
                    "Prediction did not include a persistent ID"
                )
            time.sleep(self.poll_seconds)
            current = self.get_prediction(prediction_id, get_url or None)
            if on_update:
                on_update(current)
        status = str(current.get("status"))
        if status != "succeeded":
            raise VideoGenerationError(
                f"Prediction {current.get('id', '?')} ended as {status}: "
                f"{current.get('error') or 'no provider detail'}"
            )
        return current

    def download_output(
        self, prediction: dict[str, Any], destination: Path
    ) -> Path:
        output = prediction.get("output")
        if isinstance(output, list):
            output = next(
                (item for item in output if isinstance(item, str)), None
            )
        if isinstance(output, dict):
            output = output.get("url") or output.get("video")
        if not isinstance(output, str) or not output.startswith("https://"):
            raise VideoGenerationError(
                "Prediction succeeded but did not return a downloadable video URL"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        requests = [
            urllib.request.Request(
                output,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "video/mp4,*/*",
                },
            ),
            urllib.request.Request(output, headers={"Accept": "video/mp4,*/*"}),
        ]
        last_error: Exception | None = None
        for request in requests:
            try:
                with urllib.request.urlopen(
                    request, timeout=240
                ) as response, destination.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
                verify_mp4(destination)
                return destination
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                last_error = exc
                destination.unlink(missing_ok=True)
        raise VideoGenerationError(
            f"Could not download generated video: {last_error}"
        )


def _ffmpeg_path() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ffprobe_path() -> str | None:
    path = shutil.which("ffprobe")
    if path:
        return path
    ffmpeg = _ffmpeg_path()
    if ffmpeg:
        sibling = str(Path(ffmpeg).with_name("ffprobe"))
        if Path(sibling).exists():
            return sibling
    return None


def verify_mp4(
    path: Path, *, expected_duration: float | None = None
) -> dict[str, Any]:
    """Verify the MP4 container and return available media metadata."""

    if not path.exists() or path.stat().st_size < 16_384:
        raise VideoGenerationError(
            f"Generated video is missing or too small: {path}"
        )
    with path.open("rb") as handle:
        head = handle.read(128)
    if b"ftyp" not in head:
        raise VideoGenerationError(
            f"Generated artifact is not a valid MP4 container: {path}"
        )
    metadata: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "durationSeconds": None,
        "width": None,
        "height": None,
    }
    ffprobe = _ffprobe_path()
    if not ffprobe:
        return metadata
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=30
    )
    if completed.returncode != 0:
        return metadata
    try:
        payload = json.loads(completed.stdout)
        duration = float((payload.get("format") or {}).get("duration") or 0)
        video_stream = next(
            (
                stream
                for stream in payload.get("streams") or []
                if stream.get("codec_type") == "video"
            ),
            {},
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return metadata
    if duration <= 0:
        raise VideoGenerationError(f"FFprobe found no usable duration: {path}")
    if expected_duration and duration < max(1.0, expected_duration * 0.5):
        raise VideoGenerationError(
            f"Generated clip is too short ({duration:.2f}s; expected about "
            f"{expected_duration:.2f}s)"
        )
    metadata.update(
        {
            "durationSeconds": round(duration, 3),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
        }
    )
    return metadata


def _compress_image_bytes(
    image_bytes: bytes, *, max_bytes: int = 245_000
) -> bytes:
    try:
        from PIL import Image
    except Exception:
        return image_bytes
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        if image.width > 1280:
            height = max(1, round(image.height * 1280 / image.width))
            image = image.resize((1280, height))
        for quality in (84, 74, 64, 54, 44, 34):
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()
            if len(data) <= max_bytes:
                return data
    return data


def image_to_data_url(image: Any) -> str | None:
    """Encode a small authorized image as a Replicate-compatible data URL."""

    if image is None:
        return None
    try:
        if isinstance(image, (str, os.PathLike)):
            path = Path(image)
            data = path.read_bytes()
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        elif hasattr(image, "read"):
            data = image.read()
            if hasattr(image, "seek"):
                image.seek(0)
            mime = getattr(image, "type", None) or "image/jpeg"
        else:
            return None
        data = _compress_image_bytes(data)
        if len(data) > 256_000:
            return None
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            mime = "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    except Exception:
        return None


def extract_last_frame_data_url(
    clip: Path, destination: Path
) -> tuple[str | None, Path | None]:
    """Extract and persist the accepted clip's final frame for continuity."""

    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None, None
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-sseof",
        "-0.15",
        "-i",
        str(clip),
        "-frames:v",
        "1",
        "-vf",
        "scale='min(1280,iw)':-2",
        "-q:v",
        "4",
        str(destination),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=60
    )
    if completed.returncode != 0 or not destination.exists():
        destination.unlink(missing_ok=True)
        return None, None
    data = _compress_image_bytes(destination.read_bytes())
    destination.write_bytes(data)
    if len(data) > 256_000:
        return None, destination
    return (
        f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}",
        destination,
    )


def _scene_by_number(
    state: dict[str, Any], scene_number: int
) -> dict[str, Any]:
    for scene in state.get("scenes") or []:
        if (
            isinstance(scene, dict)
            and int(scene.get("number", -1) or -1) == scene_number
        ):
            return scene
    scenes = [scene for scene in state.get("scenes") or [] if isinstance(scene, dict)]
    if not scenes:
        raise VideoGenerationError("Film state contains no scenes")
    return scenes[min(len(scenes) - 1, max(0, scene_number - 1))]


def scene_prompt(
    state: dict[str, Any],
    scene: dict[str, Any],
    shot: dict[str, Any] | None = None,
    repair: dict[str, Any] | None = None,
) -> str:
    characters = {
        str(character.get("id")): character
        for character in state.get("characters") or []
        if isinstance(character, dict)
    }
    cast_details = []
    for character_id in scene.get("characters") or []:
        character = characters.get(str(character_id), {})
        name = str(character.get("name") or "").strip()
        if name:
            description = str(character.get("description") or "").strip()
            cast_details.append(
                f"{name}{f', {description}' if description else ''}"
            )
    bible = state.get("storyBible") or {}
    segment = int((shot or {}).get("segment", 1) or 1)
    total_scene_segments = sum(
        1
        for candidate in (state.get("_videoShots") or [])
        if int((candidate.get("sourceScene") or {}).get("number", -1))
        == int(scene.get("number", -2))
    )
    repair_suffix = str((repair or {}).get("promptSuffix") or "")
    continuity = (
        "Begin from the supplied previous-frame image and preserve the exact "
        "character identity, wardrobe, lighting direction, camera axis, and physical "
        "action. Continue motion naturally without resetting the scene."
        if (shot or {}).get("continuityUsed")
        else "Establish a stable visual identity that can continue into later shots."
    )
    prompt = " ".join(
        part
        for part in [
            "Cinematic live-action film footage, coherent physical motion, realistic lighting.",
            f"Genre: {state.get('genre', 'drama')}; tone: {state.get('tone', 'cinematic')}.",
            f"Setting: {scene.get('slugline', '')}.",
            f"Characters: {'; '.join(cast_details)}." if cast_details else "",
            f"Scene segment {segment}"
            + (f" of {total_scene_segments}" if total_scene_segments else "")
            + ".",
            str(scene.get("action") or scene.get("summary") or ""),
            f"Conflict: {scene.get('conflict', '')}.",
            f"Turn: {scene.get('turn', '')}." if scene.get("turn") else "",
            f"Visual motif: {bible.get('motif', '')}." if bible.get("motif") else "",
            continuity,
            repair_suffix,
            "One continuous cinematic shot. No titles, captions, logos, or watermark.",
        ]
        if part
    )
    return prompt[:3500]


def _escape_concat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")


def assemble_clips(clips: list[Path], destination: Path) -> Path:
    """Re-encode clips into one stable MP4."""

    if not clips:
        raise VideoGenerationError("No generated clips were available for assembly")
    for clip in clips:
        verify_mp4(clip)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        shutil.copy2(clips[0], destination)
        verify_mp4(destination)
        return destination
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise VideoGenerationError(
            "FFmpeg is required to assemble multiple generated scenes"
        )
    list_file = destination.parent / f".{destination.stem}-concat.txt"
    list_file.write_text(
        "\n".join(f"file '{_escape_concat_path(clip)}'" for clip in clips),
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
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=1800
    )
    list_file.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise VideoGenerationError(
            f"FFmpeg assembly failed: {completed.stderr[-1800:]}"
        )
    verify_mp4(destination)
    return destination


def _shot_path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = shot.get("path")
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else (root / path).resolve()


def _store_relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if root.resolve() not in resolved.parents:
        raise VideoGenerationError("Video artifact escaped the production directory")
    return resolved.relative_to(root.resolve()).as_posix()


def _verified_shots(queue: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            shot
            for shot in queue.get("shots") or []
            if isinstance(shot, dict) and shot.get("status") == "verified"
        ],
        key=lambda shot: int(shot.get("order", 0) or 0),
    )


def assemble_verified_production(
    queue: dict[str, Any], root: Path, *, complete: bool
) -> dict[str, Any]:
    """Assemble verified clips chapter-first so large productions stay manageable."""

    shots = _verified_shots(queue)
    if not shots:
        return {}
    chapter_dir = root / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter_groups: dict[int, list[Path]] = {}
    for shot in shots:
        path = _shot_path(root, shot)
        if path is None:
            continue
        chapter = int((shot.get("sourceScene") or {}).get("chapter", 1) or 1)
        chapter_groups.setdefault(chapter, []).append(path)
    chapter_paths: list[Path] = []
    for chapter, clips in sorted(chapter_groups.items()):
        destination = chapter_dir / f"chapter_{chapter:03d}.mp4"
        assemble_clips(clips, destination)
        chapter_paths.append(destination)
    final_name = "final_ai_film.mp4" if complete else "partial_ai_film.mp4"
    final_path = assemble_clips(chapter_paths, root / final_name)
    artifacts = {
        "chapterReels": [_store_relative(root, path) for path in chapter_paths],
        "finalFilm" if complete else "partialFilm": _store_relative(
            root, final_path
        ),
    }
    queue.setdefault("artifacts", {}).update(artifacts)
    return artifacts


def _prediction_snapshot(prediction: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": prediction.get("id"),
        "status": prediction.get("status"),
        "model": prediction.get("model"),
        "metrics": prediction.get("metrics") or {},
        "error": prediction.get("error"),
        "urls": prediction.get("urls") or {},
        "created_at": prediction.get("created_at"),
        "started_at": prediction.get("started_at"),
        "completed_at": prediction.get("completed_at"),
    }


def _ensure_queue(
    state: dict[str, Any],
    root: Path,
    config: VideoProductionConfig,
    *,
    resume: bool,
) -> dict[str, Any]:
    queue = load_video_queue(root) if resume else None
    if queue is None:
        queue = create_video_queue(state, config)
        record_video_event(
            queue,
            "production_planned",
            detail=(
                f"{len(queue.get('shots') or [])} clips for "
                f"{queue.get('plannedRuntimeSeconds')} seconds"
            ),
        )
    else:
        queue = extend_video_queue(queue, state, config)
        record_video_event(
            queue,
            "production_resumed",
            detail=f"Resumed at {queue.get('metrics', {}).get('verifiedShots', 0)} verified shots",
        )
    state["_videoShots"] = queue.get("shots") or []
    save_video_queue(root, queue)
    return queue


def _reconcile_verified_files(
    queue: dict[str, Any], root: Path
) -> None:
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict) or shot.get("status") != "verified":
            continue
        path = _shot_path(root, shot)
        try:
            if path is None:
                raise VideoGenerationError("Verified shot has no artifact path")
            metadata = verify_mp4(
                path,
                expected_duration=float(
                    shot.get("plannedDurationSeconds", 0) or 0
                ),
            )
            shot["verification"] = metadata
        except VideoGenerationError as exc:
            shot["status"] = "pending"
            shot["lastError"] = f"Reconciliation: {exc}"
            shot["path"] = None
            shot["verifiedDurationSeconds"] = 0.0
            record_video_event(
                queue,
                "verified_artifact_reopened",
                shot_id=str(shot.get("id")),
                detail=str(exc),
            )


def _next_incomplete_shot(queue: dict[str, Any]) -> dict[str, Any] | None:
    shots = sorted(
        [shot for shot in queue.get("shots") or [] if isinstance(shot, dict)],
        key=lambda shot: int(shot.get("order", 0) or 0),
    )
    for shot in shots:
        if shot.get("status") != "verified":
            order = int(shot.get("order", 0) or 0)
            if order > 1:
                previous = next(
                    (
                        candidate
                        for candidate in shots
                        if int(candidate.get("order", 0) or 0) == order - 1
                    ),
                    None,
                )
                if previous and previous.get("status") != "verified":
                    return previous
            return shot
    return None


def _previous_verified_shot(
    queue: dict[str, Any], shot: dict[str, Any]
) -> dict[str, Any] | None:
    order = int(shot.get("order", 0) or 0)
    return next(
        (
            candidate
            for candidate in queue.get("shots") or []
            if isinstance(candidate, dict)
            and int(candidate.get("order", 0) or 0) == order - 1
            and candidate.get("status") == "verified"
        ),
        None,
    )


def _continuity_input(
    queue: dict[str, Any],
    root: Path,
    shot: dict[str, Any],
    config: VideoProductionConfig,
    initial_image: str | None,
) -> tuple[str | None, str | None]:
    if int(shot.get("order", 0) or 0) == 1 and initial_image:
        shot["continuityUsed"] = True
        return initial_image, "operator_reference"
    if not config.use_continuity_frames:
        return None, None
    previous = _previous_verified_shot(queue, shot)
    if not previous:
        return None, None
    previous_path = _shot_path(root, previous)
    if previous_path is None:
        return None, None
    frame_path_value = previous.get("continuityFrame")
    if frame_path_value:
        frame_path = (
            Path(str(frame_path_value))
            if Path(str(frame_path_value)).is_absolute()
            else root / str(frame_path_value)
        )
        if frame_path.exists():
            data_url = image_to_data_url(frame_path)
            if data_url:
                shot["continuityUsed"] = True
                return data_url, _store_relative(root, frame_path)
    frame_path = root / "continuity" / f"{previous.get('id')}_last.jpg"
    data_url, persisted = extract_last_frame_data_url(
        previous_path, frame_path
    )
    if persisted:
        previous["continuityFrame"] = _store_relative(root, persisted)
    if data_url:
        shot["continuityUsed"] = True
        return data_url, (
            _store_relative(root, persisted) if persisted else None
        )
    return None, None


def _update_prediction_in_queue(
    queue: dict[str, Any],
    root: Path,
    shot: dict[str, Any],
    prediction: dict[str, Any],
) -> None:
    snapshot = _prediction_snapshot(prediction)
    shot["providerPredictionId"] = snapshot.get("id")
    shot["providerStatus"] = snapshot.get("status")
    shot["providerPredictionUrl"] = str(
        (snapshot.get("urls") or {}).get("get") or ""
    ) or shot.get("providerPredictionUrl")
    shot["provider"] = snapshot
    save_video_queue(root, queue)


def _process_prediction(
    *,
    client: ReplicateVideoClient,
    queue: dict[str, Any],
    root: Path,
    state: dict[str, Any],
    shot: dict[str, Any],
    config: VideoProductionConfig,
    initial_image: str | None,
) -> None:
    repair = (
        choose_tgrm_repair(
            str(shot.get("lastError") or ""),
            int(shot.get("attempts", 0) or 0) + 1,
        )
        if int(shot.get("attempts", 0) or 0) > 0
        else None
    )
    image, continuity_frame = _continuity_input(
        queue, root, shot, config, initial_image
    )
    if continuity_frame:
        shot["inputContinuityFrame"] = continuity_frame
    source_scene = _scene_by_number(
        state, int((shot.get("sourceScene") or {}).get("number", 1) or 1)
    )
    prompt = scene_prompt(state, source_scene, shot, repair)
    attempt_seed = (
        int(shot.get("seed", 0) or 0)
        + int((repair or {}).get("seedDelta", 0) or 0)
    ) & 0x7FFFFFFF
    shot["prompt"] = prompt
    shot["promptRevision"] = int(shot.get("promptRevision", 0) or 0) + (
        1 if repair else 0
    )
    shot["attempts"] = int(shot.get("attempts", 0) or 0) + 1
    shot["attemptSeed"] = attempt_seed
    shot["status"] = "submitting"
    shot["startedAt"] = shot.get("startedAt") or utc_now()
    shot["lastError"] = None
    save_video_queue(root, queue)
    prediction = client.create_prediction(
        prompt,
        seed=attempt_seed,
        duration=config.clip_duration_seconds,
        image=image,
        generate_audio=(
            False if (repair or {}).get("disableAudio") else None
        ),
    )
    shot["status"] = "submitted"
    _update_prediction_in_queue(queue, root, shot, prediction)
    record_video_event(
        queue,
        "prediction_submitted",
        shot_id=str(shot.get("id")),
        data={
            "predictionId": prediction.get("id"),
            "attempt": shot.get("attempts"),
            "repair": repair,
            "continuityUsed": shot.get("continuityUsed"),
        },
    )
    save_video_queue(root, queue)
    prediction = client.wait(
        prediction,
        on_update=lambda current: _update_prediction_in_queue(
            queue, root, shot, current
        ),
    )
    clip_path = root / "clips" / f"{shot.get('id')}.mp4"
    client.download_output(prediction, clip_path)
    verification = verify_mp4(
        clip_path, expected_duration=config.clip_duration_seconds
    )
    shot["path"] = _store_relative(root, clip_path)
    shot["status"] = "verified"
    shot["providerStatus"] = "succeeded"
    shot["verification"] = verification
    shot["verifiedDurationSeconds"] = float(
        verification.get("durationSeconds")
        or shot.get("plannedDurationSeconds")
        or config.clip_duration_seconds
    )
    shot["completedAt"] = utc_now()
    shot["lastError"] = None
    if repair:
        shot.setdefault("repairHistory", []).append(repair)
        reinforce_video_scar(queue, shot=shot, repair=repair)
    record_video_event(
        queue,
        "shot_verified",
        shot_id=str(shot.get("id")),
        detail=(
            f"{shot.get('verifiedDurationSeconds')} verified seconds "
            f"after {shot.get('attempts')} attempt(s)"
        ),
    )
    save_video_queue(root, queue)


def _resume_submitted_prediction(
    *,
    client: ReplicateVideoClient,
    queue: dict[str, Any],
    root: Path,
    shot: dict[str, Any],
    config: VideoProductionConfig,
) -> None:
    prediction_id = str(shot.get("providerPredictionId") or "")
    if not prediction_id:
        shot["status"] = "pending"
        shot["lastError"] = "Orphaned submitted shot lacked a prediction ID"
        return
    prediction = client.get_prediction(
        prediction_id, str(shot.get("providerPredictionUrl") or "") or None
    )
    prediction = client.wait(
        prediction,
        on_update=lambda current: _update_prediction_in_queue(
            queue, root, shot, current
        ),
    )
    clip_path = root / "clips" / f"{shot.get('id')}.mp4"
    client.download_output(prediction, clip_path)
    verification = verify_mp4(
        clip_path, expected_duration=config.clip_duration_seconds
    )
    shot["path"] = _store_relative(root, clip_path)
    shot["status"] = "verified"
    shot["providerStatus"] = "succeeded"
    shot["verification"] = verification
    shot["verifiedDurationSeconds"] = float(
        verification.get("durationSeconds")
        or shot.get("plannedDurationSeconds")
        or config.clip_duration_seconds
    )
    shot["completedAt"] = utc_now()
    shot["lastError"] = None
    record_video_event(
        queue,
        "submitted_prediction_recovered",
        shot_id=str(shot.get("id")),
        data={"predictionId": prediction_id},
    )
    save_video_queue(root, queue)


def generate_ai_video(
    state: dict[str, Any],
    out_dir: str | os.PathLike[str],
    *,
    scene_limit: int | None = None,
    max_shots: int | None = None,
    duration: int | None = None,
    model: str | None = None,
    progress: ProgressCallback | None = None,
    target_runtime_seconds: int | None = None,
    batch_size: int | None = None,
    max_retries_per_shot: int | None = None,
    max_provider_calls: int | None = None,
    max_spend_usd: float | None = None,
    cost_per_second_usd: float | None = None,
    use_continuity_frames: bool | None = None,
    continuous: bool = False,
    resume: bool = True,
    initial_image: Any = None,
    client_factory: Callable[..., ReplicateVideoClient] = ReplicateVideoClient,
) -> dict[str, Any]:
    """Run or resume a bounded long-form AI video production.

    The function checkpoints after every provider transition and accepted shot.
    ``batch_size`` limits new paid predictions in one invocation. Set
    ``continuous=True`` to keep working until completion or a budget/repair gate.
    """

    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    effective_max_shots = (
        max(1, int(max_shots))
        if max_shots is not None
        else (max(1, int(scene_limit)) if scene_limit is not None else None)
    )
    effective_target = target_runtime_seconds
    if effective_target is None and scene_limit is not None:
        effective_target = max(1, int(scene_limit)) * int(duration or 8)
    config = normalize_video_config(
        target_runtime_seconds=effective_target,
        clip_duration_seconds=duration,
        max_shots=effective_max_shots,
        batch_size=batch_size,
        max_retries_per_shot=max_retries_per_shot,
        max_provider_calls=max_provider_calls,
        max_spend_usd=max_spend_usd,
        cost_per_second_usd=cost_per_second_usd,
        use_continuity_frames=use_continuity_frames,
    )
    queue = _ensure_queue(state, root, config, resume=resume)
    _reconcile_verified_files(queue, root)
    update_video_metrics(queue)
    save_video_queue(root, queue)
    client = client_factory(model=model)
    initial_image_data = image_to_data_url(initial_image)
    call_limit = (
        config.provider_call_budget
        if continuous or config.batch_size <= 0
        else config.batch_size
    )
    calls_at_start = int(
        (queue.get("metrics") or {}).get("providerCalls", 0) or 0
    )
    calls_this_run = 0
    queue["status"] = "running"
    queue["stopReason"] = None
    save_video_queue(root, queue)

    while True:
        update_video_metrics(queue)
        verified = int(
            (queue.get("metrics") or {}).get("verifiedShots", 0) or 0
        )
        planned = int(
            (queue.get("metrics") or {}).get("plannedShots", 0) or 0
        )
        if progress:
            progress(
                verified,
                planned,
                f"Verified {verified} of {planned} long-film shots",
            )
        if planned and verified >= planned:
            queue["status"] = "complete"
            queue["stopReason"] = "target_runtime_reached"
            queue["completedAt"] = utc_now()
            break
        reason = budget_stop_reason(queue, config)
        if reason:
            queue["status"] = "blocked"
            queue["stopReason"] = reason
            record_video_event(queue, "budget_gate", detail=reason)
            break
        if calls_this_run >= call_limit:
            queue["status"] = "partial"
            queue["stopReason"] = "batch_checkpoint"
            break
        shot = _next_incomplete_shot(queue)
        if shot is None:
            queue["status"] = "complete"
            queue["stopReason"] = "target_runtime_reached"
            queue["completedAt"] = utc_now()
            break
        allowed_attempts = config.max_retries_per_shot + 1
        if (
            shot.get("status") != "submitted"
            and int(shot.get("attempts", 0) or 0) >= allowed_attempts
        ):
            shot["status"] = "blocked"
            queue["status"] = "blocked"
            queue["stopReason"] = "shot_retry_budget_exhausted"
            record_video_event(
                queue,
                "shot_blocked",
                shot_id=str(shot.get("id")),
                detail=str(shot.get("lastError") or "Retry budget exhausted"),
            )
            break
        try:
            if shot.get("status") == "submitted":
                _resume_submitted_prediction(
                    client=client,
                    queue=queue,
                    root=root,
                    shot=shot,
                    config=config,
                )
            else:
                _process_prediction(
                    client=client,
                    queue=queue,
                    root=root,
                    state=state,
                    shot=shot,
                    config=config,
                    initial_image=initial_image_data,
                )
                calls_this_run += 1
        except Exception as exc:
            update_video_metrics(queue)
            current_calls = int(
                (queue.get("metrics") or {}).get("providerCalls", 0) or 0
            )
            calls_this_run = max(
                calls_this_run, current_calls - calls_at_start
            )
            error = str(exc)
            shot["lastError"] = error
            shot["providerStatus"] = "failed"
            repair = choose_tgrm_repair(
                error, int(shot.get("attempts", 0) or 0) + 1
            )
            shot.setdefault("repairHistory", []).append(repair)
            if int(shot.get("attempts", 0) or 0) < allowed_attempts:
                shot["status"] = "pending"
                record_video_event(
                    queue,
                    "tgrm_repair_scheduled",
                    shot_id=str(shot.get("id")),
                    detail=error,
                    data=repair,
                )
            else:
                shot["status"] = "blocked"
                queue["status"] = "blocked"
                queue["stopReason"] = "shot_retry_budget_exhausted"
                record_video_event(
                    queue,
                    "shot_blocked",
                    shot_id=str(shot.get("id")),
                    detail=error,
                    data=repair,
                )
            save_video_queue(root, queue)
            if shot.get("status") == "blocked":
                break

    update_video_metrics(queue)
    complete = queue.get("status") == "complete"
    assembly_error: str | None = None
    if _verified_shots(queue):
        try:
            assemble_verified_production(queue, root, complete=complete)
        except Exception as exc:
            assembly_error = str(exc)
            queue["status"] = "blocked" if complete else queue.get("status")
            queue["stopReason"] = (
                "assembly_failed" if complete else queue.get("stopReason")
            )
            record_video_event(
                queue, "assembly_failed", detail=assembly_error
            )
    update_video_metrics(queue)
    save_video_queue(root, queue)

    shots = _verified_shots(queue)
    scene_paths = [
        str(_shot_path(root, shot))
        for shot in shots
        if _shot_path(root, shot) is not None
    ]
    artifacts = queue.get("artifacts") or {}
    final_relative = artifacts.get("finalFilm") or artifacts.get("partialFilm")
    final_path = str((root / final_relative).resolve()) if final_relative else None
    chapter_paths = [
        str((root / path).resolve())
        for path in artifacts.get("chapterReels") or []
    ]
    fractures = detect_video_fractures(queue)
    note = (
        f"Verified {(queue.get('metrics') or {}).get('verifiedShots', 0)} of "
        f"{(queue.get('metrics') or {}).get('plannedShots', 0)} clips "
        f"({(queue.get('metrics') or {}).get('verifiedSeconds', 0)} seconds). "
        f"Status: {queue.get('status')}."
    )
    if queue.get("status") == "partial":
        note += " Run the resume command or Continue button to process the next batch."
    return {
        "ok": queue.get("status") in {"complete", "partial"},
        "status": queue.get("status"),
        "resumeRequired": queue.get("status") in {"partial", "blocked"},
        "stopReason": queue.get("stopReason"),
        "provider": "replicate",
        "model": client.model,
        "mode": "ai-video",
        "video_paths": scene_paths,
        "scene_paths": scene_paths,
        "chapter_paths": chapter_paths,
        "hero_path": final_path,
        "final_video_path": final_path if complete else None,
        "partial_video_path": final_path if not complete else None,
        "predictions": [
            {
                "id": shot.get("providerPredictionId"),
                "status": shot.get("providerStatus"),
                "scene": (shot.get("sourceScene") or {}).get("number"),
                "shot": shot.get("id"),
                "attempts": shot.get("attempts"),
                "path": str(_shot_path(root, shot))
                if _shot_path(root, shot)
                else None,
            }
            for shot in queue.get("shots") or []
            if isinstance(shot, dict) and shot.get("providerPredictionId")
        ],
        "queue": queue,
        "metrics": queue.get("metrics") or {},
        "msil": queue.get("msil") or {},
        "fractures": fractures,
        "scars": queue.get("scars") or [],
        "queue_path": str(queue_paths(root)["queue"].resolve()),
        "runtime_path": str(queue_paths(root)["runtime"].resolve()),
        "scar_memory_path": str(queue_paths(root)["scars"].resolve()),
        "warnings": [assembly_error] if assembly_error else [],
        "error": assembly_error
        if queue.get("status") == "blocked" and assembly_error
        else (
            str(
                next(
                    (
                        shot.get("lastError")
                        for shot in queue.get("shots") or []
                        if isinstance(shot, dict)
                        and shot.get("status") == "blocked"
                    ),
                    "",
                )
            )
            or None
        ),
        "note": note,
        "out_dir": str(root),
        "config": queue.get("config") or {},
    }


def resume_ai_video(
    state: dict[str, Any],
    out_dir: str | os.PathLike[str],
    **kwargs: Any,
) -> dict[str, Any]:
    kwargs["resume"] = True
    return generate_ai_video(state, out_dir, **kwargs)


def video_production_status(
    out_dir: str | os.PathLike[str],
) -> dict[str, Any] | None:
    queue = load_video_queue(out_dir)
    if queue is None:
        return None
    update_video_metrics(queue)
    return {
        "productionId": queue.get("productionId"),
        "status": queue.get("status"),
        "stopReason": queue.get("stopReason"),
        "metrics": queue.get("metrics") or {},
        "msil": queue.get("msil") or {},
        "fractures": detect_video_fractures(queue),
        "artifacts": queue.get("artifacts") or {},
        "updatedAt": queue.get("updatedAt"),
    }
