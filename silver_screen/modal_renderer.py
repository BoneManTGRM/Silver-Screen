"""Submit ComfyUI jobs over Modal's authenticated function API."""

from __future__ import annotations

import os
from typing import Any

from .ai_video import PredictionPending, VideoGenerationError
from .comfyui import MAX_OUTPUT, WAN_14B_MAX_SECONDS, ComfyUIClient
from .modal_clip_archive import CLIPS_VOLUME, MODAL_RENDER_ID, latest_saved_clips, read_saved_clip


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


def saved_clips() -> list[tuple[str, int]]:
    modal = _modal_client()
    try:
        return latest_saved_clips(modal.Volume.from_name(CLIPS_VOLUME))
    except modal.exception.NotFoundError:
        return []
    except (modal.exception.Error, OSError) as exc:
        raise VideoGenerationError('Could not load saved clips from Modal. Check again shortly.') from exc


class ModalComfyUIClient(ComfyUIClient):
    timeout_seconds = 1600

    def __init__(self, *, kind: str = 'video') -> None:
        self.modal = _modal_client()
        self.kind = kind
        self.supports_reference = kind == 'video'
        self.app = os.environ['SILVER_SCREEN_MODAL_APP'].strip()

    def create_prediction(self, prompt: str, *, wan_14b: bool = False, **settings: Any) -> dict[str, Any]:
        if not prompt.strip():
            raise VideoGenerationError('Enter a prompt before generating.')
        function = 'render'
        if wan_14b:
            if self.kind != 'video' or not settings.get('image'):
                raise VideoGenerationError('Wan 2.2 14B requires a starting photo.')
            if not 2 <= settings.get('duration', 0) <= WAN_14B_MAX_SECONDS:
                raise VideoGenerationError(f'Choose a 2- to {WAN_14B_MAX_SECONDS}-second clip for Wan 2.2 14B.')
            function = 'render_wan14'
        try:
            call = self.modal.Function.from_name(self.app, function).spawn(self.kind, prompt, settings)
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
            errors = self.modal.exception
            # A zero-time poll raises the base SDK timeout while work is pending.
            # FunctionTimeoutError and OutputExpiredError are terminal subclasses.
            if type(exc) is errors.TimeoutError:
                return result
            if isinstance(exc, (errors.OutputExpiredError, errors.NotFoundError)):
                return self.get_saved_prediction(prediction_id)
            if isinstance(exc, errors.FunctionTimeoutError):
                return dict(result, status='failed', error='The GPU worker reached its time limit. Try a shorter clip.')
            if isinstance(exc, errors.AuthError):
                raise PredictionPending('Railway could not authenticate to Modal. Fix the Modal credentials, then check this same render ID.') from exc
            if isinstance(exc, (
                errors.ConnectionError, errors.ServiceError,
            )):
                raise PredictionPending(f'Modal could not check the existing render ({type(exc).__name__}).') from exc
            return dict(result, status='failed', error=f'Modal render failed ({type(exc).__name__}).')
        except Exception as exc:
            detail = str(exc).casefold()
            if 'out of memory' in detail or 'allocation on device' in detail:
                return dict(result, status='failed', error='The GPU ran out of memory. Try a shorter clip or a smaller frame size.')
            if isinstance(exc, VideoGenerationError):
                return dict(result, status='failed', error=str(exc))
            if detail == 'comfyui did not start on the gpu worker.':
                return dict(result, status='failed', error='ComfyUI could not start on the Modal GPU worker.')
            return dict(result, status='failed', error=f'GPU render failed ({type(exc).__name__}).')
        if isinstance(data, dict):
            if data.get('archive_failed') is True:
                result['warning'] = 'The clip rendered, but Modal could not confirm the save. Download your copy now.'
            elif data.get('archived') is True:
                result['archived'] = True
            data = data.get('data')
        return self._complete_prediction(result, data)

    def get_saved_prediction(self, prediction_id: str) -> dict[str, Any]:
        result = {'id': prediction_id, 'model': self.model}
        unavailable = dict(result, status='failed', error='Modal no longer has a result for this render ID.')
        if not MODAL_RENDER_ID.fullmatch(prediction_id):
            return unavailable
        try:
            data = read_saved_clip(self.modal.Volume.from_name(CLIPS_VOLUME), prediction_id)
        except (FileNotFoundError, self.modal.exception.NotFoundError):
            return unavailable
        except VideoGenerationError as exc:
            return dict(result, status='failed', error=str(exc))
        except (self.modal.exception.Error, OSError) as exc:
            raise PredictionPending('Could not read the saved clip from Modal. Check this render again.') from exc
        return self._complete_prediction(dict(result, archived=True), data)

    def _complete_prediction(self, result: dict[str, Any], data: Any) -> dict[str, Any]:
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_OUTPUT:
            return dict(result, status='failed', error='Modal returned an invalid media file.')
        self._output = (result['id'], data)
        return dict(result, status='succeeded', output=result['id'])

    def read_output(self, prediction: dict[str, Any]) -> bytes:
        prediction_id = prediction['id']
        if getattr(self, '_output', (None,))[0] != prediction_id:
            result = self.get_prediction(prediction_id)
            if result['status'] != 'succeeded':
                raise VideoGenerationError(result.get('error') or 'The Modal result is still pending.')
        return self._output[1]
