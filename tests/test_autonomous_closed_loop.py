from __future__ import annotations

from pathlib import Path

import pytest

import silver_screen
from silver_screen import autonomous_studio
from silver_screen.autonomous_closed_loop_install import (
    AutonomousRepairError,
    _candidate,
    _persist_plan_and_routes,
    _schedule_semantic_retake,
    _select_candidate,
    _summarize,
)
from silver_screen.runtime import RunWorkspace
from silver_screen.video_runtime import load_video_queue, save_video_queue


def _report(shot_id: str, order: int, score: float, *, hard: bool = False) -> dict:
    return {
        "shotId": shot_id,
        "contract": {"shotId": shot_id, "order": order},
        "compositeScore": score,
        "semanticAvailable": True,
        "hardFailure": hard,
        "accepted": not hard and score >= 0.84,
        "rating": "reject" if hard else "accepted" if score >= 0.84 else "review",
        "repairDirective": "Correct only the failed action while preserving identity and continuity.",
    }


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunWorkspace:
    monkeypatch.chdir(tmp_path)
    return RunWorkspace("runs", "run-test-0001", brief={}, options={})


def _queue(path: Path) -> dict:
    queue = {
        "schemaVersion": 1,
        "status": "complete",
        "config": {"max_provider_calls": 10, "max_retries_per_shot": 3},
        "shots": [
            {
                "id": "shot_0001",
                "order": 1,
                "status": "verified",
                "path": "clips/shot_0001.mp4",
                "attempts": 1,
                "plannedDurationSeconds": 8,
                "verifiedDurationSeconds": 8,
                "verification": {"durationSeconds": 8},
                "sourceScene": {"number": 1, "chapter": 1},
                "semanticQuality": _report("shot_0001", 1, 0.60, hard=True),
                "visualQuality": {"score": 0.78, "accepted": True},
            }
        ],
        "metrics": {
            "providerCalls": 1,
            "verifiedShots": 1,
            "plannedShots": 1,
            "verifiedSeconds": 8,
        },
    }
    (path / "clips").mkdir(parents=True, exist_ok=True)
    (path / "clips" / "shot_0001.mp4").write_bytes(b"accepted-original")
    save_video_queue(path, queue)
    return queue


def test_closed_loop_installs_on_package_import() -> None:
    assert getattr(autonomous_studio, "_closed_loop_installed", False) is True
    assert silver_screen.__version__ == "9.0.0"


def test_candidate_prioritizes_hard_failure() -> None:
    selected = _candidate(
        {"reports": [_report("shot_0002", 2, 0.50), _report("shot_0001", 1, 0.70, hard=True)]},
        target=0.84,
    )
    assert selected is not None
    assert selected["shotId"] == "shot_0001"


def test_summary_records_semantic_counts() -> None:
    summary = _summarize(
        [_report("shot_0001", 1, 0.90), _report("shot_0002", 2, 0.60, hard=True)],
        run_id="run-test",
        project_id="project-test",
    )
    assert summary["clips"] == 2
    assert summary["semanticReviewed"] == 2
    assert summary["rejected"] == 1


def test_plan_routes_are_persisted_into_runtime_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    _queue(workspace.media_dir)
    plan = {
        "modelRoutes": {
            "routes": [
                {
                    "shotId": "shot_0001",
                    "order": 1,
                    "task": "performance",
                    "recommendedModel": "specialist/example",
                    "executionModel": "google/veo-3.1-fast",
                }
            ]
        }
    }
    _persist_plan_and_routes(
        workspace.run_id,
        plan,
        output_root="runs",
        config={"qualityProfile": "blockbuster_target"},
        project_id="project-test",
    )
    saved = load_video_queue(workspace.media_dir)
    assert saved is not None
    assert saved["shots"][0]["modelRoute"]["task"] == "performance"
    assert (workspace.path / "autonomous_plan.json").exists()
    assert (workspace.path / "autonomous_job.json").exists()


def test_semantic_retake_never_expands_approved_call_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    _queue(workspace.media_dir)
    scheduled = _schedule_semantic_retake(
        workspace.run_id,
        _report("shot_0001", 1, 0.60, hard=True),
        output_root="runs",
        approved_calls=5,
        max_retries=3,
    )
    queue = scheduled["queue"]
    assert queue["status"] == "partial"
    assert queue["shots"][0]["status"] == "pending"
    assert queue["config"]["max_provider_calls"] == 10
    assert Path(scheduled["record"]["candidatePath"]).name.endswith(".mp4")


def test_semantic_retake_is_blocked_when_no_call_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    _queue(workspace.media_dir)
    with pytest.raises(AutonomousRepairError, match="No approved provider call remains"):
        _schedule_semantic_retake(
            workspace.run_id,
            _report("shot_0001", 1, 0.60, hard=True),
            output_root="runs",
            approved_calls=1,
            max_retries=3,
        )


def test_candidate_comparison_restores_original_without_verified_gain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    queue = _queue(workspace.media_dir)
    queue["shots"][0]["semanticQuality"] = _report("shot_0001", 1, 0.70)
    save_video_queue(workspace.media_dir, queue)
    scheduled = _schedule_semantic_retake(
        workspace.run_id,
        _report("shot_0001", 1, 0.70),
        output_root="runs",
        approved_calls=5,
        max_retries=3,
    )
    queue = scheduled["queue"]
    shot = queue["shots"][0]
    target = workspace.media_dir / scheduled["record"]["sourcePath"]
    target.write_bytes(b"worse-replacement")
    shot["path"] = scheduled["record"]["sourcePath"]
    shot["status"] = "verified"
    shot["verifiedDurationSeconds"] = 8
    decision = _select_candidate(
        workspace,
        queue,
        shot,
        scheduled["record"],
        _report("shot_0001", 1, 0.705),
        minimum_gain=0.015,
    )
    assert decision["selected"] == "preserved_original"
    assert target.read_bytes() == b"accepted-original"
    assert shot["status"] == "verified"


def test_candidate_comparison_keeps_measurably_better_retake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    queue = _queue(workspace.media_dir)
    queue["shots"][0]["semanticQuality"] = _report("shot_0001", 1, 0.70)
    save_video_queue(workspace.media_dir, queue)
    scheduled = _schedule_semantic_retake(
        workspace.run_id,
        _report("shot_0001", 1, 0.70),
        output_root="runs",
        approved_calls=5,
        max_retries=3,
    )
    queue = scheduled["queue"]
    shot = queue["shots"][0]
    shot["path"] = scheduled["record"]["sourcePath"]
    shot["status"] = "verified"
    shot["verifiedDurationSeconds"] = 8
    decision = _select_candidate(
        workspace,
        queue,
        shot,
        scheduled["record"],
        _report("shot_0001", 1, 0.82),
        minimum_gain=0.015,
    )
    assert decision["selected"] == "new"
    assert decision["gain"] > 0.10
