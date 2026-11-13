from types import SimpleNamespace
import sys

import pytest
from modal import exception as modal_errors

from silver_screen.comfyui import make_comfyui_client
from silver_screen.screenplay_writer import ScreenplayError, check_screenplay


def test_modal_submission_can_resume_from_call_id(monkeypatch):
    monkeypatch.setenv('SILVER_SCREEN_MODAL_APP', 'test-renderer')
    monkeypatch.setenv('MODAL_TOKEN_ID', 'test-id')
    monkeypatch.setenv('MODAL_TOKEN_SECRET', 'test-secret')
    received = []
    lookups = []

    def spawn(*args):
        received.append(args)
        return SimpleNamespace(object_id='fc-test')

    def get(*, timeout):
        assert timeout == 0
        if len(lookups) == 1:
            raise modal_errors.TimeoutError
        return b'media'

    def from_id(prediction_id):
        lookups.append(prediction_id)
        return SimpleNamespace(get=get)

    monkeypatch.setitem(sys.modules, 'modal', SimpleNamespace(
        Function=SimpleNamespace(from_name=lambda *args: SimpleNamespace(spawn=spawn)),
        FunctionCall=SimpleNamespace(from_id=from_id),
        exception=modal_errors,
    ))
    client = make_comfyui_client()
    prediction = client.create_prediction('Waves roll onto a beach', seed=10, duration=5)
    assert received == [('video', 'Waves roll onto a beach', {'seed': 10, 'duration': 5})]
    assert prediction['status'] == 'starting'
    assert client.get_prediction(prediction['id'])['status'] == 'processing'
    resumed = make_comfyui_client()
    complete = resumed.get_prediction(prediction['id'])
    assert complete['status'] == 'succeeded'
    assert resumed.read_output(complete) == b'media'
    assert lookups == ['fc-test', 'fc-test']


@pytest.mark.parametrize("error, message", [
    (modal_errors.OutputExpiredError, "no longer has"),
    (modal_errors.FunctionTimeoutError, "reached its time limit"),
])
def test_modal_timeouts_that_end_a_job_report_failure(monkeypatch, error, message):
    monkeypatch.setenv("SILVER_SCREEN_MODAL_APP", "test-renderer")
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")

    def get(**kwargs):
        raise error("fixture")

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        FunctionCall=SimpleNamespace(from_id=lambda call_id: SimpleNamespace(get=get)),
        exception=modal_errors,
    ))
    result = make_comfyui_client().get_prediction("fc-test")
    assert result["status"] == "failed"
    assert message in result["error"]
    with pytest.raises(ScreenplayError, match=message):
        check_screenplay({"id": "fc-test"})


def test_modal_writer_waits_for_a_pending_script(monkeypatch):
    def get(**kwargs):
        raise modal_errors.TimeoutError

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        FunctionCall=SimpleNamespace(from_id=lambda call_id: SimpleNamespace(get=get)),
        exception=modal_errors,
    ))
    assert check_screenplay({"id": "fc-test"}) is None
