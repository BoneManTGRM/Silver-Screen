from __future__ import annotations

from silver_screen.script_sync import (
    ScriptSyncConfig,
    build_timing_plan,
    estimate_speech_seconds,
    parse_script,
    render_ass,
    render_srt,
    render_vtt,
)


def _video_result(shots: int = 2, duration: float = 8.0):
    return {
        "queue": {
            "shots": [
                {
                    "id": f"shot_{index + 1:04d}",
                    "order": index + 1,
                    "status": "verified",
                    "path": f"media/clips/shot_{index + 1:04d}.mp4",
                    "verifiedDurationSeconds": duration,
                }
                for index in range(shots)
            ]
        }
    }


def test_parse_script_supports_speakers_and_timecodes():
    parsed = parse_script(
        "[00:00.200 --> 00:03.500] MOONIE MOO: Welcome to my premiere.\n"
        "BULLY: I came for the snacks."
    )
    assert len(parsed) == 2
    assert parsed[0]["speaker"] == "MOONIE MOO"
    assert parsed[0]["explicitStart"] == 0.2
    assert parsed[0]["explicitEnd"] == 3.5
    assert parsed[1]["speaker"] == "BULLY"


def test_plain_text_becomes_narration():
    parsed = parse_script("A glamorous celebrity arrives beneath a full moon.")
    assert parsed == [
        {
            "id": "script_0001",
            "order": 1,
            "speaker": "Narrator",
            "text": "A glamorous celebrity arrives beneath a full moon.",
            "explicitStart": None,
            "explicitEnd": None,
            "context": "",
        }
    ]


def test_timing_plan_assigns_lines_to_verified_shots():
    plan = build_timing_plan(
        "MOONIE MOO: The cameras are waiting.\nBULLY: Then let them wait.",
        _video_result(),
        ScriptSyncConfig(words_per_minute=150),
    )
    assert plan["metrics"]["lineCount"] == 2
    assert plan["lines"][0]["shotId"] == "shot_0001"
    assert plan["lines"][0]["globalStartSeconds"] >= 0
    assert plan["lines"][0]["globalEndSeconds"] <= 16
    assert plan["metrics"]["fitStatus"] in {"fits", "needs_timing_repair"}


def test_explicit_timecode_is_preserved():
    plan = build_timing_plan(
        "[00:08.100 --> 00:11.000] NARRATOR: The second act begins.",
        _video_result(),
        ScriptSyncConfig(),
    )
    line = plan["lines"][0]
    assert line["shotId"] == "shot_0002"
    assert line["globalStartSeconds"] == 8.1
    assert line["localStartSeconds"] == 0.1


def test_speech_estimate_increases_with_word_count():
    short = estimate_speech_seconds("Hello there.")
    long = estimate_speech_seconds("Hello there, welcome to the biggest premiere of the entire year.")
    assert long > short


def test_subtitle_exports_include_timing_and_speaker():
    lines = [
        {
            "speaker": "Moonie Moo",
            "text": "The spotlight is ready.",
            "globalStartSeconds": 0.2,
            "globalEndSeconds": 2.4,
        }
    ]
    srt = render_srt(lines)
    vtt = render_vtt(lines)
    ass = render_ass(lines, "cinematic")
    assert "00:00:00,200 --> 00:00:02,400" in srt
    assert "Moonie Moo: The spotlight is ready." in srt
    assert vtt.startswith("WEBVTT")
    assert "[V4+ Styles]" in ass
    assert "Dialogue:" in ass


def test_config_rejects_unauthorized_elevenlabs_ids():
    try:
        ScriptSyncConfig(provider="elevenlabs").normalized()
    except Exception as exc:
        assert "authorization" in str(exc).lower()
    else:
        raise AssertionError("Expected authorization failure")
