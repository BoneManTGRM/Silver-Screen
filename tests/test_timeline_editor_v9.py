from __future__ import annotations

from types import SimpleNamespace

from silver_screen.timeline_editor import normalize_timeline


def test_timeline_normalization_reorders_trims_and_sanitizes(tmp_path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    first = media / "shot_0001.mp4"
    second = media / "shot_0002.mp4"
    first.write_bytes(b"candidate")
    second.write_bytes(b"candidate")
    workspace = SimpleNamespace(media_dir=media)
    timeline = {
        "runId": "run-1",
        "items": [
            {
                "timelineOrder": 2,
                "shotId": "shot_0001",
                "sourcePath": first.name,
                "inSeconds": 0.5,
                "outSeconds": 7.5,
                "transitionStyle": "unsafe-style",
                "transitionSeconds": 9,
                "locked": True,
            },
            {
                "timelineOrder": 1,
                "shotId": "shot_0002",
                "sourcePath": second.name,
                "inSeconds": 0,
                "outSeconds": 8,
                "transitionStyle": "fadeblack",
                "transitionSeconds": 0.4,
                "locked": False,
            },
        ],
    }
    normalized = normalize_timeline(timeline, workspace=workspace)
    assert [item["shotId"] for item in normalized["items"]] == [
        "shot_0002",
        "shot_0001",
    ]
    assert normalized["items"][0]["transitionStyle"] == "fadeblack"
    assert normalized["items"][1]["transitionStyle"] == "fade"
    assert normalized["items"][1]["transitionSeconds"] == 0.0
    assert normalized["items"][1]["locked"] is True
