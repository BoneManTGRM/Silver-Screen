from __future__ import annotations

import copy

import pytest

import silver_screen
from silver_screen.ai_video import scene_prompt
from silver_screen.preproduction import build_preproduction_preview
from silver_screen.script_engine import build_film_from_brief
from silver_screen.shot_director import (
    ShotDirectorError,
    audit_prompt_set,
    build_prompt_ledger,
    enforce_prompt_ledger,
    normalize_shot_direction,
    parse_shot_override_text,
    render_directed_prompt,
    render_negative_prompt,
    select_shot_blueprint,
    verify_ledger_hash,
)
from silver_screen.video_runtime import create_video_queue, normalize_video_config


def _cast() -> list[dict[str, str]]:
    return [
        {
            "name": "Cody",
            "role": "Lead operative",
            "description": "The exact same authorized lead, restrained and observant.",
        },
        {
            "name": "Mara",
            "role": "Operational counterpoint",
            "description": "A composed intelligence officer with a private objective.",
        },
    ]


def _state(
    *,
    audio: str = "dub_later",
    overrides: dict[str, str] | None = None,
) -> dict:
    return build_film_from_brief(
        premise=(
            "A routine hotel handoff was designed to expose a field operative, who "
            "must identify which team member controls the surveillance net."
        ),
        genre="thriller",
        tone="cinematic",
        title="Quiet Handoff",
        fmt="trailer",
        cast=_cast(),
        creative_direction={"profile": "modern_spy_thriller"},
        shot_direction={
            "audioStrategy": audio,
            "shotPromptOverrides": overrides or {},
        },
    )


def _queue(state: dict, shots: int = 4) -> dict:
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
    return queue


def test_shot_blueprints_are_distinct_within_the_same_scene() -> None:
    state = _state()
    queue = _queue(state, 4)
    first, second = queue["shots"][:2]
    scene = state["scenes"][0]
    one = select_shot_blueprint(state, scene, first)
    two = select_shot_blueprint(state, scene, second)
    assert one["order"] == 1
    assert two["order"] == 2
    assert (one["type"], one["description"]) != (
        two["type"],
        two["description"],
    )


def test_clip_override_and_professional_dub_contract_reach_prompt() -> None:
    state = _state(
        overrides={"1": "Static 50mm profile through the hotel glass."}
    )
    queue = _queue(state, 1)
    shot = queue["shots"][0]
    scene = state["scenes"][0]
    prompt = scene_prompt(state, scene, shot)
    lowered = prompt.casefold()
    assert "professional shot contract" in lowered
    assert "static 50mm profile" in lowered
    assert "no intelligible spoken words" in lowered
    assert "negativePrompt" in shot
    assert "gibberish dialogue" in shot["negativePrompt"]


def test_prompt_ledger_covers_both_continuity_variants_and_verifies() -> None:
    state = _state()
    queue = _queue(state, 3)
    ledger = build_prompt_ledger(state, queue)
    assert verify_ledger_hash(ledger) is True
    assert len(ledger["entries"]) == 3
    second = ledger["entries"][1]
    assert second["promptHashWithContinuity"] != second[
        "promptHashWithoutContinuity"
    ]
    audit = audit_prompt_set(ledger, state["shotDirection"])
    assert audit["passed"] is True


def test_approved_prompt_ledger_rejects_runtime_drift() -> None:
    state = _state()
    queue = _queue(state, 2)
    ledger = build_prompt_ledger(state, queue)
    state["shotDirection"] = normalize_shot_direction(
        {
            **state["shotDirection"],
            "enforcePromptLedger": True,
            "approvedPromptLedger": ledger,
            "approvedLedgerHash": ledger["ledgerHash"],
        }
    )
    shot = queue["shots"][0]
    scene = state["scenes"][0]
    current = render_directed_prompt(state, scene, shot)
    negative = render_negative_prompt(state, shot)
    locked, locked_negative = enforce_prompt_ledger(
        state, scene, shot, None, current, negative
    )
    assert locked == ledger["entries"][0]["promptWithoutContinuity"]
    assert locked_negative == ledger["entries"][0]["negativePrompt"]

    drifted = copy.deepcopy(state)
    drifted["scenes"][0]["summary"] = "A completely different unapproved scene."
    drifted_shot = copy.deepcopy(shot)
    with pytest.raises(ShotDirectorError, match="drifted"):
        enforce_prompt_ledger(
            drifted,
            drifted["scenes"][0],
            drifted_shot,
            None,
            render_directed_prompt(
                drifted, drifted["scenes"][0], drifted_shot
            ),
            render_negative_prompt(drifted, drifted_shot),
        )


def test_native_dialogue_overflow_blocks_coverage_gate() -> None:
    state = _state(audio="native_dialogue")
    queue = _queue(state, 1)
    ledger = build_prompt_ledger(state, queue)
    entry = ledger["entries"][0]
    entry["blueprint"]["dialogue"] = " ".join(["word"] * 80)
    entry["blueprint"]["durationSeconds"] = 4
    audit = audit_prompt_set(ledger, state["shotDirection"])
    assert audit["blocking"] is True
    assert audit["metrics"]["dialogueOverflows"] == 1


def test_shot_override_parser_accepts_operator_forms_and_long_spacing() -> None:
    parsed = parse_shot_override_text(
        "Clip 1: Static profile.\n"
        "Keep the reflection readable.\n"
        "[2] = Slow lateral move.\n"
        "shot_0003: Locked detail.\n"
        + (" " * 10_000)
    )
    assert parsed["1"].startswith("Static profile")
    assert "reflection" in parsed["1"]
    assert parsed["2"].startswith("Slow lateral")
    assert parsed["shot_0003"] == "Locked detail."


def test_preproduction_builds_full_ledger_without_provider_calls() -> None:
    brief = {
        "title": "Quiet Handoff",
        "premise": (
            "A routine hotel handoff was designed to expose a field operative, who "
            "must identify which team member controls the surveillance net."
        ),
        "genre": "thriller",
        "tone": "cinematic",
        "format": "trailer",
        "cast": _cast(),
        "creativeDirection": {
            "profile": "modern_spy_thriller",
            "strictGate": True,
        },
        "shotDirection": {
            "audioStrategy": "dub_later",
            "coverageGate": True,
        },
    }
    preview = build_preproduction_preview(
        brief,
        target_runtime_seconds=16,
        clip_duration_seconds=8,
        max_shots=2,
    )
    assert preview["providerCallsMade"] == 0
    assert len(preview["promptLedger"]["entries"]) == 2
    assert preview["promptSetAudit"]["passed"] is True
    assert preview["strictGatePassed"] is True
    assert all(item.get("negativePrompt") for item in preview["prompts"])


def test_package_version_is_seven() -> None:
    assert silver_screen.__version__ == "7.0.0"
