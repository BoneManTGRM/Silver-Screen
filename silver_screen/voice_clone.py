"""Render a line using one of the private studio's enrolled voices."""

import json
import os
import urllib.error
import urllib.request

from .ai_video import VideoGenerationError
from .cast_library import VOICE_ROLES

LANGUAGES = {"English": "en", "Spanish": "es"}


def voice_clone_ready() -> bool:
    return bool(os.getenv("SILVER_SCREEN_VOICE_URL", "").strip())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def render_cloned_line(role: str, language: str, text: str) -> bytes:
    endpoint = os.getenv("SILVER_SCREEN_VOICE_URL", "").strip().rstrip("/")
    if not endpoint:
        raise VideoGenerationError("Voice cloning is not configured for this studio.")
    text = text.strip()
    if not 0 < len(text) <= 300:
        raise VideoGenerationError("Type a line of dialogue up to 300 characters.")
    if role not in VOICE_ROLES or language not in LANGUAGES:
        raise VideoGenerationError("Choose one of your enrolled cast voices and a language.")
    request = urllib.request.Request(
        f"{endpoint}/speech",
        data=json.dumps({"role": VOICE_ROLES[role], "language": LANGUAGES[language], "text": text},
                        ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "audio/wav"},
        method="POST",
    )
    limit = 4 * 1024 * 1024
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=180) as response:
            audio = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise VideoGenerationError("Save this speaker's clone sample in Cast Library first.") from exc
        raise VideoGenerationError(f"The private voice renderer returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VideoGenerationError("The private voice renderer is not reachable or did not finish in time.") from exc
    if not 44 < len(audio) <= limit or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise VideoGenerationError("The voice renderer did not return usable WAV audio.")
    return audio
