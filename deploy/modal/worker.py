"""Private, on-demand ComfyUI worker for Silver Screen."""

import base64
from contextlib import ExitStack
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

import modal

from silver_screen.ai_video import PredictionPending, VideoGenerationError
from silver_screen.comfyui import WAN_14B_FPS, WAN_14B_MAX_SECONDS, WAN_14B_VIDEO_SIZES
from silver_screen.hand_review import HAND_REVIEW_UNAVAILABLE, MODEL_DIR, HandReviewer
from silver_screen.modal_clip_archive import CLIPS_MOUNT, CLIPS_VOLUME, save_completed_clip

COMFY = Path('/opt/ComfyUI')
COMMIT = '0228a9d73cd7c2daf8f61fe13d56b12efe307171'
WAN = 'https://huggingface.co/Comfy-Org/Wan-2.2/resolve/main/split_files'
MODELS = {
    'checkpoints/flux1-schnell-fp8.safetensors': 'https://huggingface.co/Comfy-Org/Flux-1-Schnell/resolve/main/flux1-schnell-fp8.safetensors',
    **{path: f'{WAN}/{path}' for path in (
        'diffusion_models/wan2.2_ti2v_5B_fp16.safetensors',
        'text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors',
        'vae/wan2.2_vae.safetensors',
    )},
}
WAN_14B = 'https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/ee6f4a40737a995bf5818954cfce6d59443b0f04/split_files'
WAN_14B_MODELS = (
    'diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors',
    'diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors',
    'vae/wan_2.1_vae.safetensors',
)
STANDARD_TIMEOUT = 1500
WAN_14B_TIMEOUT = 2700
WAN_14B_GPUS = ['H100', 'L40S']
MOTION_CHECK_TIMEOUT = 1800
RETAKE_RESERVE = 240
MOTION_CHECK_ASSET = 'https://storage.googleapis.com/mediapipe-assets/pose.jpg'
MOTION_CHECK_SHA256 = 'c8a830ed683c0276d713dd5aeda28f415f10cd6291972084a40d0d8b934ed62b'

MEDIAPIPE_MODELS = 'https://storage.googleapis.com/mediapipe-models'
HAND_REVIEW_MODELS = {
    'hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task':
        'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1',
    'pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task':
        '5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1',
}


def _with_hand_review(base_image):
    return base_image.pip_install('mediapipe==0.10.32', 'opencv-contrib-python==5.0.0.93').run_commands(
        f'mkdir -p {MODEL_DIR}',
        *[
            f'curl -fL --retry 3 --connect-timeout 15 -o {MODEL_DIR / Path(path).name} {MEDIAPIPE_MODELS}/{path}'
            f' && echo "{digest}  {MODEL_DIR / Path(path).name}" | sha256sum -c -'
            for path, digest in HAND_REVIEW_MODELS.items()
        ],
    )

image = (
    modal.Image.debian_slim(python_version='3.12')
    .apt_install('git', 'curl', 'ffmpeg', 'libgl1', 'libglib2.0-0')
    .run_commands(
        f'git init {COMFY} && git -C {COMFY} fetch --depth=1 https://github.com/Comfy-Org/ComfyUI.git {COMMIT}',
        f'git -C {COMFY} checkout --detach FETCH_HEAD',
        'pip install torch==2.9.1 torchvision==0.24.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128',
        f'pip install -r {COMFY}/requirements.txt',
        *[f'mkdir -p {COMFY}/models/{Path(path).parent} && curl -fL --retry 3 --connect-timeout 20 -o {COMFY}/models/{path} {url}'
          for path, url in MODELS.items()],
    )
)
wan_14b_image = image.run_commands(*[
    f'curl -fL --retry 3 --connect-timeout 20 -o {COMFY}/models/{path} {WAN_14B}/{path}'
    for path in WAN_14B_MODELS
])
image = _with_hand_review(image)
wan_14b_image = _with_hand_review(wan_14b_image)
if modal.is_local():
    workflows = Path(__file__).resolve().parents[2] / 'deploy/runpod/workflows'
    image = image.add_local_python_source('silver_screen').add_local_dir(
        workflows, remote_path='/opt/workflows'
    )
    wan_14b_image = wan_14b_image.add_local_python_source('silver_screen').add_local_dir(
        workflows, remote_path='/opt/workflows'
    )
app = modal.App('silver-screen-renderer')
clips_volume = modal.Volume.from_name(CLIPS_VOLUME, create_if_missing=True)


@app.function(
    image=image, gpu='L40S', max_containers=1, scaledown_window=5, timeout=STANDARD_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def render(kind: str, prompt: str, settings: dict) -> bytes | dict:
    return _render(kind, prompt, settings)


@app.function(
    image=wan_14b_image, gpu=WAN_14B_GPUS, max_containers=1, scaledown_window=5, timeout=WAN_14B_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def render_wan14(kind: str, prompt: str, settings: dict) -> bytes | dict:
    if kind != 'video' or not settings.get('image'):
        raise VideoGenerationError('Wan 2.2 14B requires a starting photo.')
    if not 2 <= settings.get('duration', 0) <= WAN_14B_MAX_SECONDS:
        raise VideoGenerationError(f'Choose a 2- to {WAN_14B_MAX_SECONDS}-second clip for Wan 2.2 14B.')
    return _render(kind, prompt, settings, wan_14b=True)


@app.function(
    image=wan_14b_image, gpu=WAN_14B_GPUS, max_containers=1, scaledown_window=5, timeout=MOTION_CHECK_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def check_wan14_motion(seed: int) -> str:
    """Run one reproducible 14B quality check from the private Modal dashboard."""
    from hashlib import sha256
    from io import BytesIO

    from silver_screen.comfyui import WAN_14B_STEPS, WAN_MOTION_NEGATIVE_PROMPT
    from silver_screen.direct_generation import prepare_wan_reference

    if type(seed) is not int or not 0 <= seed < 2**53:
        raise ValueError('Choose a whole-number seed from 0 to 9007199254740991.')
    with urllib.request.urlopen(MOTION_CHECK_ASSET, timeout=20) as response:
        source = response.read(200_001)
    if sha256(source).hexdigest() != MOTION_CHECK_SHA256:
        raise RuntimeError('The public reference photo for the motion check changed.')
    reference, size = prepare_wan_reference(BytesIO(source), None, wan_14b=True)
    result = _render('video', (
        'A steady full-body shot of an athlete holding a yoga pose at the beach. '
        'He slowly lowers his left arm toward his waist with natural finger movement. '
        'His right arm, body and gaze remain steady. Subtle waves move behind him. '
        'Consistent daylight and a locked camera.'
    ), {
        'duration': 3, 'seed': seed, 'steps': WAN_14B_STEPS, 'video_size': size,
        'image': 'data:image/jpeg;base64,' + base64.b64encode(reference).decode(),
        'negative_prompt': WAN_MOTION_NEGATIVE_PROMPT, 'save_clip': True,
    }, wan_14b=True, timeout=MOTION_CHECK_TIMEOUT, allow_retake=False)
    if not isinstance(result, dict) or not result.get('archived'):
        raise RuntimeError('The motion check did not save the clip.')
    return f"{modal.current_function_call_id()}: {result['hand_review']['status']}"


def _review_hands(data: bytes, reference: str) -> dict:
    try:
        photo = base64.b64decode(reference.partition(',')[2], validate=True)
        with ExitStack() as resources:
            return HandReviewer(MODEL_DIR, photo, resources).review(data)
    except Exception:
        # The finished clip is still useful if the optional detector fails.
        logging.warning('Could not check hand continuity for this clip.', exc_info=True)
        return HAND_REVIEW_UNAVAILABLE.copy()


def _render_take(client, prompt: str, settings: dict, *, verify_fps: int | None) -> bytes:
    from silver_screen.direct_generation import verify_direct_clip

    prediction = client.wait(client.create_prediction(prompt, **settings))
    data = client.read_output(prediction)
    if verify_fps is not None:
        verify_direct_clip(data, expected_seconds=settings.get('duration', 8), minimum_fps=verify_fps)
    return data


def _render(
    kind: str, prompt: str, settings: dict, *, wan_14b: bool = False,
    timeout: int | None = None, allow_retake: bool = True,
) -> bytes | dict:
    from silver_screen.comfyui import ComfyUIClient

    if kind not in {'image', 'video'}:
        raise ValueError('Unsupported render type.')
    settings = dict(settings)
    save_clip = settings.pop('save_clip', False) and kind == 'video'
    timeout = timeout or (WAN_14B_TIMEOUT if wan_14b else STANDARD_TIMEOUT)
    started = time.monotonic()
    reference = settings.get('image') if save_clip else None
    hand_review = None
    reference_workflow = 'wan-reference-14b.json' if wan_14b else 'wan-reference.json'
    fps = WAN_14B_FPS if wan_14b else 24
    os.environ.update(
        SILVER_SCREEN_COMFYUI_URL='http://127.0.0.1:8188',
        SILVER_SCREEN_COMFYUI_IMAGE_WORKFLOW='/opt/workflows/flux-schnell.json',
        SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW='/opt/workflows/wan-video.json',
        SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW=f'/opt/workflows/{reference_workflow}',
        SILVER_SCREEN_COMFYUI_FPS=str(fps),
    )
    with tempfile.TemporaryDirectory(prefix='silver-render-') as directory:
        command = [
            'python', 'main.py', '--listen', '127.0.0.1', '--port', '8188',
            '--disable-auto-launch', '--disable-all-custom-nodes', '--disable-metadata',
        ]
        for name in ('input', 'output', 'temp', 'user'):
            path = Path(directory) / name
            path.mkdir()
            command.extend((f'--{name}-directory', str(path)))
        process = subprocess.Popen(command, cwd=COMFY)
        try:
            deadline = time.monotonic() + 90
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('ComfyUI did not start on the GPU worker.')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8188/system_stats', timeout=2):
                        break
                except OSError:
                    time.sleep(1)
            client = ComfyUIClient(kind=kind)
            if wan_14b:
                client.video_sizes = WAN_14B_VIDEO_SIZES
                client.timeout_seconds = min(2400, timeout - 150)
            data = _render_take(client, prompt, settings, verify_fps=fps if save_clip else None)
            if reference:
                hand_review = _review_hands(data, reference)
                elapsed = time.monotonic() - started
                remaining = timeout - elapsed - RETAKE_RESERVE
                if allow_retake and hand_review['status'] == 'possible_loss' and elapsed < remaining:
                    # Retry once with a different latent seed. Keep the first take unless the second tracks.
                    client.timeout_seconds = int(remaining)
                    hand_review['retake_attempted'] = True
                    try:
                        repair = dict(settings, seed=(int(settings.get('seed', 0)) + 1) % (2**53))
                        alternate = _render_take(client, prompt, repair, verify_fps=fps)
                        alternate_review = _review_hands(alternate, reference)
                        if alternate_review['status'] == 'tracked':
                            data, hand_review = alternate, alternate_review
                            hand_review.update(selected_seed=repair['seed'], retake_attempted=True)
                    except (VideoGenerationError, OSError, subprocess.SubprocessError):
                        logging.warning('A hand-continuity retake did not complete.', exc_info=True)
        except PredictionPending as exc:
            raise VideoGenerationError('The GPU render reached its time limit. Try a shorter clip or a smaller frame size.') from exc
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    if save_clip:
        result = {'data': data}
        if hand_review is not None:
            result['hand_review'] = hand_review
        try:
            save_completed_clip(
                clips_volume, CLIPS_MOUNT, modal.current_function_call_id() or '', data, hand_review=hand_review,
            )
        except (modal.exception.Error, OSError, RuntimeError, ValueError):
            logging.exception('Could not store a completed Silver Screen clip.')
            return dict(result, archive_failed=True)
        return dict(result, archived=True)
    return data
