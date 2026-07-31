"""Local visual quality inspection and consent-gated clip retakes.

The identity checks in this module are deliberately non-biometric. They compare
broad color, luminance, framing, and silhouette statistics only. They do not
recognize a person, create face embeddings, or identify an individual.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from .runtime import RunWorkspace, load_run, utc_now
from .video_runtime import load_video_queue, record_video_event, save_video_queue, update_video_metrics


class VisualQualityError(RuntimeError):
    """Raised when a clip or saved production cannot be inspected safely."""


def _float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def settings() -> dict[str, Any]:
    return {
        "acceptScore": _float_env("SILVER_SCREEN_VISUAL_ACCEPT_SCORE", 0.68, 0.35, 0.98),
        "hardRejectScore": _float_env("SILVER_SCREEN_VISUAL_HARD_REJECT_SCORE", 0.48, 0.20, 0.90),
        "minimumSharpness": _float_env("SILVER_SCREEN_VISUAL_MIN_SHARPNESS", 0.18, 0.02, 0.90),
        "maximumFlicker": _float_env("SILVER_SCREEN_VISUAL_MAX_FLICKER", 0.24, 0.03, 0.80),
        "maximumFreezeRatio": _float_env("SILVER_SCREEN_VISUAL_MAX_FREEZE_RATIO", 0.70, 0.20, 0.98),
        "minimumIdentityConsistency": _float_env("SILVER_SCREEN_VISUAL_MIN_IDENTITY", 0.52, 0.15, 0.95),
        "sampleFrames": max(4, min(16, int(os.getenv("SILVER_SCREEN_VISUAL_SAMPLE_FRAMES", "8") or 8))),
    }


def _ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _sample_frames(clip: Path, destination: Path, count: int) -> list[Path]:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise VisualQualityError("FFmpeg is required for visual-quality inspection")
    destination.mkdir(parents=True, exist_ok=True)
    for old in destination.glob("frame_*.jpg"):
        old.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(clip),
        "-vf",
        f"fps={max(1, count)}/8,scale=384:-2",
        "-frames:v",
        str(count),
        "-q:v",
        "3",
        str(destination / "frame_%03d.jpg"),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    frames = sorted(destination.glob("frame_*.jpg"))
    if completed.returncode != 0 or len(frames) < 2:
        raise VisualQualityError(f"Could not sample clip frames: {completed.stderr[-900:]}")
    return frames


def _array(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB").resize((192, 108)), dtype=np.float32) / 255.0


def _sharpness(array: np.ndarray) -> float:
    gray = array.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1)).mean()
    gy = np.abs(np.diff(gray, axis=0)).mean()
    return float(max(0.0, min(1.0, (gx + gy) * 4.5)))


def _histogram(array: np.ndarray) -> np.ndarray:
    parts = []
    for channel in range(3):
        hist, _ = np.histogram(array[:, :, channel], bins=16, range=(0, 1), density=True)
        parts.append(hist.astype(np.float64))
    vector = np.concatenate(parts)
    total = float(vector.sum()) or 1.0
    return vector / total


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 0.0 if denominator <= 0 else float(max(0.0, min(1.0, np.dot(a, b) / denominator)))


def _reference_vectors(reference_paths: list[Path]) -> list[np.ndarray]:
    vectors: list[np.ndarray] = []
    for path in reference_paths[:6]:
        try:
            vectors.append(_histogram(_array(path)))
        except Exception:
            continue
    return vectors


def analyze_clip(
    clip: str | os.PathLike[str],
    *,
    work_dir: str | os.PathLike[str] | None = None,
    reference_paths: list[str | os.PathLike[str]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze one MP4 locally and return an explainable quality report."""

    cfg = {**settings(), **(config or {})}
    path = Path(clip).resolve()
    if not path.exists():
        raise VisualQualityError(f"Clip does not exist: {path}")
    root = Path(work_dir).resolve() if work_dir else path.parent / ".visual_quality" / path.stem
    frames = _sample_frames(path, root, int(cfg["sampleFrames"]))
    arrays = [_array(frame) for frame in frames]
    sharpness_values = [_sharpness(item) for item in arrays]
    luminance = [float(item.mean()) for item in arrays]
    contrast = [float(item.std()) for item in arrays]
    diffs = [float(np.abs(arrays[index] - arrays[index - 1]).mean()) for index in range(1, len(arrays))]
    flicker = float(np.std(luminance) / max(0.05, np.mean(luminance)))
    freeze_ratio = float(sum(value < 0.008 for value in diffs) / max(1, len(diffs)))
    clipping = float(np.mean([(item < 0.015).mean() + (item > 0.985).mean() for item in arrays]) / 2.0)

    references = [Path(value).resolve() for value in (reference_paths or []) if Path(value).exists()]
    ref_vectors = _reference_vectors(references)
    frame_vectors = [_histogram(item) for item in arrays]
    identity_scores = [max((_similarity(vector, ref) for ref in ref_vectors), default=1.0) for vector in frame_vectors]
    identity_consistency = float(np.mean(identity_scores)) if identity_scores else 1.0
    internal_consistency = float(np.mean([_similarity(frame_vectors[index], frame_vectors[index - 1]) for index in range(1, len(frame_vectors))]))

    sharpness_score = float(np.mean(sharpness_values))
    exposure_score = float(max(0.0, 1.0 - abs(float(np.mean(luminance)) - 0.48) * 1.8 - clipping * 1.8))
    contrast_score = float(max(0.0, min(1.0, float(np.mean(contrast)) * 5.0)))
    motion_score = float(max(0.0, 1.0 - max(0.0, freeze_ratio - 0.18) * 1.8))
    stability_score = float(max(0.0, 1.0 - flicker * 2.2))
    score = float(
        0.23 * sharpness_score
        + 0.16 * exposure_score
        + 0.10 * contrast_score
        + 0.16 * motion_score
        + 0.15 * stability_score
        + 0.12 * internal_consistency
        + 0.08 * identity_consistency
    )

    findings: list[dict[str, Any]] = []
    def add(code: str, severity: str, message: str, repair: str) -> None:
        findings.append({"code": code, "severity": severity, "message": message, "repair": repair})

    if sharpness_score < float(cfg["minimumSharpness"]):
        add("soft_or_blurred", "high", "The sampled frames are too soft or blurred.", "Request a stable, sharply focused retake with restrained camera motion.")
    if flicker > float(cfg["maximumFlicker"]):
        add("flicker", "high", "Luminance changes abruptly across the clip.", "Lock practical lighting and exposure; prohibit pulsing or flashing illumination.")
    if freeze_ratio > float(cfg["maximumFreezeRatio"]):
        add("frozen_motion", "medium", "Too many sampled intervals are nearly identical.", "Require one physically readable action and subtle natural performance motion.")
    if clipping > 0.18:
        add("exposure_clipping", "medium", "Large image regions are crushed or blown out.", "Use controlled highlight rolloff and preserve shadow detail.")
    if ref_vectors and identity_consistency < float(cfg["minimumIdentityConsistency"]):
        add("appearance_drift", "high", "Broad appearance statistics drift from the authorized reference pack.", "Re-anchor the authorized identity, wardrobe palette, age appearance, and body proportions.")
    if internal_consistency < 0.62:
        add("intra_clip_drift", "high", "The clip changes appearance too strongly within a single shot.", "Keep one actor, wardrobe state, environment, lens, and lighting setup throughout the shot.")

    hard_failure = any(item["severity"] == "high" for item in findings) or score < float(cfg["hardRejectScore"])
    accepted = not hard_failure and score >= float(cfg["acceptScore"])
    rating = "accepted" if accepted else ("reject" if hard_failure else "review")
    directive = " ".join(str(item["repair"]) for item in findings)[:2200]
    return {
        "schemaVersion": 1,
        "analyzedAt": utc_now(),
        "clip": str(path),
        "score": round(score, 6),
        "scorePercent": round(score * 100, 1),
        "rating": rating,
        "accepted": accepted,
        "hardFailure": hard_failure,
        "metrics": {
            "sharpness": round(sharpness_score, 6),
            "exposure": round(exposure_score, 6),
            "contrast": round(contrast_score, 6),
            "motion": round(motion_score, 6),
            "stability": round(stability_score, 6),
            "flicker": round(flicker, 6),
            "freezeRatio": round(freeze_ratio, 6),
            "clipping": round(clipping, 6),
            "internalConsistency": round(internal_consistency, 6),
            "referenceAppearanceConsistency": round(identity_consistency, 6),
            "sampledFrames": len(frames),
        },
        "findings": findings,
        "repairDirective": directive,
        "identityMethod": "non-biometric broad appearance comparison",
    }


def _shot_path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = shot.get("path")
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else (root / path).resolve()


def inspect_run(run_id: str, *, output_root: str = "runs") -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    root = workspace.media_dir.resolve()
    queue = load_video_queue(root)
    if queue is None:
        raise VisualQualityError("The selected run has no durable video queue")
    identity_root = workspace.path / "identity"
    references = sorted(identity_root.glob("**/*")) if identity_root.exists() else []
    references = [path for path in references if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    reports: list[dict[str, Any]] = []
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict) or shot.get("status") != "verified":
            continue
        clip = _shot_path(root, shot)
        if clip is None or not clip.exists():
            continue
        report = analyze_clip(
            clip,
            work_dir=root / "visual_quality" / str(shot.get("id") or "shot"),
            reference_paths=[str(path) for path in references],
        )
        report["shotId"] = shot.get("id")
        report["order"] = shot.get("order")
        report["scene"] = (shot.get("sourceScene") or {}).get("number")
        shot["visualQuality"] = report
        reports.append(report)
    reports.sort(key=lambda item: int(item.get("order", 0) or 0))
    scores = [float(item.get("score", 0) or 0) for item in reports]
    summary = {
        "schemaVersion": 1,
        "runId": run_id,
        "analyzedAt": utc_now(),
        "clips": len(reports),
        "accepted": sum(bool(item.get("accepted")) for item in reports),
        "review": sum(item.get("rating") == "review" for item in reports),
        "rejected": sum(item.get("rating") == "reject" for item in reports),
        "averageScore": round(sum(scores) / len(scores), 6) if scores else 0.0,
        "minimumScore": round(min(scores), 6) if scores else 0.0,
        "reports": reports,
    }
    (root / "visual_quality_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    queue["visualQualityReport"] = summary
    save_video_queue(root, queue)
    return {"result": result, "queue": queue, "report": summary}


def schedule_quality_retake(
    run_id: str,
    shot_id: str,
    *,
    output_root: str = "runs",
    reason: str | None = None,
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    root = workspace.media_dir.resolve()
    queue = load_video_queue(root)
    if queue is None:
        raise VisualQualityError("The selected run has no durable video queue")
    shot = next((item for item in queue.get("shots") or [] if isinstance(item, dict) and str(item.get("id") or "") == shot_id), None)
    if shot is None or shot.get("status") != "verified":
        raise VisualQualityError("The selected clip is not currently verified")
    source = _shot_path(root, shot)
    if source is None or not source.exists():
        raise VisualQualityError("The selected clip file is missing")
    report = shot.get("visualQuality") or analyze_clip(source, work_dir=root / "visual_quality" / shot_id)
    history = shot.setdefault("visualQualityRetakeHistory", [])
    maximum = max(1, min(5, int(os.getenv("SILVER_SCREEN_VISUAL_MAX_RETAKES", "2") or 2)))
    if len(history) >= maximum:
        raise VisualQualityError(f"The {maximum}-retake safety limit has been reached for this clip")
    retake_number = len(history) + 1
    archive = root / "visual_quality" / "retakes" / shot_id / f"accepted_before_retake_{retake_number:02d}.mp4"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, archive)
    history.append({
        "retakeNumber": retake_number,
        "candidatePath": archive.relative_to(root).as_posix(),
        "previousReport": deepcopy(report),
        "scheduledAt": utc_now(),
        "status": "preserved",
    })
    directive = " ".join(
        part for part in [
            "TARGETED VISUAL QUALITY RETAKE: preserve the approved story beat and change only the failed visual properties.",
            str(report.get("repairDirective") or ""),
            str(reason or "").strip(),
            "Preserve identity, wardrobe, screen direction, setting, action, lens language, and duration unless a listed repair requires a minimal change.",
        ] if part
    )[:2600]
    shot["visualQualityRetake"] = {
        "status": "scheduled",
        "retakeNumber": retake_number,
        "directive": directive,
        "previousScore": report.get("score"),
        "scheduledAt": utc_now(),
    }
    shot["status"] = "pending"
    shot["path"] = None
    shot["verifiedDurationSeconds"] = 0.0
    shot["verification"] = {}
    shot["providerPredictionId"] = None
    shot["providerPredictionUrl"] = None
    shot["providerStatus"] = None
    shot["completedAt"] = None
    shot["startedAt"] = None
    shot["lastError"] = "Visual Quality Supervisor retake requested"
    config = queue.setdefault("config", {})
    current_calls = int((queue.get("metrics") or {}).get("providerCalls", 0) or 0)
    configured = int(config.get("max_provider_calls", 0) or 0)
    config["max_provider_calls"] = max(current_calls + 1, configured + 1 if configured > 0 else current_calls + 1)
    queue["status"] = "partial"
    queue["stopReason"] = "visual_quality_retake_scheduled"
    queue["completedAt"] = None
    update_video_metrics(queue)
    record_video_event(queue, "visual_quality_retake_scheduled", shot_id=shot_id, data={"previousScore": report.get("score"), "retakeNumber": retake_number})
    save_video_queue(root, queue)
    return {"runId": run_id, "shot": shot, "queue": queue, "archivedCandidate": str(archive)}


__all__ = ["VisualQualityError", "analyze_clip", "inspect_run", "schedule_quality_retake", "settings"]
