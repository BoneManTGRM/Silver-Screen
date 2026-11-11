import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from silver_screen.ai_video import VideoGenerationError
from silver_screen.cast_library import save_clone_sample, saved_clone_samples
from silver_screen.script_sync import ScriptSyncConfig, render_script_production
from silver_screen.voice_audio import _has_audio
from silver_screen.voice_clone import render_cloned_line


def _wav(seconds):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x10\x00" * int(24000 * seconds))
    return output.getvalue()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Requires FFmpeg and FFprobe")
class PrivateCastVoiceTests(unittest.TestCase):
    def test_enroll_select_and_mix_two_speakers_in_a_film(self):
        spec = importlib.util.spec_from_file_location("voice_worker", "deploy/ec2/voice/worker.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cast = root / "cast"
            with self.assertRaisesRegex(VideoGenerationError, "5 to 30 seconds"):
                save_clone_sample(cast, "Your voice", _wav(1))
            save_clone_sample(cast, "Your voice", _wav(6))
            calls = []

            def fake_model(text, language, sample):
                self.assertTrue(sample.is_file())
                calls.append((text, language, sample.stem))
                return _wav(0.5)

            with patch.object(worker, "ROOT", cast / "clones"), patch.object(worker, "synthesize", fake_model):
                with HTTPServer(("127.0.0.1", 0), worker.VoiceHandler) as server:
                    thread = Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        with patch.dict(os.environ, {"SILVER_SCREEN_VOICE_URL": f"http://127.0.0.1:{server.server_port}"}):
                            self.assertEqual(render_cloned_line("Your voice", "English", "I am ready."), _wav(0.5))
                            with self.assertRaisesRegex(VideoGenerationError, "Save this speaker"):
                                render_cloned_line("Co-star voice", "Spanish", "Estoy lista.")
                            save_clone_sample(cast, "Co-star voice", _wav(7))
                            self.assertEqual(set(saved_clone_samples(cast)), {"Your voice", "Co-star voice"})
                            self._render_film(root)
                    finally:
                        server.shutdown()
                        thread.join()
            self.assertEqual(calls, [
                ("I am ready.", "en", "lead"),
                ("Run.", "en", "lead"),
                ("Estoy lista.", "es", "co-star"),
            ])

    def _render_film(self, root):
        source = root / "media" / "clips" / "source.mp4"
        source.parent.mkdir(parents=True)
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=3",
            "-c:v", "libx264", str(source),
        ], check=True)
        shots = [{"id": f"shot-{index}", "order": index, "path": "clips/source.mp4",
                  "verifiedDurationSeconds": 3, "status": "verified"} for index in (1, 2)]
        config = ScriptSyncConfig(provider="private_cast", authorization_confirmed=True,
                                  voice_map={"A": "Your voice:English", "B": "Co-star voice:Spanish"})
        film = render_script_production(
            root, {"queue": {"shots": shots}},
            "[00:03.150 --> 00:04.100] A: Run.\n[00:04.200 --> 00:05.500] B: Estoy lista.", config,
        )
        self.assertTrue(_has_audio(Path(film["final_video_path"])))
        self.assertEqual([line["voice"] for line in film["plan"]["lines"]],
                         ["Your voice:English", "Co-star voice:Spanish"])
