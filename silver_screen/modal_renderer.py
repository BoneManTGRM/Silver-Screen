"""Submit ComfyUI jobs over Modal's authenticated function API."""

from __future__ import annotations

import os
from typing import Any

from .ai_video import PredictionPending, VideoGenerationError
from .comfyui import MAX_OUTPUT, WAN_14B_MAX_SECONDS, ComfyUIClient, image_size_ready
from .hand_review import HAND_REVIEW_UNAVAILABLE
from .modal_clip_archive import (
    CLIPS_VOLUME, MODAL_RENDER_ID, RecentRender, delete_saved_media, latest_renders, media_was_deleted,
    read_saved_hand_review, read_saved_media, remember_submitted_render,
)


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


def recent_modal_renders() -> list[RecentRender]:
    modal = _modal_client()
    try:
        return latest_renders(modal.Volume.from_name(CLIPS_VOLUME))
    except modal.exception.NotFoundError:
        return []
    except (modal.exception.Error, OSError) as exc:
        raise VideoGenerationError('Could not load recent renders from Modal. Check again shortly.') from exc


def remember_modal_render(render_id: str, seconds: int | None) -> None:
    modal = _modal_client()
    try:
        remember_submitted_render(modal.Volume.from_name(CLIPS_VOLUME), render_id, seconds)
    except (modal.exception.Error, OSError, ValueError) as exc:
        raise VideoGenerationError('Recent renders could not save this entry. Keep the render link to recover it.') from exc


def delete_modal_render(render_id: str, *, image: bool) -> None:
    modal = _modal_client()
    try:
        delete_saved_media(modal.Volume.from_name(CLIPS_VOLUME), render_id, image=image)
    except (modal.exception.Error, OSError, ValueError) as exc:
        raise VideoGenerationError('Could not confirm deletion from Silver Screen. Try deleting it again.') from exc


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
        if settings.get('end_image') and not wan_14b:
            raise VideoGenerationError('An ending image requires Wan 2.2 14B.')
        function = 'render'
        if self.kind == 'image_edit':
            if not settings.get('image'):
                raise VideoGenerationError('Upload the image you want to edit.')
            function = 'edit_image'
        elif self.kind == 'image' and image_size_ready('image'):
            function = 'edit_image'
        if wan_14b:
            if self.kind != 'video':
                raise VideoGenerationError('Choose a video render for Wan 2.2 14B.')
            if settings.get('end_image') and not settings.get('image'):
                raise VideoGenerationError('An ending image requires a starting photo.')
            if not 2 <= settings.get('duration', 0) <= WAN_14B_MAX_SECONDS:
                raise VideoGenerationError(f'Choose a 2- to {WAN_14B_MAX_SECONDS}-second clip for Wan 2.2 14B.')
            function = 'render_wan14' if settings.get('image') else 'render_wan14_text'
        try:
            call = self.modal.Function.from_name(self.app, function).spawn(self.kind, prompt, settings)
        except self.modal.exception.Error as exc:
            raise VideoGenerationError(f'Modal could not submit the render ({type(exc).__name__}).') from exc
        return {'id': call.object_id, 'status': 'starting', 'model': self.model}

    def get_prediction(
        self, prediction_id: str, prediction_url: str | None = None, *, prefer_saved: bool = False,
    ) -> dict[str, Any]:
        if prefer_saved:
            try:
                if saved := self._find_saved_prediction(prediction_id):
                    return saved
            except PredictionPending:
                pass  # An archive outage can still leave the Modal call result available.
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
        if isinstance(data, (bytes, dict)) and MODAL_RENDER_ID.fullmatch(prediction_id):
            try:
                if media_was_deleted(self.modal.Volume.from_name(CLIPS_VOLUME), prediction_id):
                    return dict(result, status='failed', error='This render was deleted from Silver Screen.')
            except self.modal.exception.NotFoundError:
                pass
            except (self.modal.exception.Error, OSError) as exc:
                raise PredictionPending('Could not verify the saved render. Check it again shortly.') from exc
        if isinstance(data, dict):
            if data.get('archive_failed') is True:
                result['warning'] = 'The render finished, but Modal could not confirm the save. Download your copy now.'
            elif data.get('archived') is True:
                result['archived'] = True
            if isinstance(data.get('hand_review'), dict):
                result['hand_review'] = data['hand_review']
            data = data.get('data')
        if isinstance(data, str) and data.startswith(f'{prediction_id}: '):
            # The dashboard quality check returns a summary. Its MP4 lives in the archive.
            return self.get_saved_prediction(prediction_id)
        return self._complete_prediction(result, data)

    def get_saved_prediction(self, prediction_id: str) -> dict[str, Any]:
        return self._find_saved_prediction(prediction_id) or {
            'id': prediction_id, 'model': self.model, 'status': 'failed',
            'error': 'Modal no longer has a result for this render ID.',
        }

    def _find_saved_prediction(self, prediction_id: str) -> dict[str, Any] | None:
        if not MODAL_RENDER_ID.fullmatch(prediction_id):
            return None
        result = {'id': prediction_id, 'model': self.model}
        try:
            volume = self.modal.Volume.from_name(CLIPS_VOLUME)
            if media_was_deleted(volume, prediction_id):
                return dict(result, status='failed', error='This render was deleted from Silver Screen.')
            data = read_saved_media(volume, prediction_id)
        except (FileNotFoundError, self.modal.exception.NotFoundError):
            return None
        except VideoGenerationError as exc:
            return dict(result, status='failed', error=str(exc))
        except (self.modal.exception.Error, OSError) as exc:
            raise PredictionPending('Could not read the saved render from Modal. Check it again.') from exc
        try:
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                if review := read_saved_hand_review(volume, prediction_id):
                    result['hand_review'] = review
        except self.modal.exception.NotFoundError:
            pass  # Clips made before hand review have no sidecar.
        except (self.modal.exception.Error, OSError):
            result['hand_review'] = HAND_REVIEW_UNAVAILABLE.copy()
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
