from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from silver_screen import modal_renderer, screenplay_writer
from silver_screen.screenplay_writer import ScreenplayError


APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"
CALL_ID = "fc-" + "5" * 26


@pytest.mark.parametrize("failure", [False, True])
def test_writer_link_restores_pending_and_finished_work(monkeypatch, tmp_path, failure):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(modal_renderer, "screenplay_writer_configured", lambda: True)
    idea = "Show a blue mug on a morning commute."
    job = {"id": CALL_ID, "idea": idea, "lead": "", "seconds": 20, "seed": 8, "kind": "ad", "end_card": None}
    submit = Mock(return_value=job)
    check = Mock(return_value=None)
    monkeypatch.setattr(screenplay_writer, "submit_screenplay", submit)
    monkeypatch.setattr(screenplay_writer, "check_screenplay", check)

    started = AppTest.from_file(APP).run()
    started.radio[0].set_value("ad").run()
    started.text_area[0].set_value(idea)
    next(button for button in started.button if button.label == "Write script").click().run()
    assert not started.exception
    assert started.query_params.get("draft") in (CALL_ID, [CALL_ID])
    submit.assert_called_once_with(idea, "", 20, kind="ad", end_card=None)
    check.assert_called_with(job)

    error = "The writer reached its time limit."
    if failure:
        check.side_effect = ScreenplayError(error)
    else:
        check.return_value = {
            "title": "Morning Mug", "premise": idea, "kind": "ad", "seconds": 20, "cast": [],
            "authoredScript": "A commuter holds the blue mug.", "shotDirection": {"shotPromptOverrides": {}},
        }
    reopened = AppTest.from_file(APP)
    reopened.query_params["draft"] = CALL_ID
    reopened.run()
    assert not reopened.exception
    check.assert_called_with(job)
    checks = check.call_count

    check.side_effect = AssertionError("An already resolved screenplay was checked again")
    restored = AppTest.from_file(APP)
    restored.query_params["draft"] = CALL_ID
    restored.run()
    assert not restored.exception
    started.run()
    assert not started.exception
    assert [radio.value for radio in restored.radio[:2]] == ["ad", 20]
    assert restored.text_area[0].value == idea
    assert check.call_count == checks
    assert submit.call_count == 1
    if failure:
        assert error in [element.value for element in restored.error]
        assert not next(button for button in restored.button if button.label == "Write script").disabled
    else:
        assert "Morning Mug" in [element.value for element in restored.header]
        assert "Morning Mug" in [element.value for element in started.header]
        assert "Download script" in [element.proto.label for element in restored.get("download_button")]
        expired = {"job": job, "error": "Modal no longer has this draft."}
        kept = screenplay_writer.save_screenplay_checkpoint(expired)
        assert kept["draft"]["title"] == "Morning Mug"
        assert screenplay_writer.load_screenplay_checkpoint(CALL_ID) == kept
