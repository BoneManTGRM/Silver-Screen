from types import SimpleNamespace
import sys

from silver_screen.comfyui import make_comfyui_client


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
            raise TimeoutError
        return b'media'

    def from_id(prediction_id):
        lookups.append(prediction_id)
        return SimpleNamespace(get=get)

    monkeypatch.setitem(sys.modules, 'modal', SimpleNamespace(
        Function=SimpleNamespace(from_name=lambda *args: SimpleNamespace(spawn=spawn)),
        FunctionCall=SimpleNamespace(from_id=from_id),
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
