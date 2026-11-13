"""Submit ComfyUI jobs over Modal's authenticated function API."""

from __future__ import annotations

import os
from typing import Any

from .ai_video import PredictionPending, VideoGenerationError
from .comfyui import MAX_OUTPUT, ComfyUIClient


def _configured(app_setting: str) -> bool:
    return all(os.getenv(key, '').strip() for key in (
        app_setting, 'MODAL_TOKEN_ID', 'MODAL_TOKEN_SECRET',
    ))


def modal_configured() -> bool:
    return _configured('SILVER_SCREEN_MODAL_APP')


def screenplay_writer_configured() -> bool:
    return _configured('SILVER_SCREEN_SCREENPLAY_APP')


def _modal_client(app_setting: str = 'SILVER_SCREEN_MODAL_APP'):
    if not _configured(app_setting):
        raise VideoGenerationError('Configure the Modal app and deployment credentials first.')
    try:
        import modal
    except ImportError as exc:
        raise VideoGenerationError('Install the Modal SDK to enable this renderer.') from exc
    return modal


def modal_function(name: str, *, app_setting: str):
    modal = _modal_client(app_setting)
    try:
        return modal, modal.Function.from_name(os.environ[app_setting].strip(), name)
    except modal.exception.Error as exc:
        raise VideoGenerationError(f'Modal could not load {name} ({type(exc).__name__}).') from exc


class ModalComfyUIClient(ComfyUIClient):
    timeout_seconds = 1600

    def __init__(self, *, kind: str = 'video') -> None:
        self.modal = _modal_client()
        self.kind = kind
        self.supports_reference = kind == 'video'
        self.app = os.environ['SILVER_SCREEN_MODAL_APP'].strip()

    def create_prediction(self, prompt: str, **settings: Any) -> dict[str, Any]:
        if not prompt.strip():
            raise VideoGenerationError('Enter a prompt before generating.')
        try:
            call = self.modal.Function.from_name(self.app, 'render').spawn(self.kind, prompt, settings)
        except self.modal.exception.Error as exc:
            raise VideoGenerationError(f'Modal could not submit the render ({type(exc).__name__}).') from exc
        return {'id': call.object_id, 'status': 'starting', 'model': self.model}

    def get_prediction(self, prediction_id: str, prediction_url: str | None = None) -> dict[str, Any]:
        result = {'id': prediction_id, 'status': 'processing', 'model': self.model}
        try:
            data = self.modal.FunctionCall.from_id(prediction_id).get(timeout=0)
        except TimeoutError:
            return result
        except self.modal.exception.Error as exc:
            if isinstance(exc, (
                self.modal.exception.ConnectionError, self.modal.exception.ServiceError, self.modal.exception.AuthError,
            )):
                raise PredictionPending(f'Modal could not check the existing render ({type(exc).__name__}).') from exc
            return dict(result, status='failed', error=f'Modal render failed ({type(exc).__name__}).')
        except Exception as exc:
            return dict(result, status='failed', error=f'GPU render failed ({type(exc).__name__}).')
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_OUTPUT:
            return dict(result, status='failed', error='Modal returned an invalid media file.')
        self._output = (prediction_id, data)
        return dict(result, status='succeeded', output=prediction_id)

    def read_output(self, prediction: dict[str, Any]) -> bytes:
        prediction_id = prediction['id']
        if getattr(self, '_output', (None,))[0] != prediction_id:
            result = self.get_prediction(prediction_id)
            if result['status'] != 'succeeded':
                raise VideoGenerationError(result.get('error') or 'The Modal result is still pending.')
        return self._output[1]
