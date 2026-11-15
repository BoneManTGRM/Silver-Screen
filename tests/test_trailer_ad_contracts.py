"""Regression checks for explicit movie/series trailers and commercial retry capacity."""
from pathlib import Path
import json

import pytest
from streamlit.testing.v1 import AppTest

from silver_screen.screenplay_writer import ScreenplayError, screenplay_draft, screenplay_request


ROOT = Path(__file__).resolve().parents[1]


def _trailer_script() -> dict:
    actions = [
        "The camera creeps through a deserted station as every overhead display turns red.",
        "A courier grabs a silver briefcase while security shutters crash down behind her.",
        "A detective studies the same symbol glowing across every building in the city.",
        "The courier spots a familiar face reflected in a dark train window and turns.",
        "A train plunges into a tunnel as the final emergency light fades to black.",
    ]
    return {
        "title": "The Last Signal",
        "logline": "A courier finds the message that could change an entire city.",
        "genre": "thriller",
        "tone": "cinematic",
        "lead": {"name": "Maya", "role": "Courier", "appearance": "Short dark hair and a blue coat."},
        "support": {"name": "Iris", "role": "Detective", "appearance": "Silver hair and a black jacket."},
        "scenes": [
            {"heading": "EXT. CITY STATION - NIGHT", "action": action, "speaker": "none", "line": ""}
            for action in actions
        ],
    }


@pytest.mark.parametrize("trailer_type", ["movie", "series"])
def test_trailer_request_uses_trailer_instructions_and_selected_source_type(trailer_type):
    req = screenplay_request(
        "Make a suspenseful trailer for The Last Signal.", "Maya", 30, 42,
        kind="trailer", trailer_type=trailer_type,
    )
    assert "trailer" in req["messages"][0]["content"].casefold()
    user = json.loads(req["messages"][1]["content"])
    assert user["trailer type"] == trailer_type
    assert req["response_format"]["json_schema"]["schema"]["properties"]["scenes"]["maxItems"] == 5


def test_series_trailer_draft_preserves_its_type_and_cast():
    draft = screenplay_draft(
        _trailer_script(), idea="Make a suspenseful television series trailer.",
        lead="Maya", seconds=30, seed=42, kind="trailer", trailer_type="series",
    )
    assert draft["kind"] == "trailer"
    assert draft["screenplayWriter"] == "trailer"
    assert draft["trailerType"] == "series"
    assert draft["format"] == "trailer"
    assert len(draft["shotDirection"]["shotPromptOverrides"]) == 5
    assert draft["cast"][0]["name"] == "Maya"


def test_unknown_trailer_type_rejected_before_paid_generation():
    with pytest.raises(ScreenplayError, match="trailer"):
        screenplay_request(
            "Make a suspenseful trailer for The Last Signal.", "", 30, 42,
            kind="trailer", trailer_type="advertisement",
        )


def test_writer_page_exposes_movie_and_tv_series_trailers():
    # Standalone AppTest does not register Streamlit page_link targets.
    page = AppTest.from_file(ROOT / "pages/0_Write_a_Movie.py").run()
    kinds = next(radio for radio in page.radio if radio.label == "What are you making?")
    assert "Movie trailer" in kinds.options
    assert "TV show trailer" in kinds.options


def test_advertisement_default_budget_allows_one_retake_per_shot():
    page = AppTest.from_file(ROOT / "pages/9_Autonomous_Studio.py")
    page.session_state["screenplay-production"] = {
        "kind": "ad", "seconds": 20, "title": "Test advertisement",
        "premise": "A detailed product advertisement for a travel mug.",
        "authoredScript": "Five planned shots", "seed": 42,
    }
    page.run()
    assert not page.exception
    request_budget = next(field for field in page.number_input if field.label == "Maximum video requests")
    assert request_budget.value == 10


def test_completed_modal_render_always_has_native_video_and_download(monkeypatch):
    """An embedded HTTP preview must not be the only way to see a finished clip."""
    from silver_screen.ai_video import DEFAULT_MODEL

    monkeypatch.setenv("SILVER_SCREEN_HTTP_RENDER_PREVIEW", "1")
    monkeypatch.delenv("SILVER_SCREEN_MODAL_APP", raising=False)
    app = AppTest.from_file(ROOT / "pages/0_Direct_Create.py")
    app.session_state["direct_create_job"] = {
        "backend": "ComfyUI", "model": DEFAULT_MODEL,
        "prediction": {"id": "fc-" + "1" * 26, "status": "succeeded", "archived": True},
        "data": b"\\x00\\x00\\x00\\x20ftypisom" + (b"video fixture" * 40),
        "requested_seconds": 4,
    }
    app.run()
    assert not app.exception
    assert app.get("video"), "The finished clip must be visible in a native player."
    labels = [element.proto.label for element in app.get("download_button")]
    assert "Download original clip" in labels
