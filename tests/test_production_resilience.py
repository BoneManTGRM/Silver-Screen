from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from silver_screen import ai_video
from silver_screen.production_resilience import (
    call_with_rate_limit_backoff,
    is_rate_limit_error,
    reconcile_transition_retakes,
    retry_after_seconds,
    schedule_transition_retake,
    transition_retake_candidates,
)
from silver_screen.provider_diagnostics import diagnose_provider_error
from silver_screen.runtime import RunWorkspace
from silver_screen.transition_engine import build_plan, settings
from silver_screen.video_runtime import (
    create_video_queue,
    load_video_queue,
    normalize_video_config,
    save_video_queue,
    update_video_metrics,
)


def _state() -> dict[str, Any]:
    return {
        "id": "director-test",
        "title": "Director Test",
        "premise": "A lead actor crosses a rooftop while a helicopter door closes.",
        "genre": "thriller",
        "tone": "cinematic",
        "seed": 42,
        "storyBible": {"motif": "red warning light"},
        "characters": [
            {
                "id": "lead",
                "name": "Cody",
                "description": "the exact same authorized lead actor",
            }
        ],
        "chapters": [{"number": 1, "title": "Escape"}],
        "scenes": [
            {
                "number": 1,
                "act": 1,
                "chapter": 1,
                "slugline": "EXT. ROOFTOP - NIGHT",
                "characters": ["lead"],
                "action": "Cody runs toward the waiting helicopter.",
                "conflict": "The door is closing.",
                "summary": "The escape begins.",
            }
        ],
    }


def _fake_mp4(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20000)


def test_retry_after_parser_understands_replicate_payload() -> None:
    error = (
        'Replicate returned HTTP 429: {"detail":"Request was throttled. '
        'Your rate limit resets in ~10s.","status":429,"retry_after":10}'
    )
    assert is_rate_limit_error(error)
    assert retry_after_seconds(error) == 10


def test_bounded_backoff_retries_only_rate_limits() -> None:
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError(
                'HTTP 429 throttled {"retry_after":2}'
            )
        return "ok"

    result = call_with_rate_limit_backoff(
        operation,
        max_retries=3,
        max_wait_seconds=5,
        sleep=sleeps.append,
    )
    assert result == "ok"
    assert calls == 3
    assert sleeps == [2.0, 2.0]


def test_non_rate_provider_error_is_not_retried() -> None:
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("HTTP 401 invalid token")

    with pytest.raises(RuntimeError, match="401"):
        call_with_rate_limit_backoff(
            operation,
            max_retries=5,
            sleep=lambda _: None,
        )
    assert calls == 1


def test_rate_limit_diagnosis_is_not_misclassified_as_billing() -> None:
    diagnosis = diagnose_provider_error(
        'HTTP 429: {"detail":"less than $5.0 in credit", "retry_after":10}'
    )
    assert diagnosis.code == "rate_limited"
    assert diagnosis.retryable is True
    assert diagnosis.retry_after_seconds == 10
    assert "$5" in diagnosis.detail


def test_director_retake_preserves_original_and_reopens_only_incoming_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    run_id = "ss_director_test_01"
    workspace = RunWorkspace(
        "runs",
        run_id,
        brief={"title": "Director Test"},
        options={
            "targetRuntimeSeconds": 16,
            "videoMaxShots": 2,
            "videoBatchSize": 1,
            "videoMaxRetries": 0,
            "videoMaxProviderCalls": 2,
            "videoUseContinuity": True,
        },
    )
    config = normalize_video_config(
        target_runtime_seconds=16,
        clip_duration_seconds=8,
        max_shots=2,
        max_retries_per_shot=0,
        max_provider_calls=2,
    )
    queue = create_video_queue(_state(), config)
    for index, shot in enumerate(queue["shots"], start=1):
        path = workspace.media_dir / "clips" / f"shot_{index:04d}.mp4"
        _fake_mp4(path)
        shot.update(
            {
                "status": "verified",
                "path": path.relative_to(workspace.media_dir).as_posix(),
                "verifiedDurationSeconds": 8.0,
                "verification": {"bytes": path.stat().st_size, "durationSeconds": 8.0},
                "attempts": 1,
                "continuityUsed": index > 1,
            }
        )
    queue["status"] = "complete"
    queue["stopReason"] = "target_runtime_reached"
    update_video_metrics(queue)
    plan = build_plan(
        queue,
        workspace.media_dir,
        settings("auto", analyze_frames=False),
    )
    save_video_queue(workspace.media_dir, queue)
    result = {
        "status": "complete",
        "run": {"id": run_id, "workspace": str(workspace.path), "persisted": True},
        "state": _state(),
        "brief": {"title": "Director Test"},
        "options": dict(workspace.manifest["options"]),
        "media": {
            "status": "complete",
            "queue": queue,
            "metrics": queue["metrics"],
            "msil": queue["msil"],
            "transitionPlan": plan,
        },
        "metrics": {},
        "msil": {},
        "artifacts": {},
        "warnings": [],
        "timings": {},
    }
    workspace.write_json("result.json", result)
    workspace.complete({"title": "Director Test"})

    transition_id = str(plan["transitions"][0]["transitionId"])
    scheduled = schedule_transition_retake(
        run_id,
        transition_id,
        output_root="runs",
    )

    updated = load_video_queue(workspace.media_dir)
    assert updated is not None
    first, second = updated["shots"]
    assert first["status"] == "verified"
    assert second["status"] == "pending"
    assert second["path"] is None
    assert second["transitionRetake"]["status"] == "scheduled"
    assert Path(scheduled["archivedCandidatePath"]).exists()
    assert scheduled["authorizedProviderCalls"] >= 3
    reopened = RunWorkspace.open_existing("runs", run_id)
    assert reopened.manifest["status"] == "partial"
    assert reopened.manifest["stage"] == "transition_retake_scheduled"


def test_retake_reconciliation_restores_better_preserved_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "media"
    old = root / "retakes" / "shot_0002" / "accepted_before_retake_01.mp4"
    new = root / "clips" / "shot_0002.mp4"
    previous = root / "clips" / "shot_0001.mp4"
    for path in (old, new, previous):
        _fake_mp4(path)

    queue = {
        "shots": [
            {
                "id": "shot_0001",
                "order": 1,
                "status": "verified",
                "path": "clips/shot_0001.mp4",
                "verifiedDurationSeconds": 8.0,
                "sourceScene": {"number": 1, "chapter": 1},
            },
            {
                "id": "shot_0002",
                "order": 2,
                "status": "verified",
                "path": "clips/shot_0002.mp4",
                "verifiedDurationSeconds": 8.0,
                "sourceScene": {"number": 1, "chapter": 1},
                "transitionRetake": {
                    "status": "scheduled",
                    "transitionId": "transition_0001_0002",
                    "retakeNumber": 1,
                },
                "transitionRetakeHistory": [
                    {
                        "candidatePath": "retakes/shot_0002/accepted_before_retake_01.mp4",
                        "verification": {"durationSeconds": 8.0},
                        "verifiedDurationSeconds": 8.0,
                    }
                ],
            },
        ],
        "events": [],
        "metrics": {},
        "msil": {},
        "config": {"clip_duration_seconds": 8},
    }

    def fake_analyze(
        _previous: dict[str, Any],
        current: dict[str, Any],
        _root: Path,
        _cfg: Any,
    ) -> dict[str, Any]:
        score = 0.82 if "accepted_before" in str(current.get("path")) else 0.51
        return {"effectiveScore": score, "rating": "smooth" if score > 0.64 else "masked"}

    monkeypatch.setattr(
        "silver_screen.production_resilience.analyze",
        fake_analyze,
    )
    outcomes = reconcile_transition_retakes(queue, root)
    assert outcomes[0]["selected"] == "preserved_original"
    assert queue["shots"][1]["path"] == (
        "retakes/shot_0002/accepted_before_retake_01.mp4"
    )
    assert queue["shots"][1]["transitionRetake"] is None
    assert (root / outcomes[0]["rejectedPath"]).exists()


def test_director_retake_prompt_is_appended() -> None:
    state = _state()
    scene = state["scenes"][0]
    shot = {
        "segment": 2,
        "continuityUsed": True,
        "transitionRetake": {
            "directive": "Match the running pose and camera velocity exactly."
        },
    }
    prompt = ai_video.scene_prompt(state, scene, shot)
    assert "DIRECTOR REVIEW RETAKE" in prompt
    assert "camera velocity exactly" in prompt


def test_retake_candidates_prioritize_weak_same_scene_boundary() -> None:
    plan = {
        "transitions": [
            {
                "transitionId": "a",
                "fromOrder": 1,
                "toOrder": 2,
                "relation": "continuation",
                "effectiveScore": 0.60,
                "rating": "masked",
            },
            {
                "transitionId": "b",
                "fromOrder": 2,
                "toOrder": 3,
                "relation": "scene_change",
                "effectiveScore": 0.63,
                "rating": "masked",
            },
        ]
    }
    candidates = transition_retake_candidates(plan, threshold=0.64)
    assert [item["transitionId"] for item in candidates] == ["a", "b"]


def test_director_review_page_compiles() -> None:
    page = Path("pages/5_Director_Review.py")
    compile(page.read_text(encoding="utf-8"), str(page), "exec")
