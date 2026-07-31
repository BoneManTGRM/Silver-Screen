"""Semantic shot supervision for the Autonomous Director.

The local path never pretends to understand image content that it cannot verify.
It produces a provisional contract-completeness score from durable production
metadata. When OPENAI_API_KEY is configured and semantic review is enabled, a
small set of sampled frames is evaluated against the approved shot contract and
production memory using OpenAI's multimodal Responses API.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .production_memory import memory_prompt_context
from .runtime import RunWorkspace, atomic_write_json, load_run, utc_now
from .video_runtime import load_video_queue, save_video_queue


class SemanticSupervisorError(RuntimeError):
    """Raised when semantic inspection cannot be completed safely."""


DIMENSIONS = (
    "storyBeat",
    "actionAccuracy",
    "characterAccuracy",
    "performanceAccuracy",
    "compositionAccuracy",
    "propAndWardrobeAccuracy",
    "continuityAccuracy",
    "worldConsistency",
)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off", ""}


def _float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def settings() -> dict[str, Any]:
    return {
        "enabled": _bool_env("SILVER_SCREEN_SEMANTIC_REVIEW", True),
        "providerEnabled": bool(os.getenv("OPENAI_API_KEY"))
        and _bool_env("SILVER_SCREEN_SEMANTIC_PROVIDER", True),
        "model": (
            os.getenv("SILVER_SCREEN_SEMANTIC_MODEL") or "gpt-5-mini"
        ).strip(),
        "acceptScore": _float_env(
            "SILVER_SCREEN_SEMANTIC_ACCEPT_SCORE", 0.76, 0.35, 0.98
        ),
        "hardRejectScore": _float_env(
            "SILVER_SCREEN_SEMANTIC_HARD_REJECT_SCORE", 0.46, 0.20, 0.90
        ),
        "sampleFrames": _int_env(
            "SILVER_SCREEN_SEMANTIC_SAMPLE_FRAMES", 4, 2, 8
        ),
        "maxImageBytes": _int_env(
            "SILVER_SCREEN_SEMANTIC_MAX_IMAGE_BYTES", 360_000, 80_000, 1_500_000
        ),
        "timeoutSeconds": _int_env(
            "SILVER_SCREEN_SEMANTIC_TIMEOUT_SECONDS", 180, 30, 600
        ),
        "gate": _bool_env("SILVER_SCREEN_SEMANTIC_GATE", False),
    }


def _clean(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _sample_frames(
    clip: Path,
    destination: Path,
    count: int,
) -> list[Path]:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise SemanticSupervisorError(
            "FFmpeg is required for semantic frame sampling"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for old in destination.glob("semantic_*.jpg"):
        old.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(clip),
        "-vf",
        f"fps={max(2, count)}/8,scale='min(768,iw)':-2",
        "-frames:v",
        str(count),
        "-q:v",
        "4",
        str(destination / "semantic_%03d.jpg"),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
    )
    frames = sorted(destination.glob("semantic_*.jpg"))
    if completed.returncode != 0 or len(frames) < 2:
        raise SemanticSupervisorError(
            "Could not sample semantic-review frames: "
            + completed.stderr[-800:]
        )
    return frames


def _image_data_url(path: Path, max_bytes: int) -> str:
    data = path.read_bytes()
    if len(data) > max_bytes:
        try:
            from PIL import Image

            with Image.open(path) as image:
                image = image.convert("RGB")
                if image.width > 640:
                    height = max(2, round(image.height * 640 / image.width))
                    image = image.resize((640, height))
                for quality in (72, 60, 48, 36):
                    with tempfile.NamedTemporaryFile(suffix=".jpg") as handle:
                        image.save(handle.name, format="JPEG", quality=quality)
                        compressed = Path(handle.name).read_bytes()
                    if len(compressed) <= max_bytes:
                        data = compressed
                        break
        except Exception:
            data = data[:max_bytes]
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def _scene_for_shot(
    state: dict[str, Any], shot: dict[str, Any]
) -> dict[str, Any]:
    number = int(((shot.get("sourceScene") or {}).get("number", 1)) or 1)
    for scene in state.get("scenes") or []:
        if isinstance(scene, dict) and int(scene.get("number", -1) or -1) == number:
            return scene
    return next(
        (item for item in state.get("scenes") or [] if isinstance(item, dict)),
        {},
    )


def build_shot_contract(
    state: dict[str, Any], shot: dict[str, Any]
) -> dict[str, Any]:
    scene = _scene_for_shot(state, shot)
    blueprint = shot.get("shotBlueprint") or {}
    if not blueprint:
        try:
            from .shot_director import select_shot_blueprint

            blueprint = select_shot_blueprint(state, scene, shot)
        except Exception:
            blueprint = {}
    characters: list[dict[str, Any]] = []
    character_map = {
        str(item.get("id") or ""): item
        for item in state.get("characters") or []
        if isinstance(item, dict)
    }
    for raw_id in scene.get("characters") or []:
        item = character_map.get(str(raw_id), {})
        if item:
            characters.append(
                {
                    "name": _clean(item.get("name"), 100),
                    "role": _clean(item.get("role"), 200),
                    "description": _clean(item.get("description"), 1200),
                }
            )
    if not characters:
        for item in list(character_map.values())[:4]:
            characters.append(
                {
                    "name": _clean(item.get("name"), 100),
                    "role": _clean(item.get("role"), 200),
                    "description": _clean(item.get("description"), 1200),
                }
            )
    world_context = memory_prompt_context(state, scene, shot, max_chars=1800)
    return {
        "shotId": _clean(shot.get("id"), 100),
        "order": int(shot.get("order", 0) or 0),
        "scene": int(scene.get("number", 1) or 1),
        "setting": _clean(scene.get("slugline"), 260),
        "sceneSummary": _clean(
            scene.get("summary") or scene.get("action"), 1600
        ),
        "conflict": _clean(scene.get("conflict"), 800),
        "turn": _clean(scene.get("turn"), 800),
        "shotType": _clean(blueprint.get("type"), 120),
        "shotObjective": _clean(
            blueprint.get("description")
            or scene.get("action")
            or scene.get("summary"),
            1800,
        ),
        "dialogue": _clean(blueprint.get("dialogue"), 1200),
        "directorOverride": _clean(blueprint.get("override"), 1200),
        "alternateCoverage": _clean(
            blueprint.get("alternateCoverage"), 800
        ),
        "characters": characters,
        "continuityUsed": bool(shot.get("continuityUsed")),
        "worldMemory": world_context,
        "negativePrompt": _clean(shot.get("negativePrompt"), 1800),
    }


def _local_report(
    contract: dict[str, Any],
    shot: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "storyBeat": bool(contract.get("sceneSummary") or contract.get("conflict")),
        "actionAccuracy": bool(contract.get("shotObjective")),
        "characterAccuracy": bool(contract.get("characters")),
        "performanceAccuracy": bool(
            contract.get("shotObjective") or contract.get("dialogue")
        ),
        "compositionAccuracy": bool(
            contract.get("shotType") or contract.get("directorOverride")
        ),
        "propAndWardrobeAccuracy": bool(contract.get("worldMemory")),
        "continuityAccuracy": bool(
            int(contract.get("order", 0) or 0) == 1
            or contract.get("continuityUsed")
        ),
        "worldConsistency": bool(contract.get("worldMemory")),
    }
    dimensions = {
        key: (0.82 if value else 0.48) for key, value in checks.items()
    }
    visual = shot.get("visualQuality") or {}
    visual_score = float(visual.get("score", 0.72) or 0.72)
    contract_score = sum(dimensions.values()) / len(dimensions)
    score = 0.70 * contract_score + 0.30 * visual_score
    findings: list[dict[str, Any]] = []
    for key, present in checks.items():
        if present:
            continue
        findings.append(
            {
                "code": f"unverifiable_{key}",
                "severity": "low",
                "confidence": 0.55,
                "message": (
                    f"The local reviewer lacks enough structured evidence to verify {key}."
                ),
                "repair": (
                    "Make the shot objective and corresponding world-state requirement explicit."
                ),
            }
        )
    return {
        "schemaVersion": 1,
        "analyzedAt": utc_now(),
        "method": "local contract-completeness heuristic",
        "evidenceQuality": "provisional",
        "provider": None,
        "model": None,
        "score": round(score, 6),
        "scorePercent": round(score * 100, 1),
        "rating": "provisional",
        "accepted": None,
        "hardFailure": False,
        "dimensions": dimensions,
        "findings": findings,
        "observedSummary": (
            "No remote multimodal reviewer was used. The report confirms that the "
            "shot has a complete, reviewable production contract but does not claim "
            "to recognize the visual content."
        ),
        "repairDirective": " ".join(
            str(item.get("repair") or "") for item in findings
        )[:2200],
        "contract": contract,
        "thresholds": {
            "acceptScore": cfg["acceptScore"],
            "hardRejectScore": cfg["hardRejectScore"],
        },
    }


def _schema() -> dict[str, Any]:
    finding = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "code": {"type": "string"},
            "severity": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "message": {"type": "string"},
            "repair": {"type": "string"},
        },
        "required": ["code", "severity", "confidence", "message", "repair"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "observedSummary": {"type": "string"},
            "dimensions": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    key: {"type": "number", "minimum": 0, "maximum": 1}
                    for key in DIMENSIONS
                },
                "required": list(DIMENSIONS),
            },
            "findings": {"type": "array", "items": finding, "maxItems": 12},
        },
        "required": ["observedSummary", "dimensions", "findings"],
    }


def _extract_response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for output in payload.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            value = content.get("text") or content.get("output_text")
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _provider_report(
    contract: dict[str, Any],
    frames: list[Path],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    token = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not token:
        raise SemanticSupervisorError("OPENAI_API_KEY is not configured")
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "You are the semantic shot supervisor for a professional film. "
                "Compare the sampled frames only against the supplied approved shot "
                "contract. Do not identify any real person. Do not infer sensitive "
                "traits. Judge only visible story action, character/wardrobe/prop "
                "consistency, performance, composition, continuity, and contradictions. "
                "Use low confidence when the frames cannot establish a fact.\n\n"
                "APPROVED SHOT CONTRACT:\n"
                + json.dumps(contract, ensure_ascii=False, indent=2)[:18_000]
            ),
        }
    ]
    for frame in frames:
        content.append(
            {
                "type": "input_image",
                "image_url": _image_data_url(frame, int(cfg["maxImageBytes"])),
                "detail": "low",
            }
        )
    request_payload = {
        "model": cfg["model"],
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "semantic_shot_report",
                "strict": True,
                "schema": _schema(),
            }
        },
        "max_output_tokens": 1600,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=int(cfg["timeoutSeconds"])
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SemanticSupervisorError(
            f"Semantic reviewer returned HTTP {exc.code}: {detail[:1500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SemanticSupervisorError(
            f"Could not reach semantic reviewer: {exc.reason}"
        ) from exc
    try:
        response_payload = json.loads(raw)
        result_text = _extract_response_text(response_payload)
        result = json.loads(result_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SemanticSupervisorError(
            "Semantic reviewer returned invalid structured output"
        ) from exc
    if not isinstance(result, dict):
        raise SemanticSupervisorError(
            "Semantic reviewer returned an unexpected result"
        )
    dimensions = {
        key: max(
            0.0,
            min(1.0, float((result.get("dimensions") or {}).get(key, 0.0) or 0.0)),
        )
        for key in DIMENSIONS
    }
    weights = {
        "storyBeat": 0.18,
        "actionAccuracy": 0.18,
        "characterAccuracy": 0.13,
        "performanceAccuracy": 0.12,
        "compositionAccuracy": 0.11,
        "propAndWardrobeAccuracy": 0.10,
        "continuityAccuracy": 0.10,
        "worldConsistency": 0.08,
    }
    score = sum(dimensions[key] * weights[key] for key in DIMENSIONS)
    findings = [
        item
        for item in result.get("findings") or []
        if isinstance(item, dict)
    ][:12]
    high_confidence_failure = any(
        str(item.get("severity") or "") == "high"
        and float(item.get("confidence", 0) or 0) >= 0.78
        for item in findings
    )
    hard_failure = high_confidence_failure or score < float(
        cfg["hardRejectScore"]
    )
    accepted = not hard_failure and score >= float(cfg["acceptScore"])
    rating = "accepted" if accepted else ("reject" if hard_failure else "review")
    return {
        "schemaVersion": 1,
        "analyzedAt": utc_now(),
        "method": "OpenAI multimodal contract comparison",
        "evidenceQuality": "provider",
        "provider": "openai",
        "model": cfg["model"],
        "score": round(score, 6),
        "scorePercent": round(score * 100, 1),
        "rating": rating,
        "accepted": accepted,
        "hardFailure": hard_failure,
        "dimensions": dimensions,
        "findings": findings,
        "observedSummary": _clean(result.get("observedSummary"), 2000),
        "repairDirective": " ".join(
            _clean(item.get("repair"), 500) for item in findings
        )[:2600],
        "contract": contract,
        "thresholds": {
            "acceptScore": cfg["acceptScore"],
            "hardRejectScore": cfg["hardRejectScore"],
        },
    }


def analyze_semantic_shot(
    clip: str | os.PathLike[str],
    state: dict[str, Any],
    shot: dict[str, Any],
    *,
    work_dir: str | os.PathLike[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = {**settings(), **(config or {})}
    contract = build_shot_contract(state, shot)
    provisional = _local_report(contract, shot, cfg)
    if not cfg.get("enabled") or not cfg.get("providerEnabled"):
        return provisional
    path = Path(clip).resolve()
    if not path.exists():
        raise SemanticSupervisorError(f"Clip does not exist: {path}")
    root = (
        Path(work_dir).resolve()
        if work_dir
        else path.parent / ".semantic_review" / path.stem
    )
    try:
        frames = _sample_frames(path, root, int(cfg["sampleFrames"]))
        report = _provider_report(contract, frames, cfg)
        report["sampledFrames"] = len(frames)
        return report
    except SemanticSupervisorError as exc:
        provisional["providerError"] = str(exc)
        provisional["observedSummary"] += (
            " The configured multimodal reviewer was unavailable, so no semantic "
            "content claim was made."
        )
        return provisional


def _shot_path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = shot.get("path")
    if not value:
        return None
    candidate = Path(str(value))
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise SemanticSupervisorError("Shot artifact escaped the production workspace")
    return resolved


def inspect_semantic_run(
    run_id: str,
    *,
    output_root: str = "runs",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    state = result.get("state") or {}
    queue = load_video_queue(workspace.media_dir)
    if queue is None:
        raise SemanticSupervisorError("The selected run has no durable video queue")
    reports: list[dict[str, Any]] = []
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict) or shot.get("status") != "verified":
            continue
        clip = _shot_path(workspace.media_dir, shot)
        if clip is None or not clip.exists():
            continue
        report = analyze_semantic_shot(
            clip,
            state,
            shot,
            work_dir=(
                workspace.media_dir
                / "semantic_review"
                / str(shot.get("id") or "shot")
            ),
            config=config,
        )
        report.update(
            {
                "shotId": shot.get("id"),
                "order": shot.get("order"),
                "scene": (shot.get("sourceScene") or {}).get("number"),
            }
        )
        shot["semanticQuality"] = report
        reports.append(report)
    reports.sort(key=lambda item: int(item.get("order", 0) or 0))
    usable = [
        item
        for item in reports
        if item.get("evidenceQuality") == "provider"
    ]
    scores = [float(item.get("score", 0) or 0) for item in reports]
    summary = {
        "schemaVersion": 1,
        "runId": run_id,
        "analyzedAt": utc_now(),
        "clips": len(reports),
        "providerReviewed": len(usable),
        "accepted": sum(item.get("accepted") is True for item in reports),
        "review": sum(item.get("rating") == "review" for item in reports),
        "rejected": sum(item.get("rating") == "reject" for item in reports),
        "provisional": sum(
            item.get("evidenceQuality") != "provider" for item in reports
        ),
        "averageScore": round(sum(scores) / len(scores), 6) if scores else 0.0,
        "minimumScore": round(min(scores), 6) if scores else 0.0,
        "reports": reports,
    }
    path = workspace.media_dir / "semantic_shot_report.json"
    atomic_write_json(path, summary)
    queue["semanticShotReport"] = summary
    save_video_queue(workspace.media_dir, queue)
    workspace.register_artifact("semanticShotReport", path)
    return {
        "result": result,
        "queue": queue,
        "report": summary,
        "reportPath": str(path),
    }


__all__ = [
    "DIMENSIONS",
    "SemanticSupervisorError",
    "analyze_semantic_shot",
    "build_shot_contract",
    "inspect_semantic_run",
    "settings",
]
