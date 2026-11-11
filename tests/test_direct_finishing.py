import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from silver_screen.ai_video import VideoGenerationError
from silver_screen.direct_generation import assemble_direct_film, finish_direct_clip, verify_direct_output
from silver_screen.voice_audio import verify_audio
from silver_screen.voice_config import VoiceStudioError


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def _probe(path):
    result = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,avg_frame_rate,nb_frames",
        "-of", "json", str(path),
    ])
    return json.loads(result)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Requires FFmpeg and FFprobe")
class DirectFinishingTests(unittest.TestCase):
    def test_rejects_media_without_expected_track(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "only-audio.mp4"
            video = root / "only-video.mp4"
            _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:a", "aac", audio)
            _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=3", "-c:v", "libx264", video)
            with self.assertRaisesRegex(VideoGenerationError, "no readable video track"):
                verify_direct_output(audio.read_bytes(), image=False)
            with self.assertRaisesRegex(VoiceStudioError, "no audio track"):
                verify_audio(video)

    def test_recorded_dialogue_and_runtime_limited_cut(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.mp4"
            alternate = root / "alternate.mp4"
            _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=3", "-f", "lavfi", "-i",
                    "sine=frequency=200:duration=3", "-c:v", "libx264", "-c:a", "aac", original)
            _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=180x320:r=24:d=3", "-c:v", "libx264", alternate)
            first = root / "first.wav"
            second = root / "second.m4a"
            _ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=0.5", first)
            _ffmpeg("-f", "lavfi", "-i", "sine=frequency=500:duration=0.5", "-c:a", "aac", second)
            recordings = [(first.read_bytes(), 0.25), (second.read_bytes(), 1.25)]
            edited = root / "edited.mp4"
            edited.write_bytes(finish_direct_clip(original.read_bytes(), recordings, smooth=True, preserve_audio=True))
            self.assert_export(edited, duration=3, fps=48)

            for fps in (24, 48):
                with self.subTest(fps=fps):
                    cut = root / "film.mp4"
                    cut.write_bytes(assemble_direct_film([edited.read_bytes(), alternate.read_bytes()], 4, smooth=fps == 48))
                    self.assert_export(cut, duration=4, fps=fps)

            with self.assertRaisesRegex(VideoGenerationError, "Render another shot"):
                assemble_direct_film([edited.read_bytes()], 4, smooth=False)
            with self.assertRaisesRegex(VideoGenerationError, "extends past"):
                finish_direct_clip(original.read_bytes(), [(first.read_bytes(), 3.0)], smooth=False, preserve_audio=False)

    def assert_export(self, path, *, duration, fps):
        payload = _probe(path)
        self.assertAlmostEqual(float(payload["format"]["duration"]), duration, delta=0.025)
        video, audio = payload["streams"]
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(video["avg_frame_rate"], f"{fps}/1")
        self.assertEqual(int(video["nb_frames"]), duration * fps)
        self.assertEqual(audio["codec_name"], "aac")
