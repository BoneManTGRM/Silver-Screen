from __future__ import annotations

from pathlib import Path

import pytest

from silver_screen.voice_studio import process_voice_production
from test_voice_studio import FakeProvider, _make_audio, _request, _state, _video_result


def test_voice_regression_reports_full_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    audio_source = _make_audio(tmp_path / "source.wav")
    provider = FakeProvider(audio_source, fail_once=True)
    result = process_voice_production(
        _state(),
        _video_result(tmp_path),
        tmp_path,
        voice_inputs=[_request()],
        provider_factory=lambda config: provider,
    )
    assert result["status"] == "complete", result
