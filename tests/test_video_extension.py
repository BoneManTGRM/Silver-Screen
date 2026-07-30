from __future__ import annotations

from pathlib import Path
from typing import Any

import silver_screen.video_extension as video_extension


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.media_dir = root / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.manifest: dict[str, Any] = {"progress": 100, "status": "complete"}
        self.updates: list[dict[str, Any]] = []
        self.failed: str | None = None

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)
        self.manifest.update(kwargs)

    def fail(self, error: str) -> None:
        self.failed = error


def test_extension_updates_the_saved_target_and_reuses_the_existing_queue(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = FakeWorkspace(tmp_path)
    source_result = {
        "status": "complete",
        "state": {"title": "Moonie Moo", "scenes": [{"number": 1}]},
        "options": {
            "targetRuntimeSeconds": 8,
            "videoMaxShots": 1,
            "videoBatchSize": 1,
            "videoMaxRetries": 1,
            "videoMaxProviderCalls": 2,
            "videoUseContinuity": True,
        },
        "media": {
            "status": "complete",
            "metrics": {"plannedShots": 1, "verifiedShots": 1},
        },
        "warnings": [],
        "timings": {},
    }
    captured: dict[str, Any] = {}
    persisted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        video_extension.RunWorkspace,
        "open_existing",
        classmethod(lambda cls, output_root, run_id: workspace),
    )
    monkeypatch.setattr(
        video_extension,
        "load_run",
        lambda run_id, output_root: source_result,
    )

    def fake_process_media(state: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "status": "partial",
            "mode": "ai-video",
            "metrics": {
                "plannedShots": 15,
                "verifiedShots": 2,
                "verifiedSeconds": 16,
            },
            "msil": {"verdict": "checkpoint"},
            "warnings": [],
            "error": None,
        }

    monkeypatch.setattr(video_extension, "process_media", fake_process_media)
    monkeypatch.setattr(
        video_extension, "_pipeline_status", lambda mode, media: "partial"
    )
    monkeypatch.setattr(
        video_extension, "_video_progress", lambda progress, workspace: None
    )
    monkeypatch.setattr(
        video_extension,
        "_persist_and_finalize",
        lambda workspace, result: persisted.append(result),
    )

    result = video_extension.extend_video_run(
        "ss_existing",
        target_runtime_seconds=120,
        max_shots=15,
        output_root="runs",
        batch_size=1,
        continuous=False,
        max_retries=1,
        max_provider_calls=30,
        use_continuity=True,
    )

    assert captured["target_runtime_seconds"] == 120
    assert captured["video_max_shots"] == 15
    assert captured["video_batch_size"] == 1
    assert captured["video_resume"] is True
    assert result["options"]["targetRuntimeSeconds"] == 120
    assert result["options"]["videoMaxShots"] == 15
    assert result["media"]["metrics"]["verifiedShots"] == 2
    assert result["status"] == "partial"
    assert persisted and persisted[0] is result
    assert workspace.failed is None
