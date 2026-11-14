"""Private, on-demand ComfyUI worker for Silver Screen."""

import logging
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

import modal

from silver_screen.ai_video import PredictionPending, VideoGenerationError
from silver_screen.comfyui import WAN_14B_MAX_SECONDS, WAN_14B_VIDEO_SIZES
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
    image=image, gpu='L40S', max_containers=1, scaledown_window=5, timeout=1500,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def render(kind: str, prompt: str, settings: dict) -> bytes | dict:
    return _render(kind, prompt, settings)


@app.function(
    image=wan_14b_image, gpu='L40S', max_containers=1, scaledown_window=5, timeout=2700,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def render_wan14(kind: str, prompt: str, settings: dict) -> bytes | dict:
    if kind != 'video' or not settings.get('image'):
        raise VideoGenerationError('Wan 2.2 14B requires a starting photo.')
    if not 2 <= settings.get('duration', 0) <= WAN_14B_MAX_SECONDS:
        raise VideoGenerationError(f'Choose a 2- to {WAN_14B_MAX_SECONDS}-second clip for Wan 2.2 14B.')
    return _render(kind, prompt, settings, wan_14b=True)


def _render(kind: str, prompt: str, settings: dict, *, wan_14b: bool = False) -> bytes | dict:
    from silver_screen.comfyui import ComfyUIClient
    from silver_screen.direct_generation import verify_direct_clip

    if kind not in {'image', 'video'}:
        raise ValueError('Unsupported render type.')
    settings = dict(settings)
    save_clip = settings.pop('save_clip', False) and kind == 'video'
    reference_workflow = 'wan-reference-14b.json' if wan_14b else 'wan-reference.json'
    os.environ.update(
        SILVER_SCREEN_COMFYUI_URL='http://127.0.0.1:8188',
        SILVER_SCREEN_COMFYUI_IMAGE_WORKFLOW='/opt/workflows/flux-schnell.json',
        SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW='/opt/workflows/wan-video.json',
        SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW=f'/opt/workflows/{reference_workflow}',
        SILVER_SCREEN_COMFYUI_FPS='16' if wan_14b else '24',
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
                client.timeout_seconds = 2400
            data = client.read_output(client.wait(client.create_prediction(prompt, **settings)))
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
        verify_direct_clip(data, expected_seconds=settings.get('duration', 8))
        try:
            save_completed_clip(clips_volume, CLIPS_MOUNT, modal.current_function_call_id() or '', data)
        except (modal.exception.Error, OSError, RuntimeError, ValueError):
            logging.exception('Could not store a completed Silver Screen clip.')
            return {'data': data, 'archive_failed': True}
        return {'data': data, 'archived': True}
    return data
