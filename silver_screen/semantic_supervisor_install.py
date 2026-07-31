"""Install semantic review and autonomous candidate-repair directives."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .semantic_supervisor import analyze_semantic_shot, settings


def _enabled() -> bool:
    return os.getenv("SILVER_SCREEN_SEMANTIC_REVIEW", "1").strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _clip_path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = shot.get("path")
    if not value:
        return None
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def install_semantic_supervisor() -> None:
    from . import ai_video, pipeline

    if getattr(pipeline, "_semantic_supervisor_installed", False):
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
        current = shot or {}
        directives = []
        for key in ("candidateRetake", "semanticRetake"):
            value = current.get(key) or {}
            directive = str(value.get("directive") or "").strip()
            if directive:
                directives.append(directive)
        if directives:
            suffix = " ".join(directives)
            prompt = f"{prompt[: max(0, 3500 - len(suffix) - 1)]} {suffix}"[:3500]
        return prompt

    def process_prediction(**kwargs: Any) -> None:
        original_process(**kwargs)
        if not _enabled():
            return
        shot = kwargs.get("shot") or {}
        state = kwargs.get("state") or {}
        root = Path(kwargs.get("root")).resolve()
        clip = _clip_path(root, shot)
        if clip is None or not clip.exists() or shot.get("status") != "verified":
            return
        report = analyze_semantic_shot(
            clip,
            state,
            shot,
            work_dir=root / "semantic_review" / str(shot.get("id") or "shot"),
        )
        shot["semanticQuality"] = report
        cfg = settings()
        if not cfg.get("gate") or not report.get("hardFailure"):
            return
        rejected_dir = root / "semantic_review" / "rejected" / str(
            shot.get("id") or "shot"
        )
        rejected_dir.mkdir(parents=True, exist_ok=True)
        rejected = rejected_dir / f"attempt_{int(shot.get('attempts', 1) or 1):02d}.mp4"
        shutil.copy2(clip, rejected)
        shot["semanticRejectedCandidate"] = rejected.relative_to(root).as_posix()
        shot["semanticRetake"] = {
            "status": "scheduled",
            "directive": (
                "SEMANTIC SHOT REPAIR: "
                + str(report.get("repairDirective") or "Correct the failed shot contract.")
            )[:2600],
            "previousScore": report.get("score"),
        }
        shot["status"] = "pending"
        shot["path"] = None
        shot["verifiedDurationSeconds"] = 0.0
        shot["verification"] = {}
        shot["lastError"] = (
            "Semantic Shot Supervisor rejected the generated clip: "
            + "; ".join(
                str(item.get("message") or "")
                for item in report.get("findings") or []
            )
        )[:1800]
        raise ai_video.VideoGenerationError(shot["lastError"])

    ai_video.scene_prompt = scene_prompt
    ai_video._process_prediction = process_prediction
    pipeline._semantic_supervisor_installed = True


__all__ = ["install_semantic_supervisor"]
