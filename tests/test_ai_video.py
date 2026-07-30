from __future__ import annotations

from pathlib import Path

import pytest

from silver_screen.ai_video import (
    ReplicateVideoClient,
    VideoGenerationError,
    scene_prompt,
    verify_mp4,
)
from silver_screen.media import process_media


def _state() -> dict:
    return {
        "title": "Memory Repair",
        "genre": "scifi",
        "tone": "cinematic",
        "seed": 42,
        "storyBible": {"motif": "memory residue"},
        "characters": [
            {"id": "lead", "name": "Elena Vale"},
            {"id": "foil", "name": "Marcus Cross"},
        ],
        "chapters": [{"number": 1, "title": "Fracture"}],
        "scenes": [
            {
                "number": 1,
                "chapter": 1,
                "slugline": "INT. REPAIR LAB - NIGHT",
                "characters": ["lead", "foil"],
                "action": "Elena touches a repaired machine and sees its stored memory wake.",
                "conflict": "Marcus wants the machine disconnected before Elena can verify it.",
                "summary": "A repaired system remembers the break.",
            }
        ],
    }


def test_client_requires_provider_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    with pytest.raises(VideoGenerationError, match="REPLICATE_API_TOKEN"):
        ReplicateVideoClient()


def test_ai_video_mode_fails_transactionally_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    with pytest.raises(VideoGenerationError):
        process_media(_state(), out_dir=tmp_path, video_mode="ai-video")
    assert not list(tmp_path.glob("*.mp4"))


def test_scene_prompt_contains_story_and_excludes_overlay_request() -> None:
    prompt = scene_prompt(_state(), _state()["scenes"][0])
    assert "Elena Vale" in prompt
    assert "REPAIR LAB" in prompt
    assert "memory residue" in prompt
    assert "no titles" in prompt.lower()


def test_verify_mp4_accepts_container_signature(tmp_path: Path) -> None:
    path = tmp_path / "generated.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20000)
    verify_mp4(path)


def test_verify_mp4_rejects_fake_or_tiny_output(tmp_path: Path) -> None:
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"not a video")
    with pytest.raises(VideoGenerationError):
        verify_mp4(fake)
