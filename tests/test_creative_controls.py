from __future__ import annotations

from pathlib import Path

import pytest

import silver_screen
from silver_screen.ai_video import scene_prompt
from silver_screen.creative_direction import (
    audit_screenplay,
    normalize_creative_direction,
    parse_scene_override_text,
)
from silver_screen.pipeline import BriefValidationError, run_pipeline, validate_brief
from silver_screen.preproduction import build_preproduction_preview
from silver_screen.script_engine import build_film_from_brief


def _cast() -> list[dict[str, str]]:
    return [
        {
            "name": "Cody",
            "role": "Lead protagonist",
            "description": "The exact same authorized lead actor, restrained and observant.",
        },
        {
            "name": "Mara",
            "role": "Operational counterpoint",
            "description": "A composed intelligence officer with a private objective.",
        },
    ]


def _brief(direction: dict | None = None) -> dict:
    return {
        "title": "Quiet Handoff",
        "premise": (
            "A routine hotel handoff was designed to expose a field operative, who "
            "must identify which member of the team controls the surveillance net."
        ),
        "genre": "thriller",
        "tone": "cinematic",
        "format": "trailer",
        "cast": _cast(),
        "creativeDirection": direction or {"profile": "grounded_prestige"},
    }


def test_default_brief_receives_grounded_reference_aware_direction() -> None:
    raw = _brief()
    raw.pop("creativeDirection")
    normalized = validate_brief(raw)
    direction = normalized["creativeDirection"]
    assert direction["profile"] == "grounded_prestige"
    assert "reference medium" in direction["medium"]
    assert direction["strictGate"] is True


def test_grounded_profile_removes_stock_dialogue_and_passes_gate() -> None:
    film = build_film_from_brief(
        premise=_brief()["premise"],
        genre="thriller",
        tone="cinematic",
        title="Quiet Handoff",
        fmt="trailer",
        cast=_cast(),
        creative_direction={"profile": "grounded_prestige"},
    )
    script = film["script"]
    assert "which failure you are willing to own" not in script.lower()
    assert "the world seems to wait" not in script.lower()
    assert "whose silence may be protection, leverage, or control" in film["logline"]
    assert film["creativeQuality"]["passed"] is True
    assert audit_screenplay(script, direction=film["creativeDirection"], state=film)["score"] >= 82


def test_prompt_contains_creative_contract_and_scene_override() -> None:
    direction = normalize_creative_direction(
        {
            "profile": "modern_spy_thriller",
            "scenePromptOverrides": {
                "1": "Static 50mm profile. The lead notices the tail only in the glass reflection."
            },
        }
    )
    film = build_film_from_brief(
        premise=_brief()["premise"],
        genre="thriller",
        tone="cinematic",
        title="Quiet Handoff",
        fmt="trailer",
        cast=_cast(),
        creative_direction=direction,
    )
    scene = film["scenes"][0]
    prompt = scene_prompt(film, scene, {"segment": 1, "continuityUsed": False})
    lowered = prompt.lower()
    assert "creative contract" in lowered
    assert "scene-specific director override" in lowered
    assert "static 50mm profile" in lowered
    assert "villain monologue" in lowered


def test_authored_script_is_preserved_and_drives_scene_excerpts() -> None:
    script = (
        "EXT. HOTEL TERRACE - NIGHT\n"
        "Cody watches the reflection in the glass instead of the street.\n\n"
        "CODY: Were you followed?\n"
        "MARA: I was expected.\n\n"
        "INT. SERVICE CORRIDOR - NIGHT\n"
        "They walk without looking at each other."
    )
    film = build_film_from_brief(
        premise=_brief()["premise"],
        genre="thriller",
        tone="cinematic",
        title="Quiet Handoff",
        fmt="trailer",
        cast=_cast(),
        creative_direction={
            "profile": "modern_spy_thriller",
            "scriptSource": "authored",
        },
        authored_script=script,
    )
    assert film["scriptSource"] == "authored"
    assert "Were you followed?" in film["script"]
    assert film["script"].rstrip().endswith("THE END")
    assert any(scene.get("authoredExcerpt") for scene in film["scenes"])


def test_preproduction_preview_makes_no_provider_calls_and_passes_strict_gates() -> None:
    preview = build_preproduction_preview(
        _brief({"profile": "modern_spy_thriller", "strictGate": True}),
        target_runtime_seconds=16,
        clip_duration_seconds=8,
        max_shots=2,
    )
    assert preview["providerCallsMade"] == 0
    assert preview["renderPlan"]["plannedShots"] == 2
    assert len(preview["prompts"]) == 2
    assert preview["strictGatePassed"] is True
    assert preview["screenplayAudit"]["passed"] is True
    assert preview["promptGate"]["passed"] is True


def test_paid_pipeline_is_blocked_until_all_three_approvals_exist() -> None:
    direction = {
        "profile": "grounded_prestige",
        "enforceApprovalGates": True,
        "approvals": {
            "scriptApproved": True,
            "promptsApproved": False,
            "budgetApproved": True,
        },
    }
    with pytest.raises(BriefValidationError, match="visual prompts"):
        run_pipeline(
            _brief(direction),
            render_media=True,
            video_mode="ai-video",
            persist=False,
        )


def test_scene_override_text_parser_is_operator_friendly() -> None:
    parsed = parse_scene_override_text(
        "1: Static 50mm medium shot.\nKeep the lead in profile.\n"
        "Scene 2 = Slow lateral move through the corridor."
    )
    assert parsed["1"].startswith("Static 50mm")
    assert "profile" in parsed["1"]
    assert parsed["2"].startswith("Slow lateral")


def test_scene_override_parser_handles_long_spacing_without_regex_backtracking() -> None:
    spacing = " " * 10_000
    parsed = parse_scene_override_text(
        f"Scene{spacing}1{spacing}:{spacing}Static 50mm profile."
    )
    assert parsed == {"1": "Static 50mm profile."}


def test_creative_director_page_compiles() -> None:
    page = Path("pages/6_Creative_Director.py")
    compile(page.read_text(encoding="utf-8"), str(page), "exec")


def test_package_version_is_eight() -> None:
    assert silver_screen.__version__ == "8.0.0"
