"""Install automatic clip-quality verification after AI video generation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .visual_quality import VisualQualityError, analyze_clip


def _enabled() -> bool:
    return os.getenv("SILVER_SCREEN_VISUAL_QUALITY_GATE", "1").strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }


def install_visual_quality_supervisor() -> None:
    from . import ai_video, pipeline

    if getattr(pipeline, "_visual_quality_supervisor_installed", False):
        return

    original_process = ai_video._process_prediction
    previous_scene_prompt = ai_video.scene_prompt

    def scene_prompt(
        state: dict[str, Any],
        scene: dict[str, Any],
        shot: dict[str, Any] | None = None,
        repair: dict[str, Any] | None = None,
    ) -> str:
        prompt = previous_scene_prompt(state, scene, shot, repair)
        directive = str(((shot or {}).get("visualQualityRetake") or {}).get("directive") or "").strip()
        if directive:
            prompt = f"{prompt} {directive}"[:3500]
        return prompt

    def process_prediction(**kwargs: Any) -> None:
        original_process(**kwargs)
        if not _enabled():
            return
        shot = kwargs.get("shot") or {}
        root = Path(kwargs.get("root")).resolve()
        path_value = shot.get("path")
        if not path_value:
            return
        clip = Path(str(path_value))
        if not clip.is_absolute():
            clip = (root / clip).resolve()
        try:
            report = analyze_clip(
                clip,
                work_dir=root / "visual_quality" / str(shot.get("id") or "shot"),
            )
        except VisualQualityError as exc:
            # Synthetic test artifacts, constrained hosts, or unavailable FFmpeg
            # should not convert an otherwise verified provider clip into a false
            # rejection. The saved-production supervisor can inspect it later.
            shot["visualQuality"] = {
                "schemaVersion": 1,
                "rating": "unavailable",
                "accepted": None,
                "hardFailure": False,
                "error": str(exc),
            }
            return
        shot["visualQuality"] = report
        if not report.get("hardFailure"):
            return
        rejected_dir = root / "visual_quality" / "rejected" / str(shot.get("id") or "shot")
        rejected_dir.mkdir(parents=True, exist_ok=True)
        rejected = rejected_dir / f"attempt_{int(shot.get('attempts', 1) or 1):02d}.mp4"
        shutil.copy2(clip, rejected)
        shot["visualQualityRejectedCandidate"] = rejected.relative_to(root).as_posix()
        shot["status"] = "pending"
        shot["path"] = None
        shot["verifiedDurationSeconds"] = 0.0
        shot["verification"] = {}
        shot["lastError"] = (
            "Visual Quality Supervisor rejected the generated clip: "
            + "; ".join(str(item.get("message") or "") for item in report.get("findings") or [])
        )[:1800]
        raise ai_video.VideoGenerationError(shot["lastError"])

    ai_video.scene_prompt = scene_prompt
    ai_video._process_prediction = process_prediction
    pipeline._visual_quality_supervisor_installed = True


__all__ = ["install_visual_quality_supervisor"]
