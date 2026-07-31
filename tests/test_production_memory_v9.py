from __future__ import annotations

from silver_screen.production_memory import (
    build_production_memory,
    load_project_memory,
    memory_prompt_context,
    memory_summary,
    save_project_memory,
)
from silver_screen.script_engine import build_film_from_brief


def _state() -> dict:
    return build_film_from_brief(
        premise=(
            "A courier finds that one brass key changes ownership whenever the "
            "surveillance system is repaired."
        ),
        genre="thriller",
        tone="cinematic",
        title="The Brass Key",
        fmt="trailer",
        cast=[
            {
                "name": "Mara",
                "role": "Courier",
                "description": "Black field coat. She owns the brass key.",
            },
            {
                "name": "Cody",
                "role": "Technician",
                "description": "Gray work jacket and a restrained performance.",
            },
        ],
        creative_direction={"profile": "grounded_prestige"},
        shot_direction={"audioStrategy": "dub_later"},
    )


def test_project_memory_round_trip_and_summary(tmp_path) -> None:
    state = _state()
    memory = build_production_memory(
        state,
        seed={
            "projectId": "brass-key-universe",
            "projectNotes": "The key always remains visible when ownership changes.",
            "world": {
                "props": {
                    "brass-key": {
                        "owner": "Mara",
                        "locked": True,
                    }
                }
            },
        },
        project_id="brass-key-universe",
    )
    saved = save_project_memory(
        "brass-key-universe",
        memory,
        output_root=str(tmp_path),
    )
    loaded = load_project_memory(
        "brass-key-universe",
        output_root=str(tmp_path),
    )
    assert loaded["projectId"] == "brass-key-universe"
    assert loaded["promptCoreHash"] == memory["promptCoreHash"]
    assert "brass-key" in (loaded.get("world") or {}).get("props", {})
    assert saved["projectId"] == loaded["projectId"]
    summary = memory_summary(loaded)
    assert summary["projectId"] == "brass-key-universe"
    assert summary["promptCoreHash"]


def test_memory_prompt_context_is_bounded_and_specific() -> None:
    state = _state()
    memory = build_production_memory(
        state,
        seed={
            "projectId": "brass-key-universe",
            "world": {
                "props": {"brass-key": {"owner": "Mara", "locked": True}},
                "storyRules": ["Mara owns the brass key until scene four."],
            },
        },
        project_id="brass-key-universe",
    )
    state["productionMemory"] = memory
    context = memory_prompt_context(
        state,
        state["scenes"][0],
        {"id": "shot_0001", "order": 1},
        max_chars=900,
    )
    assert "Mara" in context
    assert "brass" in context.casefold()
    assert len(context) <= 900
