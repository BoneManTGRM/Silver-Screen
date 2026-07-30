"""Operational end-to-end pipeline for Silver-Screen."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .media import process_media
from .runtime import RunWorkspace, create_run_id
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
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _normalize_genre(value: Any) -> str:
    token = _normalize_token(value or "scifi")
    normalized = GENRE_ALIASES.get(token, token.replace("-", "").replace(" ", ""))
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
        raise BriefValidationError("cast must be a list of character objects")
    cast: list[dict[str, str]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise BriefValidationError(f"cast entry {index + 1} must be an object")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise BriefValidationError(f"cast entry {index + 1} requires a name")
        cast.append(
            {
                "name": name[:80],
                "role": str(raw.get("role") or "Supporting character")[:120],
                "description": str(raw.get("description") or "")[:400],
                "arc": str(raw.get("arc") or "From fracture to a tested new belief")[:160],
            }
        )
        if len(cast) >= 6:
            break
    if cast and len(cast) < 2:
        raise BriefValidationError("custom cast requires at least two named characters")
    return cast


def validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a production brief without mutating the caller."""

    if not isinstance(brief, dict):
        raise BriefValidationError("brief must be a JSON object or Python dictionary")
    premise = str(brief.get("premise") or "").strip()
    if len(premise) < 12:
        raise BriefValidationError("premise must contain at least 12 characters")
    if len(premise) > 4000:
        raise BriefValidationError("premise must not exceed 4,000 characters")

    title_value = str(brief.get("title") or "").strip()
    if len(title_value) > 120:
        raise BriefValidationError("title must not exceed 120 characters")
    genre = _normalize_genre(brief.get("genre"))
    tone = _normalize_tone(brief.get("tone"))
    fmt = _normalize_format(brief.get("format") or brief.get("fmt"))

    raw_seed = brief.get("seed")
    if raw_seed in (None, ""):
        seed = derive_seed(premise, genre, tone, fmt)
    else:
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError) as exc:
            raise BriefValidationError("seed must be an integer") from exc
        if not 0 <= seed <= 2_147_483_647:
            raise BriefValidationError("seed must be between 0 and 2,147,483,647")

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
    payload = json.dumps(brief, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _notify(callback: ProgressCallback | None, stage: str, percent: int, message: str) -> None:
    if callback is None:
        return
    try:
        callback(stage, percent, message)
    except Exception:
        return


def _timed_stage(timings: dict[str, float], name: str, started: float) -> None:
    timings[name] = round(time.perf_counter() - started, 4)


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
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run validation, generation, TGRM repair, media, and artifact persistence."""

    normalized = validate_brief(brief)
    cycle_limit = max(1, min(20, int(max_cycles or SCIENCE["maxCycles"])))
    budget = max(3, min(500, int(energy_budget or SCIENCE["energyBudget"])))
    chapter_limit = max(1, min(12, int(max_chapters)))
    options = {
        "persist": bool(persist),
        "renderMedia": bool(render_media),
        "videoMode": video_mode,
        "maxChapters": chapter_limit,
        "maxCycles": cycle_limit,
        "energyBudget": budget,
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
            options={**options, "briefFingerprint": brief_fingerprint(normalized)},
        )

    try:
        _notify(progress, "validate", 5, "Brief validated and normalized")
        if workspace:
            workspace.update(stage="validated", progress=5)

        started = time.perf_counter()
        _notify(progress, "generate", 20, "Building deterministic story state")
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
        _notify(progress, "repair", 48, "Running bounded TGRM verification and repair")
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
        media: dict[str, Any]
        if render_media:
            _notify(progress, "media", 72, "Rendering controlled media artifacts")
            if workspace:
                workspace.update(stage="rendering_media", progress=72)
                media_dir = workspace.media_dir
            else:
                media_dir = Path(tempfile.mkdtemp(prefix="silverscreen_media_"))
            media = process_media(
                repaired_state,
                images=images,
                voices=voices,
                out_dir=media_dir,
                max_chapters=chapter_limit,
                video_mode=video_mode,
            )
            for warning in media.get("warnings") or []:
                if warning not in warnings:
                    warnings.append(str(warning))
            if media.get("error"):
                warnings.append(f"Media degraded safely: {media['error']}")
        else:
            media = {
                "ok": True,
                "status": "skipped",
                "mode": "off",
                "chapter_paths": [],
                "card_paths": [],
                "video_paths": [],
                "hero_path": None,
                "warnings": [],
                "note": "Media rendering was disabled for this run.",
                "error": None,
            }
        _timed_stage(timings, "mediaSeconds", started)

        result: dict[str, Any] = {
            "status": "complete",
            "run": {
                "id": run_id,
                "workspace": str(workspace.path) if workspace else None,
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
            "log": tgrm_result.get("log", []),
            "scars": tgrm_result.get("scars", []),
            "remainingFractures": tgrm_result.get("remainingFractures", []),
            "warnings": warnings,
            "timings": timings,
            "artifacts": {},
        }

        if workspace:
            started = time.perf_counter()
            _notify(progress, "persist", 90, "Writing manifest and production bundle")
            workspace.update(stage="persisting", progress=90)
            for warning in warnings:
                workspace.update(warning=warning)

            for index, path in enumerate(media.get("card_paths") or [], start=1):
                workspace.register_artifact(f"chapterCard{index:02d}", path)
            for index, path in enumerate(media.get("video_paths") or [], start=1):
                workspace.register_artifact(f"chapterVideo{index:02d}", path)
            if media.get("hero_path"):
                workspace.register_artifact("heroReel", media["hero_path"])

            core_artifacts = workspace.persist_result(result)
            result["artifacts"].update(core_artifacts)
            preliminary_bundle = workspace.build_bundle(str(repaired_state.get("title") or run_id))
            result["artifacts"]["bundle"] = str(preliminary_bundle)
            workspace.write_json("result.json", result)
            workspace.complete(
                {
                    "metrics": result["metrics"],
                    "msil": result["msil"],
                    "timings": timings,
                    "title": repaired_state.get("title"),
                }
            )
            final_bundle = workspace.build_bundle(str(repaired_state.get("title") or run_id))
            result["artifacts"]["bundle"] = str(final_bundle)
            workspace.write_json("result.json", result)
            _timed_stage(timings, "persistenceSeconds", started)

        timings["totalSeconds"] = round(time.perf_counter() - pipeline_started, 4)
        result["timings"] = timings
        if workspace:
            workspace.update(extra={"timings": timings})
            workspace.write_json("result.json", result)
            workspace.build_bundle(str(repaired_state.get("title") or run_id))

        _notify(progress, "complete", 100, "Production run completed")
        return result
    except BriefValidationError:
        raise
    except Exception as exc:
        if workspace:
            workspace.fail(str(exc))
        raise PipelineError(
            f"Silver-Screen pipeline failed during the current run: {exc}",
            run_id=run_id,
        ) from exc


def run_pipeline_from_file(
    brief_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    path = Path(brief_path)
    brief = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(brief, dict):
        raise BriefValidationError("brief file must contain a JSON object")
    return run_pipeline(brief, **kwargs)
