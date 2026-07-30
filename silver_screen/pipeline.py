"""Operational end-to-end pipeline for Silver-Screen."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .ai_video import video_production_status
from .media import process_media
from .runtime import (
    RunWorkspace,
    create_run_id,
    load_run,
)
from .science import FORMATS, GENRES, SCIENCE, TONES
from .script_engine import build_film_from_brief, derive_seed
from .tgrm import run_tgrm

ProgressCallback = Callable[[str, int, str], None]


class BriefValidationError(ValueError):
    """Raised when the requested production brief is not safe to execute."""


class PipelineError(RuntimeError):
    """Raised when a pipeline stage fails after validation."""

    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id


GENRE_ALIASES = {
    "sci-fi": "scifi",
    "sci fi": "scifi",
    "science fiction": "scifi",
    "sciencefiction": "scifi",
    "scifi": "scifi",
    "noir": "noir",
    "drama": "drama",
    "thriller": "thriller",
    "fantasy": "fantasy",
    "horror": "horror",
    "western": "western",
    "romance": "romance",
}
TONE_ALIASES = {
    "poetic": "cinematic",
    "cinematic": "cinematic",
    "intimate": "intimate",
    "epic": "epic",
    "melancholy": "melancholy",
    "tense": "tense",
    "hopeful": "hopeful",
}
FORMAT_ALIASES = {
    "film": "feature",
    "movie": "feature",
    "feature film": "feature",
    "short film": "short",
    "tv episode": "episode",
    "trailer": "trailer",
    "short": "short",
    "episode": "episode",
    "featurette": "featurette",
    "feature": "feature",
}


def _normalize_token(value: Any) -> str:
    return " ".join(
        str(value or "").strip().lower().replace("_", " ").split()
    )


def _normalize_genre(value: Any) -> str:
    token = _normalize_token(value or "scifi")
    normalized = GENRE_ALIASES.get(
        token, token.replace("-", "").replace(" ", "")
    )
    return normalized if normalized in GENRES else "drama"


def _normalize_tone(value: Any) -> str:
    token = _normalize_token(value or "cinematic")
    normalized = TONE_ALIASES.get(token, token)
    return normalized if normalized in TONES else "cinematic"


def _normalize_format(value: Any) -> str:
    token = _normalize_token(value or "short")
    normalized = FORMAT_ALIASES.get(token, token)
    return normalized if normalized in FORMATS else "short"


def _normalize_cast(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise BriefValidationError(
            "cast must be a list of character objects"
        )
    cast: list[dict[str, str]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise BriefValidationError(
                f"cast entry {index + 1} must be an object"
            )
        name = str(raw.get("name") or "").strip()
        if not name:
            raise BriefValidationError(
                f"cast entry {index + 1} requires a name"
            )
        cast.append(
            {
                "name": name[:80],
                "role": str(
                    raw.get("role") or "Supporting character"
                )[:120],
                "description": str(
                    raw.get("description") or ""
                )[:400],
                "arc": str(
                    raw.get("arc")
                    or "From fracture to a tested new belief"
                )[:160],
            }
        )
        if len(cast) >= 6:
            break
    if cast and len(cast) < 2:
        raise BriefValidationError(
            "custom cast requires at least two named characters"
        )
    return cast


def validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a production brief without mutating it."""

    if not isinstance(brief, dict):
        raise BriefValidationError(
            "brief must be a JSON object or Python dictionary"
        )
    premise = str(brief.get("premise") or "").strip()
    if len(premise) < 12:
        raise BriefValidationError(
            "premise must contain at least 12 characters"
        )
    if len(premise) > 4000:
        raise BriefValidationError(
            "premise must not exceed 4,000 characters"
        )
    title_value = str(brief.get("title") or "").strip()
    if len(title_value) > 120:
        raise BriefValidationError(
            "title must not exceed 120 characters"
        )
    genre = _normalize_genre(brief.get("genre"))
    tone = _normalize_tone(brief.get("tone"))
    fmt = _normalize_format(
        brief.get("format") or brief.get("fmt")
    )
    raw_seed = brief.get("seed")
    if raw_seed in (None, ""):
        seed = derive_seed(premise, genre, tone, fmt)
    else:
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError) as exc:
            raise BriefValidationError(
                "seed must be an integer"
            ) from exc
        if not 0 <= seed <= 2_147_483_647:
            raise BriefValidationError(
                "seed must be between 0 and 2,147,483,647"
            )
    return {
        "title": title_value or None,
        "premise": premise,
        "genre": genre,
        "tone": tone,
        "format": fmt,
        "seed": seed,
        "cast": _normalize_cast(brief.get("cast")),
        "scars": list(brief.get("scars") or []),
    }


def brief_fingerprint(brief: dict[str, Any]) -> str:
    payload = json.dumps(
        brief,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _notify(
    callback: ProgressCallback | None,
    stage: str,
    percent: int,
    message: str,
) -> None:
    if callback is None:
        return
    try:
        callback(stage, percent, message)
    except Exception:
        return


def _timed_stage(
    timings: dict[str, float], name: str, started: float
) -> None:
    timings[name] = round(time.perf_counter() - started, 4)


def _video_progress(
    callback: ProgressCallback | None,
    workspace: RunWorkspace | None,
):
    def update(completed: int, total: int, message: str) -> None:
        ratio = completed / max(1, total)
        percent = min(89, 72 + round(ratio * 17))
        _notify(callback, "video_production", percent, message)
        if workspace:
            workspace.update(
                stage="video_production",
                progress=percent,
                status="running",
                extra={
                    "videoProgress": {
                        "completed": completed,
                        "total": total,
                    }
                },
            )

    return update


def _register_media_artifacts(
    workspace: RunWorkspace,
    media: dict[str, Any],
) -> None:
    for index, path in enumerate(
        media.get("card_paths") or [], start=1
    ):
        workspace.register_optional_artifact(
            f"chapterCard{index:03d}", path
        )
    for index, path in enumerate(
        media.get("scene_paths")
        or media.get("video_paths")
        or [],
        start=1,
    ):
        workspace.register_optional_artifact(
            f"generatedShot{index:04d}", path
        )
    for index, path in enumerate(
        media.get("chapter_paths") or [], start=1
    ):
        workspace.register_optional_artifact(
            f"chapterVideo{index:03d}", path
        )
    workspace.register_optional_artifact(
        "finalFilm", media.get("final_video_path")
    )
    workspace.register_optional_artifact(
        "partialFilm", media.get("partial_video_path")
    )
    workspace.register_optional_artifact(
        "videoQueue", media.get("queue_path")
    )
    workspace.register_optional_artifact(
        "videoRuntime", media.get("runtime_path")
    )
    workspace.register_optional_artifact(
        "videoScarMemory", media.get("scar_memory_path")
    )


def _pipeline_status(
    video_mode: str,
    media: dict[str, Any],
) -> str:
    if video_mode != "ai-video":
        return "complete"
    status = str(media.get("status") or "failed")
    if status in {"complete", "partial", "blocked"}:
        return status
    return "failed"


def _persist_and_finalize(
    workspace: RunWorkspace,
    result: dict[str, Any],
) -> None:
    media = result.get("media") or {}
    for warning in result.get("warnings") or []:
        workspace.update(warning=str(warning))
    _register_media_artifacts(workspace, media)
    core_artifacts = workspace.persist_result(result)
    result.setdefault("artifacts", {}).update(core_artifacts)
    status = str(result.get("status") or "complete")
    if status == "complete":
        workspace.complete(
            {
                "metrics": result.get("metrics") or {},
                "msil": result.get("msil") or {},
                "videoMetrics": media.get("metrics") or {},
                "videoMsil": media.get("msil") or {},
                "timings": result.get("timings") or {},
                "title": (result.get("state") or {}).get("title"),
            }
        )
    elif status in {"partial", "blocked"}:
        completion = float(
            (media.get("metrics") or {}).get(
                "completionRatio", 0
            )
            or 0
        )
        workspace.checkpoint(
            status=status,
            stage=(
                "video_checkpoint"
                if status == "partial"
                else "video_blocked"
            ),
            progress=min(99, 72 + round(completion * 26)),
            extra={
                "videoMetrics": media.get("metrics") or {},
                "videoMsil": media.get("msil") or {},
                "videoStopReason": media.get("stopReason"),
                "title": (result.get("state") or {}).get("title"),
            },
        )
    else:
        workspace.fail(
            str(media.get("error") or "Media production failed")
        )
    bundle = workspace.build_bundle(
        str((result.get("state") or {}).get("title") or workspace.run_id)
    )
    result["artifacts"]["bundle"] = str(bundle)
    workspace.write_json("result.json", result)
    workspace.build_bundle(
        str((result.get("state") or {}).get("title") or workspace.run_id)
    )


def run_pipeline(
    brief: dict[str, Any],
    images: list[Any] | None = None,
    voices: list[Any] | None = None,
    *,
    output_root: str | None = None,
    persist: bool = True,
    render_media: bool = True,
    video_mode: str = "cards",
    max_chapters: int = 4,
    max_cycles: int | None = None,
    energy_budget: int | None = None,
    target_runtime_seconds: int | None = None,
    video_max_shots: int | None = None,
    video_batch_size: int | None = None,
    video_max_retries: int | None = None,
    video_max_provider_calls: int | None = None,
    video_max_spend_usd: float | None = None,
    video_cost_per_second_usd: float | None = None,
    video_continuous: bool = False,
    video_use_continuity: bool | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run validation, story generation, TGRM, video, and persistence."""

    normalized = validate_brief(brief)
    cycle_limit = max(
        1, min(20, int(max_cycles or SCIENCE["maxCycles"]))
    )
    budget = max(
        3,
        min(
            500,
            int(energy_budget or SCIENCE["energyBudget"]),
        ),
    )
    chapter_limit = max(1, min(12, int(max_chapters)))
    options = {
        "persist": bool(persist),
        "renderMedia": bool(render_media),
        "videoMode": video_mode,
        "maxChapters": chapter_limit,
        "maxCycles": cycle_limit,
        "energyBudget": budget,
        "targetRuntimeSeconds": target_runtime_seconds,
        "videoMaxShots": video_max_shots,
        "videoBatchSize": video_batch_size,
        "videoMaxRetries": video_max_retries,
        "videoMaxProviderCalls": video_max_provider_calls,
        "videoMaxSpendUsd": video_max_spend_usd,
        "videoCostPerSecondUsd": video_cost_per_second_usd,
        "videoContinuous": video_continuous,
        "videoUseContinuity": video_use_continuity,
    }
    run_id = create_run_id()
    workspace: RunWorkspace | None = None
    warnings: list[str] = []
    timings: dict[str, float] = {}
    pipeline_started = time.perf_counter()

    if persist:
        workspace = RunWorkspace(
            output_root,
            run_id,
            brief=normalized,
            options={
                **options,
                "briefFingerprint": brief_fingerprint(normalized),
            },
        )

    try:
        _notify(
            progress, "validate", 5, "Brief validated and normalized"
        )
        if workspace:
            workspace.update(stage="validated", progress=5)

        started = time.perf_counter()
        _notify(
            progress,
            "generate",
            20,
            "Building deterministic story state",
        )
        if workspace:
            workspace.update(stage="generating", progress=20)
        film = build_film_from_brief(
            premise=normalized["premise"],
            genre=normalized["genre"],
            tone=normalized["tone"],
            title=normalized["title"],
            fmt=normalized["format"],
            scars=normalized["scars"],
            seed=normalized["seed"],
            cast=normalized["cast"],
        )
        _timed_stage(timings, "generationSeconds", started)

        started = time.perf_counter()
        _notify(
            progress,
            "repair",
            48,
            "Running bounded narrative TGRM repair",
        )
        if workspace:
            workspace.update(stage="repairing", progress=48)
        tgrm_result = run_tgrm(
            film,
            max_cycles=cycle_limit,
            energy_budget=budget,
        )
        repaired_state = tgrm_result.get("state") or film
        _timed_stage(timings, "repairSeconds", started)

        started = time.perf_counter()
        if render_media:
            _notify(
                progress,
                "media",
                72,
                "Starting checkpointed media production",
            )
            if workspace:
                workspace.update(
                    stage="rendering_media", progress=72
                )
                media_dir = workspace.media_dir
            else:
                media_dir = Path(
                    tempfile.mkdtemp(
                        prefix="silverscreen_media_"
                    )
                )
            media = process_media(
                repaired_state,
                images=images,
                voices=voices,
                out_dir=media_dir,
                max_chapters=chapter_limit,
                video_mode=video_mode,
                target_runtime_seconds=target_runtime_seconds,
                video_max_shots=video_max_shots,
                video_batch_size=video_batch_size,
                video_max_retries=video_max_retries,
                video_max_provider_calls=video_max_provider_calls,
                video_max_spend_usd=video_max_spend_usd,
                video_cost_per_second_usd=(
                    video_cost_per_second_usd
                ),
                video_continuous=video_continuous,
                video_resume=True,
                video_use_continuity=video_use_continuity,
                video_progress=_video_progress(
                    progress, workspace
                ),
            )
            for warning in media.get("warnings") or []:
                if warning and warning not in warnings:
                    warnings.append(str(warning))
        else:
            media = {
                "ok": True,
                "status": "skipped",
                "mode": "off",
                "chapter_paths": [],
                "card_paths": [],
                "video_paths": [],
                "hero_path": None,
                "final_video_path": None,
                "partial_video_path": None,
                "warnings": [],
                "note": "Media rendering was disabled.",
                "error": None,
            }
        _timed_stage(timings, "mediaSeconds", started)
        status = _pipeline_status(video_mode, media)
        if status == "failed":
            raise PipelineError(
                str(media.get("error") or "Media production failed"),
                run_id=run_id,
            )

        timings["totalSeconds"] = round(
            time.perf_counter() - pipeline_started, 4
        )
        result: dict[str, Any] = {
            "status": status,
            "run": {
                "id": run_id,
                "workspace": (
                    str(workspace.path) if workspace else None
                ),
                "persisted": bool(workspace),
                "briefFingerprint": brief_fingerprint(normalized),
            },
            "brief": normalized,
            "options": options,
            "film": film,
            "tgrm": tgrm_result,
            "state": repaired_state,
            "media": media,
            "metrics": tgrm_result.get("metrics", {}),
            "msil": tgrm_result.get("msil", {}),
            "videoMetrics": media.get("metrics") or {},
            "videoMsil": media.get("msil") or {},
            "log": tgrm_result.get("log", []),
            "scars": tgrm_result.get("scars", []),
            "remainingFractures": tgrm_result.get(
                "remainingFractures", []
            ),
            "warnings": warnings,
            "timings": timings,
            "artifacts": {},
        }
        if workspace:
            _notify(
                progress,
                "persist",
                90 if status == "complete" else 89,
                "Persisting production checkpoint and bundle",
            )
            _persist_and_finalize(workspace, result)
        _notify(
            progress,
            "complete" if status == "complete" else "checkpoint",
            100 if status == "complete" else 99,
            (
                "Production completed"
                if status == "complete"
                else "Production checkpoint saved"
            ),
        )
        return result
    except BriefValidationError:
        raise
    except PipelineError as exc:
        if workspace and workspace.manifest.get("status") == "running":
            workspace.fail(str(exc))
        raise
    except Exception as exc:
        if workspace:
            workspace.fail(str(exc))
        raise PipelineError(
            f"Silver-Screen pipeline failed: {exc}",
            run_id=run_id,
        ) from exc


def resume_video_run(
    run_id: str,
    *,
    output_root: str | None = "runs",
    batch_size: int | None = None,
    continuous: bool = False,
    max_retries: int | None = None,
    max_provider_calls: int | None = None,
    max_spend_usd: float | None = None,
    cost_per_second_usd: float | None = None,
    use_continuity: bool | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Resume only the persisted video queue without rebuilding the story."""

    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    state = result.get("state") or {}
    options = result.get("options") or {}
    if not state:
        raise PipelineError(
            "The run has no persisted film state", run_id=run_id
        )
    started = time.perf_counter()
    workspace.update(
        status="running",
        stage="video_resume",
        progress=max(
            72, int(workspace.manifest.get("progress", 72) or 72)
        ),
        error=None,
    )
    try:
        media = process_media(
            state,
            out_dir=workspace.media_dir,
            video_mode="ai-video",
            target_runtime_seconds=options.get(
                "targetRuntimeSeconds"
            ),
            video_max_shots=options.get("videoMaxShots"),
            video_batch_size=(
                batch_size
                if batch_size is not None
                else options.get("videoBatchSize")
            ),
            video_max_retries=(
                max_retries
                if max_retries is not None
                else options.get("videoMaxRetries")
            ),
            video_max_provider_calls=(
                max_provider_calls
                if max_provider_calls is not None
                else options.get("videoMaxProviderCalls")
            ),
            video_max_spend_usd=(
                max_spend_usd
                if max_spend_usd is not None
                else options.get("videoMaxSpendUsd")
            ),
            video_cost_per_second_usd=(
                cost_per_second_usd
                if cost_per_second_usd is not None
                else options.get("videoCostPerSecondUsd")
            ),
            video_continuous=continuous,
            video_resume=True,
            video_use_continuity=(
                use_continuity
                if use_continuity is not None
                else options.get("videoUseContinuity")
            ),
            video_progress=_video_progress(progress, workspace),
        )
        status = _pipeline_status("ai-video", media)
        if status == "failed":
            raise PipelineError(
                str(media.get("error") or "Video resume failed"),
                run_id=run_id,
            )
        result["status"] = status
        result["media"] = media
        result["videoMetrics"] = media.get("metrics") or {}
        result["videoMsil"] = media.get("msil") or {}
        result.setdefault("timings", {})[
            "lastResumeSeconds"
        ] = round(time.perf_counter() - started, 4)
        result["timings"]["resumeCount"] = int(
            result["timings"].get("resumeCount", 0) or 0
        ) + 1
        result["warnings"] = list(
            dict.fromkeys(
                [
                    *(result.get("warnings") or []),
                    *(media.get("warnings") or []),
                ]
            )
        )
        _persist_and_finalize(workspace, result)
        _notify(
            progress,
            "complete" if status == "complete" else "checkpoint",
            100 if status == "complete" else 99,
            (
                "Long-film target reached"
                if status == "complete"
                else "Next resumable checkpoint saved"
            ),
        )
        return result
    except PipelineError:
        raise
    except Exception as exc:
        workspace.fail(str(exc))
        raise PipelineError(
            f"Video resume failed: {exc}", run_id=run_id
        ) from exc


def video_run_status(
    run_id: str,
    output_root: str | None = "runs",
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    status = video_production_status(workspace.media_dir)
    if status is None:
        raise PipelineError(
            "The run has no video production queue",
            run_id=run_id,
        )
    return {
        "runId": run_id,
        "workspace": str(workspace.path),
        "manifest": workspace.manifest,
        "video": status,
    }


def run_pipeline_from_file(
    brief_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    path = Path(brief_path)
    brief = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(brief, dict):
        raise BriefValidationError(
            "brief file must contain a JSON object"
        )
    return run_pipeline(brief, **kwargs)
