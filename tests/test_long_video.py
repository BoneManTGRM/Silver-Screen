from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from silver_screen.ai_video import (
    VideoGenerationError,
    generate_ai_video,
    video_production_status,
)
from silver_screen.video_runtime import (
    choose_tgrm_repair,
    create_video_queue,
    detect_video_fractures,
    extend_video_queue,
    normalize_video_config,
)


def _state() -> dict[str, Any]:
    return {
        "id": "film_test",
        "title": "Memory Repair",
        "premise": "A repair technician discovers that machines remember pain.",
        "genre": "scifi",
        "tone": "cinematic",
        "seed": 42,
        "storyBible": {"motif": "memory residue"},
        "characters": [
            {
                "id": "lead",
                "name": "Elena Vale",
                "description": "short dark hair, navy repair uniform",
            },
            {
                "id": "foil",
                "name": "Marcus Cross",
                "description": "older engineer, silver field jacket",
            },
        ],
        "chapters": [
            {"number": 1, "title": "Fracture"},
            {"number": 2, "title": "Repair"},
        ],
        "scenes": [
            {
                "number": 1,
                "act": 1,
                "chapter": 1,
                "slugline": "INT. REPAIR LAB - NIGHT",
                "characters": ["lead", "foil"],
                "action": "Elena touches a repaired machine and its stored memory wakes.",
                "conflict": "Marcus wants the machine disconnected before Elena can verify it.",
                "turn": "The machine displays Elena's own childhood memory.",
                "summary": "A repaired system remembers the break.",
            },
            {
                "number": 2,
                "act": 2,
                "chapter": 2,
                "slugline": "EXT. ORBITAL PLATFORM - DAWN",
                "characters": ["lead", "foil"],
                "action": "Elena carries the repaired core into morning light.",
                "conflict": "The station must choose deletion or accountable repair.",
                "turn": "Marcus accepts that the memory is evidence.",
                "summary": "The repaired system is allowed to testify.",
            },
        ],
    }


def _fake_mp4(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20000)


class FakeClient:
    calls = 0
    downloads = 0
    fail_first_download = False

    def __init__(self, model: str | None = None, **_: Any) -> None:
        self.model = model or "fake/veo"
        self.token = "test"

    def create_prediction(self, prompt: str, **_: Any) -> dict[str, Any]:
        type(self).calls += 1
        return {
            "id": f"prediction-{type(self).calls}",
            "status": "succeeded",
            "output": "https://example.test/video.mp4",
            "model": self.model,
            "metrics": {"predict_time": 1},
            "urls": {"get": "https://example.test/prediction"},
            "input": {"prompt": prompt},
        }

    def get_prediction(
        self, prediction_id: str, prediction_url: str | None = None
    ) -> dict[str, Any]:
        return {
            "id": prediction_id,
            "status": "succeeded",
            "output": "https://example.test/video.mp4",
            "model": self.model,
            "urls": {"get": prediction_url or ""},
        }

    def wait(self, prediction, *, on_update=None):
        if on_update:
            on_update(prediction)
        return prediction

    def download_output(self, prediction, destination: Path) -> Path:
        type(self).downloads += 1
        if type(self).fail_first_download and type(self).downloads == 1:
            raise VideoGenerationError("download failed")
        _fake_mp4(destination)
        return destination


@pytest.fixture(autouse=True)
def reset_fake_client() -> None:
    FakeClient.calls = 0
    FakeClient.downloads = 0
    FakeClient.fail_first_download = False


def _fake_assemble(clips: list[Path], destination: Path) -> Path:
    assert clips
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(clips[0], destination)
    return destination


def test_runtime_planner_expands_story_to_target_duration() -> None:
    config = normalize_video_config(
        target_runtime_seconds=60,
        clip_duration_seconds=8,
        max_shots=20,
    )
    queue = create_video_queue(_state(), config)
    assert len(queue["shots"]) == 8
    assert queue["plannedRuntimeSeconds"] == 64
    assert queue["shots"][0]["sourceScene"]["number"] == 1
    assert queue["shots"][-1]["sourceScene"]["number"] == 2


def test_queue_can_extend_without_deleting_existing_shots() -> None:
    initial = normalize_video_config(
        target_runtime_seconds=8,
        clip_duration_seconds=8,
        max_shots=20,
    )
    queue = create_video_queue(_state(), initial)
    queue["shots"][0]["status"] = "verified"
    expanded = normalize_video_config(
        target_runtime_seconds=24,
        clip_duration_seconds=8,
        max_shots=20,
    )
    extend_video_queue(queue, _state(), expanded)
    assert len(queue["shots"]) == 3
    assert queue["shots"][0]["status"] == "verified"


def test_checkpoint_then_resume_does_not_regenerate_verified_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "silver_screen.ai_video.assemble_clips", _fake_assemble
    )
    first = generate_ai_video(
        _state(),
        tmp_path,
        target_runtime_seconds=16,
        max_shots=4,
        batch_size=1,
        max_retries_per_shot=1,
        client_factory=FakeClient,
    )
    assert first["status"] == "partial"
    assert first["metrics"]["verifiedShots"] == 1
    assert FakeClient.calls == 1

    second = generate_ai_video(
        _state(),
        tmp_path,
        target_runtime_seconds=16,
        max_shots=4,
        batch_size=1,
        max_retries_per_shot=1,
        client_factory=FakeClient,
        resume=True,
    )
    assert second["status"] == "complete"
    assert second["metrics"]["verifiedShots"] == 2
    assert FakeClient.calls == 2
    assert Path(second["final_video_path"]).exists()


def test_tgrm_retries_only_failed_shot_and_reinforces_scar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "silver_screen.ai_video.assemble_clips", _fake_assemble
    )
    FakeClient.fail_first_download = True
    result = generate_ai_video(
        _state(),
        tmp_path,
        target_runtime_seconds=8,
        max_shots=2,
        batch_size=2,
        max_retries_per_shot=1,
        client_factory=FakeClient,
    )
    assert result["status"] == "complete"
    assert result["metrics"]["verifiedShots"] == 1
    assert result["metrics"]["providerCalls"] == 2
    assert result["metrics"]["repairs"] >= 1
    assert result["scars"]
    shot = result["queue"]["shots"][0]
    assert shot["attempts"] == 2
    assert shot["status"] == "verified"


def test_provider_call_budget_blocks_before_unapproved_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "silver_screen.ai_video.assemble_clips", _fake_assemble
    )
    result = generate_ai_video(
        _state(),
        tmp_path,
        target_runtime_seconds=16,
        max_shots=4,
        batch_size=4,
        max_retries_per_shot=1,
        max_provider_calls=1,
        client_factory=FakeClient,
    )
    assert result["status"] == "blocked"
    assert result["stopReason"] == "provider_call_budget_exhausted"
    assert result["metrics"]["verifiedShots"] == 1
    assert result["metrics"]["providerCalls"] == 1


def test_video_status_reads_durable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "silver_screen.ai_video.assemble_clips", _fake_assemble
    )
    generate_ai_video(
        _state(),
        tmp_path,
        target_runtime_seconds=16,
        max_shots=4,
        batch_size=1,
        client_factory=FakeClient,
    )
    status = video_production_status(tmp_path)
    assert status is not None
    assert status["status"] == "partial"
    assert status["metrics"]["verifiedShots"] == 1
    assert (tmp_path / "video_queue.json").exists()
    assert (tmp_path / "video_runtime.json").exists()
    assert (tmp_path / "video_scar_memory.json").exists()


def test_video_fracture_detector_finds_orphaned_prediction() -> None:
    config = normalize_video_config(target_runtime_seconds=8)
    queue = create_video_queue(_state(), config)
    queue["shots"][0]["status"] = "submitted"
    queue["shots"][0]["providerPredictionId"] = None
    fractures = detect_video_fractures(queue)
    assert fractures[0]["class"] == "orphaned_prediction"


@pytest.mark.parametrize(
    ("message", "strategy"),
    [
        ("download failed", "redownload_or_regenerate"),
        ("prediction timeout", "retry_with_simplified_motion"),
        ("HTTP 429 rate limit", "backoff_and_retry"),
        ("invalid MP4 container", "regenerate_verified_container"),
    ],
)
def test_tgrm_selects_minimal_video_repair(
    message: str, strategy: str
) -> None:
    assert choose_tgrm_repair(message, 1)["strategy"] == strategy
