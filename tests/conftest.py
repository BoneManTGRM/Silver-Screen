from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _voice_provider_test_key(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Use a non-secret placeholder key only for injected offline voice-provider tests."""

    path = getattr(request.node, "path", None)
    filename = getattr(path, "name", "") or getattr(getattr(request.node, "fspath", None), "basename", "")
    if filename == "test_voice_studio.py":
        monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
