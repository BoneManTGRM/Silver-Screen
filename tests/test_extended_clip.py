import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from silver_screen.ai_video import VideoGenerationError, verify_mp4
from silver_screen.extended_clip import render_extended_clip, wan14_segment_lengths
from silver_screen.modal_clip_archive import latest_renders, read_render_progress, write_render_progress


@pytest.mark.parametrize(("seconds", "lengths"), [(6, (3, 3)), (8, (4, 4)), (11, (4, 4, 3)), (20, (5, 5, 5, 5))])
def test_extended_lengths_match_requested_duration(seconds, lengths):
    assert wan14_segment_lengths(seconds) == lengths


@pytest.mark.parametrize("first_photo", [False, True])
@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="Requires FFmpeg and FFprobe")
def test_extended_clip_continues_from_the_last_frame_and_exports_eight_seconds(tmp_path, first_photo):
    source = tmp_path / "motion.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x192:rate=16",
        "-frames:v", "65", "-c:v", "libx264", "-threads", "2", str(source),
    ], check=True, timeout=15)
    data = source.read_bytes()
    anchor = "data:image/jpeg;base64,c3RhcnQ="
    ending = "data:image/jpeg;base64,ZW5k"
    calls, updates = [], []

    def render(prompt, settings):
        calls.append((prompt, settings))
        first = len(calls) == 1
        return {
            "data": data,
            "render_stats": {"gpu_worker_seconds": 12.5, "gpus": ["NVIDIA L40S"]},
            "face_review": {"status": "matched"} if first else {"status": "possible_drift", "first_issue_seconds": 0.5},
        }

    settings = {"duration": 8, "seed": 17, "video_size": (320, 192), "save_clip": True}
    if first_photo:
        settings.update(image=anchor, end_image=ending, check_face=True)
    result = render_extended_clip("The light sweeps across the table.", settings, render, lambda *state: updates.append(state))
    assert [call[1]["duration"] for call in calls] == [4, 4]
    assert [call[1]["seed"] for call in calls] == [17, 18]
    assert calls[0][1].get("image") == (anchor if first_photo else None)
    assert calls[1][1]["image"].startswith("data:image/jpeg;base64,/9j/")
    assert "Continue directly" in calls[1][0]
    assert "end_image" not in calls[0][1]
    assert calls[1][1].get("end_image") == (ending if first_photo else None)
    if first_photo:
        assert all(call[1]["face_reference"] == anchor for call in calls)
    assert all(not call[1]["save_clip"] and not call[1]["allow_retake"] for call in calls)
    assert updates == [(0, 2), (1, 2), (2, 2)]
    assert result["face_review"] == {"status": "possible_drift", "first_issue_seconds": 4.5}
    assert result["render_stats"]["gpu_worker_seconds"] == 25
    clip = tmp_path / "extended.mp4"
    clip.write_bytes(result["data"])
    metadata = verify_mp4(clip)
    assert metadata["durationSeconds"] == 8
    assert metadata["frameRate"] == 16


def test_extended_segment_failure_surfaces_without_submitting_the_next_part():
    renderer = Mock(side_effect=VideoGenerationError("GPU ran out of memory."))
    updates = []
    with pytest.raises(VideoGenerationError, match="Segment 1 of 2 failed. GPU ran out of memory"):
        render_extended_clip("Move slowly.", {"duration": 8, "seed": 3}, renderer, lambda *state: updates.append(state))
    assert updates == [(0, 2)]
    renderer.assert_called_once()


def test_twenty_second_receipt_and_progress_are_readable_after_reopening(tmp_path):
    identifier = "fc-" + "7" * 26
    volume = Mock()
    volume.iterdir.return_value = [SimpleNamespace(path=f"/{identifier}.20.submitted", size=1, mtime=3)]
    volume.read_file.side_effect = lambda name: iter([(tmp_path / name).read_bytes()])
    assert latest_renders(volume)[0].requested_seconds == 20
    write_render_progress(volume, tmp_path, identifier, 1, 4)
    assert read_render_progress(volume, identifier) == {"complete": 1, "total": 4}
