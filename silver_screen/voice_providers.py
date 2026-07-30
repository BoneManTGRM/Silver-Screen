"""Voice-provider clients and safe custom-voice enrollment.

The module intentionally uses the Python standard library so the production
container does not need a provider SDK. Provider credentials are read only
from environment variables and are never persisted in run artifacts.
"""

from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OPENAI_BUILTIN_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "onyx",
    "nova",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)

MAX_PROVIDER_AUDIO_BYTES = 20 * 1024 * 1024


class VoiceProviderError(RuntimeError):
    """Raised when a voice provider cannot create a usable audio artifact."""


@dataclass(frozen=True)
class VoiceErrorDiagnosis:
    code: str
    title: str
    detail: str
    retryable: bool


def diagnose_voice_error(error: str) -> VoiceErrorDiagnosis:
    text = (error or "").lower()
    if any(
        token in text
        for token in (
            "401",
            "unauthorized",
            "invalid api key",
            "incorrect api key",
        )
    ):
        return VoiceErrorDiagnosis(
            "authentication",
            "The voice-provider key was rejected",
            "Check the configured API key and redeploy before retrying.",
            False,
        )
    if any(
        token in text
        for token in (
            "billing",
            "credit",
            "quota",
            "payment",
            "insufficient_quota",
        )
    ):
        return VoiceErrorDiagnosis(
            "billing",
            "Voice-provider billing or credits are required",
            "Add usable provider credits or select manual uploaded audio.",
            False,
        )
    if any(
        token in text
        for token in (
            "consent",
            "permission",
            "not authorized",
            "forbidden",
            "403",
        )
    ):
        return VoiceErrorDiagnosis(
            "consent",
            "The custom voice lacks valid consent or permission",
            (
                "Use an authorized voice, provide genuine provider consent, "
                "or select a built-in voice."
            ),
            False,
        )
    if any(
        token in text
        for token in (
            "voice_id",
            "voice id",
            "voice not found",
            "404",
        )
    ):
        return VoiceErrorDiagnosis(
            "voice_not_found",
            "The selected voice could not be found",
            "Copy a valid provider voice ID or select a built-in OpenAI voice.",
            False,
        )
    if any(
        token in text
        for token in (
            "400",
            "invalid request",
            "invalid input",
            "unprocessable",
        )
    ):
        return VoiceErrorDiagnosis(
            "invalid_request",
            "The voice provider rejected the request",
            (
                "Check the voice ID, model, text, uploaded audio format, "
                "and consent fields."
            ),
            False,
        )
    if any(
        token in text
        for token in (
            "429",
            "rate limit",
            "too many requests",
        )
    ):
        return VoiceErrorDiagnosis(
            "rate_limit",
            "The voice provider is rate-limiting requests",
            (
                "Keep the saved checkpoint and retry the same line after "
                "the limit resets."
            ),
            True,
        )
    if any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "502",
            "503",
            "504",
        )
    ):
        return VoiceErrorDiagnosis(
            "temporary",
            "The voice provider is temporarily unavailable",
            (
                "Retry the same saved line instead of creating a duplicate "
                "production."
            ),
            True,
        )
    if any(
        token in text
        for token in (
            "safety",
            "policy",
            "moderation",
        )
    ):
        return VoiceErrorDiagnosis(
            "safety",
            "The voice provider rejected the requested speech",
            (
                "Revise the affected line and use only authorized, "
                "non-deceptive content."
            ),
            False,
        )
    if any(
        token in text
        for token in (
            "audio",
            "mp3",
            "ffprobe",
            "empty",
            "too small",
            "decode",
        )
    ):
        return VoiceErrorDiagnosis(
            "invalid_audio",
            "The provider response was not usable audio",
            (
                "TGRM can retry only the affected line with simplified "
                "delivery settings."
            ),
            True,
        )
    return VoiceErrorDiagnosis(
        "unknown",
        "Voice generation failed",
        "Inspect the exact provider error and retry only the affected line.",
        True,
    )


def provider_capabilities() -> dict[str, Any]:
    return {
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
        "manual": True,
        "openaiModel": os.getenv(
            "SILVER_SCREEN_OPENAI_TTS_MODEL",
            "gpt-4o-mini-tts",
        ),
        "elevenLabsModel": os.getenv(
            "SILVER_SCREEN_ELEVENLABS_MODEL",
            "eleven_multilingual_v2",
        ),
        "openaiBuiltInVoices": list(OPENAI_BUILTIN_VOICES),
        "customVoiceEnrollment": bool(os.getenv("OPENAI_API_KEY")),
    }


def _atomic_bytes(path: Path, data: bytes) -> Path:
    if len(data) < 256:
        raise VoiceProviderError(
            "Voice provider returned an empty or unusably small audio file"
        )
    if len(data) > MAX_PROVIDER_AUDIO_BYTES:
        raise VoiceProviderError(
            "Voice provider returned an unexpectedly large audio file"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
    ) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return path


def _json_request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout: int = 180,
) -> tuple[bytes, str]:
    data = (
        json.dumps(payload).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            body = response.read(MAX_PROVIDER_AUDIO_BYTES + 1)
            content_type = str(
                response.headers.get("Content-Type") or ""
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise VoiceProviderError(
            f"Voice provider returned HTTP {exc.code}: {detail[:1800]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise VoiceProviderError(
            f"Could not reach voice provider: {exc.reason}"
        ) from exc
    if len(body) > MAX_PROVIDER_AUDIO_BYTES:
        raise VoiceProviderError(
            "Voice provider response exceeded the audio size limit"
        )
    return body, content_type


def _multipart_body(
    fields: dict[str, str],
    files: dict[str, Path],
) -> tuple[bytes, str]:
    boundary = f"----SilverScreen{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"'
                    "\r\n\r\n"
                ).encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, path in files.items():
        if not path.exists() or not path.is_file():
            raise VoiceProviderError(
                f"Required voice file is missing: {path}"
            )
        if path.stat().st_size > 10 * 1024 * 1024:
            raise VoiceProviderError(
                f"Voice enrollment file exceeds 10 MiB: {path.name}"
            )
        mime = (
            mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{path.name}"\r\n'
                ).encode(),
                f"Content-Type: {mime}\r\n\r\n".encode(),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def _multipart_request(
    *,
    url: str,
    headers: dict[str, str],
    fields: dict[str, str],
    files: dict[str, Path],
    timeout: int = 240,
) -> dict[str, Any]:
    body, boundary = _multipart_body(fields, files)
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            **headers,
            "Content-Type": (
                f"multipart/form-data; boundary={boundary}"
            ),
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise VoiceProviderError(
            f"Voice provider returned HTTP {exc.code}: {detail[:1800]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise VoiceProviderError(
            f"Could not reach voice provider: {exc.reason}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VoiceProviderError(
            "Voice provider returned invalid enrollment JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise VoiceProviderError(
            "Voice provider returned an unexpected enrollment response"
        )
    return payload


class OpenAIVoiceProvider:
    name = "openai"

    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
    ) -> None:
        self.token = (
            token or os.getenv("OPENAI_API_KEY") or ""
        ).strip()
        self.model = (
            model
            or os.getenv("SILVER_SCREEN_OPENAI_TTS_MODEL")
            or "gpt-4o-mini-tts"
        ).strip()
        if not self.token:
            raise VoiceProviderError(
                "OPENAI_API_KEY is not configured for OpenAI speech generation"
            )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "audio/mpeg,application/json",
        }

    def synthesize(
        self,
        *,
        text: str,
        voice: str,
        destination: Path,
        instructions: str = "",
        speed: float = 1.0,
        seed: int | None = None,
    ) -> Path:
        voice_value: str | dict[str, str]
        if voice.startswith("voice_"):
            voice_value = {"id": voice}
        else:
            voice_value = (
                voice
                if voice in OPENAI_BUILTIN_VOICES
                else "coral"
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "input": text[:4096],
            "voice": voice_value,
            "response_format": "mp3",
            "speed": max(0.25, min(4.0, float(speed))),
        }
        if (
            instructions
            and self.model not in {"tts-1", "tts-1-hd"}
        ):
            payload["instructions"] = instructions[:4096]
        body, content_type = _json_request(
            url="https://api.openai.com/v1/audio/speech",
            method="POST",
            headers=self.headers,
            payload=payload,
        )
        if "json" in content_type.lower():
            detail = body.decode(
                "utf-8",
                errors="replace",
            )[:1200]
            raise VoiceProviderError(
                f"OpenAI returned JSON instead of audio: {detail}"
            )
        return _atomic_bytes(destination, body)

    def create_consent(
        self,
        *,
        recording: Path,
        name: str,
        language: str = "en-US",
    ) -> str:
        payload = _multipart_request(
            url="https://api.openai.com/v1/audio/voice_consents",
            headers={"Authorization": f"Bearer {self.token}"},
            fields={
                "name": name[:120],
                "language": language[:35],
            },
            files={"recording": recording},
        )
        consent_id = str(payload.get("id") or "")
        if not consent_id:
            raise VoiceProviderError(
                "OpenAI did not return a consent recording ID"
            )
        return consent_id

    def create_custom_voice(
        self,
        *,
        audio_sample: Path,
        consent_id: str,
        name: str,
    ) -> str:
        payload = _multipart_request(
            url="https://api.openai.com/v1/audio/voices",
            headers={"Authorization": f"Bearer {self.token}"},
            fields={
                "name": name[:120],
                "consent": consent_id,
            },
            files={"audio_sample": audio_sample},
        )
        voice_id = str(payload.get("id") or "")
        if not voice_id:
            raise VoiceProviderError(
                "OpenAI did not return a custom voice ID"
            )
        return voice_id


class ElevenLabsVoiceProvider:
    name = "elevenlabs"

    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
    ) -> None:
        self.token = (
            token or os.getenv("ELEVENLABS_API_KEY") or ""
        ).strip()
        self.model = (
            model
            or os.getenv("SILVER_SCREEN_ELEVENLABS_MODEL")
            or "eleven_multilingual_v2"
        ).strip()
        if not self.token:
            raise VoiceProviderError(
                "ELEVENLABS_API_KEY is not configured for ElevenLabs speech generation"
            )

    def synthesize(
        self,
        *,
        text: str,
        voice: str,
        destination: Path,
        instructions: str = "",
        speed: float = 1.0,
        seed: int | None = None,
    ) -> Path:
        if not voice.strip():
            raise VoiceProviderError(
                "An ElevenLabs voice ID is required"
            )
        endpoint = (
            "https://api.elevenlabs.io/v1/text-to-speech/"
            f"{urllib.parse.quote(voice.strip(), safe='')}"
            "?output_format=mp3_44100_128"
        )
        payload: dict[str, Any] = {
            "text": text[:40000],
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": max(0.7, min(1.2, float(speed))),
            },
        }
        if seed is not None:
            payload["seed"] = int(seed) & 0xFFFFFFFF
        body, content_type = _json_request(
            url=endpoint,
            method="POST",
            headers={
                "xi-api-key": self.token,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg,application/json",
            },
            payload=payload,
        )
        if "json" in content_type.lower():
            detail = body.decode(
                "utf-8",
                errors="replace",
            )[:1200]
            raise VoiceProviderError(
                "ElevenLabs returned JSON instead of audio: "
                f"{detail}"
            )
        return _atomic_bytes(destination, body)


def make_voice_provider(config: dict[str, Any]):
    provider = str(
        config.get("provider") or "openai"
    ).lower()
    model = str(config.get("model") or "").strip() or None
    if provider == "openai":
        return OpenAIVoiceProvider(model=model)
    if provider == "elevenlabs":
        return ElevenLabsVoiceProvider(model=model)
    if provider == "manual":
        return None
    raise VoiceProviderError(
        f"Unsupported voice provider: {provider}"
    )
