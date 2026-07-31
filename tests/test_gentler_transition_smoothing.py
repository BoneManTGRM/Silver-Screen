from __future__ import annotations

from dataclasses import replace

from silver_screen import transition_engine


def test_auto_mode_uses_gentler_default_overlaps(monkeypatch) -> None:
    for name in (
        "SILVER_SCREEN_TRANSITION_SAME_SCENE_SECONDS",
        "SILVER_SCREEN_TRANSITION_SCENE_SECONDS",
        "SILVER_SCREEN_TRANSITION_CHAPTER_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = transition_engine.settings("auto", analyze_frames=False)
    assert cfg.same_scene >= 0.26
    assert cfg.scene_change >= 0.40
    assert cfg.chapter_change >= 0.58


def test_explicit_transition_values_are_respected(monkeypatch) -> None:
    monkeypatch.setenv("SILVER_SCREEN_TRANSITION_SAME_SCENE_SECONDS", "0.31")
    monkeypatch.setenv("SILVER_SCREEN_TRANSITION_SCENE_SECONDS", "0.44")
    monkeypatch.setenv("SILVER_SCREEN_TRANSITION_CHAPTER_SECONDS", "0.66")
    cfg = transition_engine.settings("auto", analyze_frames=False)
    assert cfg.same_scene == 0.31
    assert cfg.scene_change == 0.44
    assert cfg.chapter_change == 0.66


def test_adaptive_analysis_extends_mismatched_continuation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        transition_engine,
        "_similarity",
        lambda first, second: {"visual": 0.1, "luminance": 0.5, "edge": 0.2},
    )
    monkeypatch.setattr(transition_engine, "_frame", lambda *args, **kwargs: tmp_path / "frame.jpg")
    monkeypatch.setattr(transition_engine, "shot_path", lambda root, shot: tmp_path / "clip.mp4")
    (tmp_path / "clip.mp4").write_bytes(b"video")
    cfg = replace(transition_engine.settings("auto"), analyze_frames=True)
    previous = {"id": "a", "order": 1, "status": "verified", "sourceScene": {"number": 1, "chapter": 1}}
    current = {"id": "b", "order": 2, "status": "verified", "sourceScene": {"number": 1, "chapter": 1}}
    item = transition_engine.analyze(previous, current, tmp_path, cfg)
    assert item["relation"] == "continuation"
    assert item["durationSeconds"] >= 0.42
