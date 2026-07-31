from __future__ import annotations

from pathlib import Path

import silver_screen
from silver_screen.autonomous_studio import (
    QUALITY_PROFILES,
    normalize_autonomous_config,
    recommended_creative_profile,
)
from silver_screen.model_router import route_queue
from silver_screen.production_memory import (
    build_world_graph,
    load_project_memory,
    memory_context,
    merge_project_memory,
    project_id_for,
    save_project_memory,
)
from silver_screen.semantic_supervisor import evaluate_clip, semantic_settings


def _state() -> dict:
    return {
        "title": "Quiet Handoff",
        "characters": [
            {"id": "c1", "name": "Cody", "role": "Lead", "description": "Restrained operative", "wardrobe": "Dark jacket"},
            {"id": "c2", "name": "Mara", "role": "Counterpoint", "description": "Composed officer"},
        ],
        "scenes": [
            {"number": 1, "chapter": 1, "slugline": "INT. HOTEL CORRIDOR - NIGHT", "characters": ["c1", "c2"], "summary": "Cody notices the tail in a reflection.", "action": "He keeps walking.", "conflict": "Acknowledging the tail would expose him.", "turn": "Mara changes the route."}
        ],
        "creativeDirection": {"medium": "photorealistic live action"},
        "shotDirection": {"audioStrategy": "dub_later"},
    }


def test_project_memory_round_trip_and_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    Path("runs").mkdir()
    brief = {"title": "Quiet Handoff", "premise": "A routine handoff exposes a surveillance operation.", "genre": "thriller", "tone": "cinematic", "format": "trailer"}
    project_id = project_id_for(brief)
    memory = load_project_memory(project_id, "runs")
    memory = merge_project_memory(memory, brief=brief, state=_state(), preferences={"qualityProfile": "blockbuster_target"})
    path = save_project_memory(memory, "runs")
    assert path.exists()
    restored = load_project_memory(project_id, "runs")
    assert restored["worldGraph"]["characters"]
    assert "PERSISTENT PRODUCTION MEMORY" in memory_context(restored, scene_number=1)


def test_world_graph_tracks_scene_location_and_cast() -> None:
    graph = build_world_graph(_state(), brief={"assets": [{"name": "Key card", "owner": "Mara"}]})
    assert len(graph["characters"]) == 2
    assert graph["locations"]
    assert graph["props"]["key-card"]["owner"] == "Mara"
    assert graph["scenes"]["1"]["characters"]


def test_model_router_recommends_but_uses_safe_execution_model() -> None:
    state = _state()
    queue = {"shots": [{"id": "shot_0001", "order": 1, "sourceScene": {"number": 1}, "blueprint": {"type": "performance", "description": "Close-up reaction"}}]}
    routed = route_queue(queue, state=state, config={"qualityTier": "blockbuster_target", "executeSpecialists": False})
    route = routed["routes"][0]
    assert route["recommendedModel"]
    assert route["executionModel"] == "google/veo-3.1-fast"
    assert route["modelIndependent"] is True


def test_semantic_local_fallback_never_claims_semantic_review(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not-a-real-video")
    shot = {"id": "shot_0001", "order": 1, "sourceScene": {"number": 1}, "continuityUsed": False}
    visual = {"score": 0.8, "accepted": True, "hardFailure": False, "findings": [], "metrics": {"referenceAppearanceConsistency": 0.8}}
    report = evaluate_clip(clip, state=_state(), shot=shot, visual_report=visual, config={"mode": "local", "authorized": False})
    assert report["semanticAvailable"] is False
    assert report["decisionBasis"] == "visual_only_semantic_unavailable"
    assert report["accepted"] is True


def test_autonomous_defaults_target_maximum_orchestration() -> None:
    config = normalize_autonomous_config()
    assert config["qualityProfile"] == "blockbuster_target"
    assert config["retriesPerShot"] == QUALITY_PROFILES["blockbuster_target"].retries_per_shot
    assert config["semantic"]["automaticReject"] is False
    assert recommended_creative_profile("thriller") == "modern_spy_thriller"


def test_autonomous_page_compiles() -> None:
    page = Path("pages/9_Autonomous_Studio.py")
    compile(page.read_text(encoding="utf-8"), str(page), "exec")


def test_package_version_is_nine() -> None:
    assert silver_screen.__version__ == "9.0.0"
