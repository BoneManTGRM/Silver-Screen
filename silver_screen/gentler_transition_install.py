"""Install slightly longer, adaptive cinematic transitions by default."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any


def _explicit(name: str) -> bool:
    return os.getenv(name) not in {None, ""}


def install_gentler_transition_smoothing() -> None:
    """Raise default overlap modestly while respecting explicit operator values."""

    from . import transition_engine

    if getattr(transition_engine, "_gentler_transition_smoothing_installed", False):
        return

    original_settings = transition_engine.settings
    original_analyze = transition_engine.analyze

    def settings(mode: str | None = None, *, analyze_frames: bool | None = None):
        cfg = original_settings(mode, analyze_frames=analyze_frames)
        if cfg.mode == "off":
            return cfg
        same_scene = cfg.same_scene
        scene_change = cfg.scene_change
        chapter_change = cfg.chapter_change
        if cfg.mode == "auto":
            if not _explicit("SILVER_SCREEN_TRANSITION_SAME_SCENE_SECONDS"):
                same_scene = max(same_scene, 0.26)
            if not _explicit("SILVER_SCREEN_TRANSITION_SCENE_SECONDS"):
                scene_change = max(scene_change, 0.40)
            if not _explicit("SILVER_SCREEN_TRANSITION_CHAPTER_SECONDS"):
                chapter_change = max(chapter_change, 0.58)
        return replace(
            cfg,
            same_scene=min(0.8, same_scene),
            scene_change=min(1.0, scene_change),
            chapter_change=min(1.2, chapter_change),
        )

    def analyze(
        previous: dict[str, Any],
        current: dict[str, Any],
        root,
        cfg,
    ) -> dict[str, Any]:
        item = original_analyze(previous, current, root, cfg)
        relation = str(item.get("relation") or "")
        score = float(item.get("rawMatchScore", 0) or 0)
        seconds = float(item.get("durationSeconds", 0) or 0)
        if relation == "continuation":
            target = 0.42 if score < 0.58 else 0.34 if score < 0.70 else 0.26
            item["durationSeconds"] = round(max(seconds, target), 3)
            if score < 0.70 and item.get("repair") in {None, "none"}:
                item["repair"] = "gentler_continuation_blend"
        elif relation == "scene_change":
            target = 0.48 if score < 0.56 else 0.40
            item["durationSeconds"] = round(max(seconds, target), 3)
        elif relation == "chapter_change":
            item["durationSeconds"] = round(max(seconds, 0.58), 3)
        return item

    transition_engine.settings = settings
    transition_engine.analyze = analyze
    transition_engine._gentler_transition_smoothing_installed = True

    # These modules import transition helpers directly, so refresh their local
    # references after installation as well.
    try:
        from . import cinematic_continuity

        cinematic_continuity.settings = settings
    except Exception:
        pass
    try:
        from . import production_resilience

        production_resilience.settings = settings
        production_resilience.analyze = analyze
    except Exception:
        pass


__all__ = ["install_gentler_transition_smoothing"]
