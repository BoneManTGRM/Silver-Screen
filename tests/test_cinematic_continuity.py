from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from silver_screen import ai_video
from silver_screen.cinematic_continuity import (
    assemble_cinematic_clips,
    build_transition_plan,
    build_xfade_filter,
    plan_transition,
    transition_relation,
    transition_rows,
    transition_settings,
)


def _shot(
    order: int,
    *,
    scene: int,
    chapter: int,
    status: str = "verified",
    path: str | None = None,
    continuity: bool = True,
) -> dict[str, Any]:
    return {
        "id": f"shot_{order:04d}",
        "order": order,
        "status": status,
        "sourceScene": {
            "number": scene,
            "chapter": chapter,
        },
        "segment": order,
        "path": path,
        "plannedDurationSeconds": 8.0,
        "verifiedDurationSeconds": 8.0,
        "continuityUsed": continuity,
    }


def test_transition_relation_and_styles_follow_story_structure() -> None:
    first = _shot(1, scene=1, chapter=1)
    same_scene = _shot(2, scene=1, chapter=1)
    new_scene = _shot(3, scene=2, chapter=1)
    new_chapter = _shot(4, scene=3, chapter=2)

    assert transition_relation(first, same_scene) == "continuation"
    assert transition_relation(same_scene, new_scene) == "scene_change"
    assert transition_relation(new_scene, new_chapter) == "chapter_change"

    cfg = transition_settings(mode="auto", analyze_frames=False)
    assert plan_transition(first, same_scene, cfg)["style"] == "fade"
    assert plan_transition(same_scene, new_scene, cfg)["style"] == "fade"
    assert plan_transition(new_scene, new_chapter, cfg)["style"] == "fadeblack"


def test_transition_plan_creates_overlap_timeline_without_files(
    tmp_path: Path,
) -> None:
    queue = {
        "productionId": "video-test",
        "shots": [
            _shot(1, scene=1, chapter=1, path="clips/a.mp4"),
            _shot(2, scene=1, chapter=1, path="clips/b.mp4"),
            _shot(3, scene=2, chapter=1, path="clips/c.mp4"),
        ],
    }
    plan = build_transition_plan(
        queue,
        tmp_path,
        cfg=transition_settings(
            mode="auto",
            analyze_frames=False,
        ),
    )
    assert plan["metrics"]["boundaries"] == 2
    assert plan["metrics"]["hardCutsAvoided"] == 2
    assert plan["metrics"]["assembledRuntimeSeconds"] < 24.0
    assert queue["shots"][1]["timelineStartSeconds"] < 8.0
    assert queue["shots"][1]["transitionIn"]["relation"] == "continuation"
    assert len(transition_rows(queue)) == 2
    assert (tmp_path / "transition_plan.json").exists()
    assert (tmp_path / "transition_runtime.json").exists()


def test_xfade_graph_chains_video_and_audio_with_correct_runtime() -> None:
    graph, video, audio, runtime, effective = build_xfade_filter(
        [8.0, 8.0, 8.0],
        [
            {"style": "fade", "durationSeconds": 0.2},
            {"style": "fade", "durationSeconds": 0.3},
        ],
    )
    assert graph.count("xfade=transition=fade") == 2
    assert "acrossfade" in graph
    assert video == "v2"
    assert audio == "a2"
    assert runtime == pytest.approx(23.5)
    assert effective[1]["offsetSeconds"] == pytest.approx(15.5)


def test_provider_prompt_is_upgraded_for_same_take_continuation() -> None:
    state = {
        "genre": "thriller",
        "tone": "cinematic",
        "storyBible": {"motif": "red light"},
        "characters": [
            {
                "id": "lead",
                "name": "Cody",
                "description": "the exact same authorized lead actor",
            }
        ],
        "_videoShots": [],
    }
    scene = {
        "number": 1,
        "slugline": "EXT. ROOFTOP - NIGHT",
        "characters": ["lead"],
        "action": "Cody continues running toward the helicopter.",
        "conflict": "The door is closing.",
    }
    shot = {
        "segment": 2,
        "continuityUsed": True,
    }
    prompt = ai_video.scene_prompt(state, scene, shot)
    assert "literal final frame" in prompt
    assert "do not reset the take" in prompt
    assert getattr(
        ai_video,
        "_cinematic_continuity_installed",
        False,
    )


def _ffmpeg_for_test() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pytest.skip("FFmpeg is required for cinematic assembly")


def _make_clip(path: Path, color: str, frequency: int) -> None:
    ffmpeg = _ffmpeg_for_test()
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=640x360:r=24",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000",
            "-t",
            "1.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr


def test_cinematic_assembly_crossfades_playable_video_and_audio(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    destination = tmp_path / "cinematic.mp4"
    _make_clip(first, "red", 440)
    _make_clip(second, "blue", 660)

    report = assemble_cinematic_clips(
        [first, second],
        destination,
        [
            {
                "style": "fade",
                "durationSeconds": 0.30,
            }
        ],
        cfg=transition_settings(
            mode="auto",
            analyze_frames=False,
        ),
    )

    assert destination.exists()
    assert destination.stat().st_size > 16_384
    with destination.open("rb") as handle:
        assert b"ftyp" in handle.read(128)
    assert report["durationSeconds"] > 2.0
    assert report["durationSeconds"] < 3.0
    assert report["effectiveTransitions"][0]["style"] == "fade"


def test_script_sync_uses_overlap_aware_cinematic_timeline() -> None:
    from silver_screen.script_sync import ScriptSyncConfig, build_timing_plan

    video_result = {
        "queue": {
            "shots": [
                {
                    **_shot(1, scene=1, chapter=1, path="a.mp4"),
                    "timelineStartSeconds": 0.0,
                    "timelineEndSeconds": 8.0,
                },
                {
                    **_shot(2, scene=2, chapter=1, path="b.mp4"),
                    "timelineStartSeconds": 7.7,
                    "timelineEndSeconds": 15.7,
                },
            ]
        }
    }
    plan = build_timing_plan(
        "CODY: We move before the door closes.",
        video_result,
        ScriptSyncConfig(),
    )
    assert plan["videoDurationSeconds"] == pytest.approx(15.7)
    assert plan["metrics"]["cinematicTimeline"] is True
    assert plan["metrics"]["transitionOverlapSeconds"] == pytest.approx(0.3)


def test_cinematic_continuity_page_compiles() -> None:
    page = Path("pages/4_Cinematic_Continuity.py")
    compile(page.read_text(encoding="utf-8"), str(page), "exec")
