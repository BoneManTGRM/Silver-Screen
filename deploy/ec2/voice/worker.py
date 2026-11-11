"""Private Chatterbox worker for the EC2 Compose deployment."""

import io
import json
import logging
import os
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(os.getenv("CAST_SAMPLE_DIR", "/workspace/cast/clones"))
ROLES = {"lead", "co-star"}
LANGUAGES = {"en", "es"}


@lru_cache(maxsize=1)
def load_model():
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    return ChatterboxMultilingualTTS.from_pretrained(device="cpu")


def synthesize(text, language, sample):
    import torchaudio

    model = load_model()
    audio = model.generate(text, language_id=language, audio_prompt_path=str(sample))
    output = io.BytesIO()
    torchaudio.save(output, audio.cpu(), model.sr, format="wav", encoding="PCM_S", bits_per_sample=16)
    return output.getvalue()


class VoiceHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        if self.path != "/speech":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            role, language, text = (payload[key] for key in ("role", "language", "text"))
            if (not isinstance(role, str) or role not in ROLES or not isinstance(language, str)
                    or language not in LANGUAGES or not isinstance(text, str) or not 0 < len(text.strip()) <= 300):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            self.send_error(400)
            return
        sample = ROOT / f"{role}.wav"
        if not sample.is_file():
            self.send_error(404)
            return
        try:
            audio = synthesize(text.strip(), language, sample)
        except Exception as exc:
            logging.error("Voice synthesis failed (%s)", type(exc).__name__)
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)


if __name__ == "__main__":
    # Chatterbox changes its speaker conditioning per request. Serve one line at a time.
    HTTPServer(("0.0.0.0", 8765), VoiceHandler).serve_forever()
