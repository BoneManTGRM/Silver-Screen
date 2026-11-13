"""Private, on-demand ComfyUI worker for Silver Screen."""

import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

import modal

ROOT = Path(__file__).resolve().parents[2]
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
    .add_local_python_source('silver_screen')
    .add_local_dir(ROOT / 'deploy/runpod/workflows', remote_path='/opt/workflows')
)
app = modal.App('silver-screen-renderer')


@app.function(image=image, gpu='L40S', max_containers=1, scaledown_window=5, timeout=1500, retries=0)
def render(kind: str, prompt: str, settings: dict) -> bytes:
    from silver_screen.comfyui import ComfyUIClient

    if kind not in {'image', 'video'}:
        raise ValueError('Unsupported render type.')
    os.environ.update(
        SILVER_SCREEN_COMFYUI_URL='http://127.0.0.1:8188',
        SILVER_SCREEN_COMFYUI_IMAGE_WORKFLOW='/opt/workflows/flux-schnell.json',
        SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW='/opt/workflows/wan-video.json',
        SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW='/opt/workflows/wan-reference.json',
        SILVER_SCREEN_COMFYUI_FPS='24',
    )
    with tempfile.TemporaryDirectory(prefix='silver-render-') as directory:
        process = subprocess.Popen([
            'python', 'main.py', '--listen', '127.0.0.1', '--port', '8188',
            '--disable-auto-launch', '--disable-all-custom-nodes', '--disable-metadata',
            '--input-directory', f'{directory}/input', '--output-directory', f'{directory}/output',
            '--temp-directory', f'{directory}/temp', '--user-directory', f'{directory}/user',
        ], cwd=COMFY)
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
            return client.read_output(client.wait(client.create_prediction(prompt, **settings)))
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
