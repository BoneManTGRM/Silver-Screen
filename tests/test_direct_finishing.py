import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from silver_screen.ai_video import VideoGenerationError
from silver_screen.direct_generation import assemble_direct_film, finish_direct_clip, verify_direct_clip, verify_direct_output
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
    def test_short_modal_result_is_not_offered_as_full_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "short.mp4"
            _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=1", "-c:v", "libx264", video)
            with self.assertRaisesRegex(VideoGenerationError, "1.0 seconds of a 4-second clip"):
                verify_direct_output(video.read_bytes(), image=False, expected_seconds=4)

    def test_long_audio_cannot_pass_as_a_full_length_picture(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "short-picture.mp4"
            _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-c:v", "libx264", "-c:a", "aac", video)
            with self.assertRaisesRegex(VideoGenerationError, "1.0 seconds of a 4-second clip"):
                verify_direct_clip(video.read_bytes(), expected_seconds=4)

    def test_rendered_video_rejects_a_repeated_frame_with_real_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "frozen.mp4"
            _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:r=24",
                    "-vf", "trim=end_frame=1,tpad=stop_mode=clone:stop_duration=3",
                    "-c:v", "libx264", "-crf", "12", video)
            with self.assertRaisesRegex(VideoGenerationError, "frozen"):
                verify_direct_clip(video.read_bytes(), expected_seconds=3, minimum_fps=24)

    def test_film_export_preserves_even_wan_frame_cadence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shots = []
            for fps in (16, 24):
                source = root / f"source-{fps}.mp4"
                _ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=320x180:r={fps}:d=2",
                        "-c:v", "libx264", source)
                shots.append(source.read_bytes())

            for selected, runtime, fps in ((shots[:1], 2, 16), (shots, 4, 48)):
                with self.subTest(fps=fps):
                    export = root / f"export-{fps}.mp4"
                    export.write_bytes(assemble_direct_film(selected, runtime, smooth=False, canvas=(960, 540)))
                    self.assert_export(export, duration=runtime, fps=fps)

    def test_48_fps_export_repeats_each_wan_frame_with_no_invented_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "wan.mp4"
            export = Path(directory) / "export.mp4"
            _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=16:d=2", "-c:v", "libx264", source)
            export.write_bytes(finish_direct_clip(source.read_bytes(), [], smooth=True, preserve_audio=True))
            self.assertEqual(_probe(export)["streams"][0]["avg_frame_rate"], "48/1")

            def frames(video):
                raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(video), "-f", "rawvideo",
                                               "-pix_fmt", "gray", "-"])
                return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 320 * 180).astype(np.int16)

            originals, delivery = frames(source), frames(export)
            self.assertEqual(len(delivery), 3 * len(originals))
            error = np.abs(delivery - np.repeat(originals, 3, axis=0)).mean(axis=1)
            self.assertLess(error.max(), 2.0)

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
