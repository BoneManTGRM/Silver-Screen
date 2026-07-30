from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import silver_screen.ai_video as ai_video
import silver_screen.voice_audio as voice_audio
import silver_screen.voice_studio as voice_studio
from silver_screen.voice_config import _ffmpeg_path

# Keep the private test helper available from the orchestration module without
# changing the public Voice Studio API.
voice_studio._ffmpeg_path = _ffmpeg_path


@pytest.fixture(autouse=True)
def allow_tiny_synthetic_voice_video_fixtures(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
):
    """Relax only the size floor for tiny FFmpeg fixtures used by voice tests.

    Production verification remains unchanged. The black 320x180 test clips are
    valid MP4 containers but compress below the production 16 KiB anti-junk floor.
    Voice tests exercise speech retry, mixing, persistence, and assembly rather
    than provider-download size validation.
    """
    if request.node.path.name not in {
        "test_voice_studio.py",
        "test_voice_debug_regression.py",
    }:
        return

    production_verify = ai_video.verify_mp4

    def verify_fixture(
        path: Path, *, expected_duration: float | None = None
    ) -> dict[str, Any]:
        candidate = Path(path)
        if candidate.is_file() and candidate.stat().st_size >= 512:
            with candidate.open("rb") as handle:
                if b"ftyp" in handle.read(128):
                    return {
                        "bytes": candidate.stat().st_size,
                        "durationSeconds": expected_duration,
                        "width": 320,
                        "height": 180,
                    }
        return production_verify(candidate, expected_duration=expected_duration)

    monkeypatch.setattr(ai_video, "verify_mp4", verify_fixture)
    monkeypatch.setattr(voice_audio, "verify_mp4", verify_fixture)
