from __future__ import annotations

from pathlib import Path

import pytest

import silver_screen
from silver_screen.ai_video import scene_prompt
from silver_screen.autonomous_director import prepare_autonomous_project
from silver_screen.candidate_selection import shot_quality_score
from silver_screen.script_engine import build_film_from_brief
from silver_screen.semantic_supervisor import analyze_semantic_shot, build_shot_contract
from silver_screen.video_runtime import create_video_queue, normalize_video_config


def _cast() -> list[dict[str, str]]:
    return [
        {
            "name": "Cody",
            "role": "Lead operative",
            "description": (
                "The same authorized lead in every shot. Dark field jacket, controlled "
                "performance, no identity or wardrobe substitution."
            ),
        },
        {
            "name": "Mara",
            "role": "Operational counterpoint",
            "description": "A composed intelligence officer carrying one brass key.",
        },
    ]


def _brief() -> dict:
    return {
        "title": "Quiet Handoff",
        "premise": (
            "A routine hotel handoff was designed to expose an operative, who must "
            "identify which teammate controls the surveillance net before the evidence disappears."
        ),
        "genre": "thriller",
        "tone": "cinematic",
        "format": "trailer",
        "cast": _cast(),
        "creativeDirection": {"profile": "modern_spy_thriller", "strictGate": True},
        "shotDirection": {"audioStrategy": "dub_later", "coverageGate": True},
        "projectId": "quiet-handoff-universe",
        "productionMemory": {
            "projectId": "quiet-handoff-universe",
            "projectNotes": "The brass key always belongs to Mara. The operation happens in one night.",
            "world": {
                "props": {
                    "brass-key": {
                        "owner": "Mara",
                        "state": "intact",
                        "locked": True,
                    }
                }
            },
        },
    }


def _state_and_queue(shots: int = 2) -> tuple[dict, dict]:
    state = build_film_from_brief(
        premise=_brief()["premise"],
        genre="thriller",
        tone="cinematic",
        title="Quiet Handoff",
        fmt="trailer",
        cast=_cast(),
        creative_direction={"profile": "modern_spy_thriller"},
        shot_direction={"audioStrategy": "dub_later"},
    )
    config = normalize_video_config(
        target_runtime_seconds=shots * 8,
        clip_duration_seconds=8,
        max_shots=shots,
        batch_size=0,
        max_retries_per_shot=0,
        max_provider_calls=0,
        use_continuity_frames=True,
    )
    queue = create_video_queue(state, config)
    state["_videoShots"] = queue["shots"]
    return state, queue


def test_autonomous_preproduction_selects_a_provider_free_plan(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    plan = prepare_autonomous_project(
        _brief(),
        target_runtime_seconds=16,
        clip_duration_seconds=8,
        max_shots=2,
        config={
            "profile": "efficient",
            "planningAttempts": 2,
            "projectId": "quiet-handoff-universe",
        },
    )
    assert plan["providerCallsMade"] == 0
    assert plan["selectedAttempt"] in {1, 2}
    assert len(plan["planningAttempts"]) == 2
    assert len((plan["animatic"] or {}).get("shots") or []) == 2
    assert (plan["preview"].get("promptLedger") or {}).get("ledgerHash")
    assert (plan["preview"].get("state") or {}).get("productionMemory")


def test_production_memory_is_inserted_into_approved_shot_prompt() -> None:
    state, queue = _state_and_queue(1)
    state["productionMemory"] = {
        "projectId": "quiet-handoff-universe",
        "memoryVersion": 1,
        "promptCoreHash": "abc123",
        "world": {
            "props": {"brass-key": {"owner": "Mara", "locked": True}},
            "storyRules": ["The brass key always belongs to Mara."],
        },
    }
    shot = queue["shots"][0]
    scene = state["scenes"][0]
    prompt = scene_prompt(state, scene, shot)
    assert "production world memory" in prompt.casefold()
    assert "brass" in prompt.casefold()
    assert (shot.get("productionMemory") or {}).get("projectId") == "quiet-handoff-universe"


def test_semantic_local_fallback_is_honest_and_non_blocking(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state, queue = _state_and_queue(1)
    shot = queue["shots"][0]
    contract = build_shot_contract(state, shot)
    assert contract["shotObjective"]
    report = analyze_semantic_shot(
        "/path/does/not/need/to/exist.mp4",
        state,
        shot,
        config={"providerEnabled": False, "enabled": True},
    )
    assert report["evidenceQuality"] == "provisional"
    assert report["accepted"] is None
    assert report["hardFailure"] is False
    assert "does not claim" in report["observedSummary"]


def test_candidate_quality_score_combines_actual_evidence() -> None:
    shot = {
        "visualQuality": {"score": 0.90},
        "semanticQuality": {"score": 0.80, "evidenceQuality": "provider"},
        "transitionIn": {"effectiveScore": 0.70},
    }
    score = shot_quality_score(shot)
    assert 0.7 < score["score"] < 0.91
    assert score["semanticEvidence"] == "provider"


def test_autonomous_and_timeline_pages_compile() -> None:
    for value in (
        "pages/9_Autonomous_Director.py",
        "pages/10_Timeline_Editor.py",
    ):
        path = Path(value)
        compile(path.read_text(encoding="utf-8"), value, "exec")


def test_package_version_is_nine() -> None:
    assert silver_screen.__version__ == "9.0.0"
