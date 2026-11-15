"""Private, on-demand ComfyUI worker for Silver Screen."""

import base64
from contextlib import ExitStack, contextmanager
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request

import modal

from silver_screen.ai_video import PredictionPending, VideoGenerationError
from silver_screen.comfyui import WAN_14B_EXTENDED_MAX_SECONDS, WAN_14B_FPS, WAN_14B_MAX_SECONDS, WAN_14B_VIDEO_SIZES
from silver_screen.extended_clip import REVIEW_FIELDS, render_extended_clip
from silver_screen.face_review import COMPARATOR_FILE, DETECTOR_FILE, FACE_REVIEW_UNAVAILABLE, FaceReviewer
from silver_screen.hand_review import BODY_REVIEW_UNAVAILABLE, HAND_REVIEW_UNAVAILABLE, MODEL_DIR, HandReviewer
from silver_screen.modal_clip_archive import (
    CLIPS_MOUNT, CLIPS_VOLUME, render_progress_filename, save_completed_clip, save_completed_image, write_render_progress,
)

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
WAN_14B_TEXT_MODELS = (
    'diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors',
    'diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors',
    'vae/wan_2.1_vae.safetensors',
)
STANDARD_TIMEOUT = 1500
WAN_14B_TIMEOUT = 2700
EXTENDED_TIMEOUT = (WAN_14B_EXTENDED_MAX_SECONDS // WAN_14B_MAX_SECONDS) * (WAN_14B_TIMEOUT + 180) + 300
WAN_14B_GPUS = ['H100', 'L40S']
MOTION_CHECK_TIMEOUT = 1800
RETAKE_RESERVE = 240
MOTION_CHECK_ASSET = 'https://storage.googleapis.com/mediapipe-assets/pose.jpg'
MOTION_CHECK_SHA256 = 'c8a830ed683c0276d713dd5aeda28f415f10cd6291972084a40d0d8b934ed62b'
DUET_CHECK_ASSET = ('https://upload.wikimedia.org/wikipedia/commons/7/74/'
                    'Two_individuals_share_a_playful_and_lively_moment_outside_a_building%2C_engaging_in_a_dance_as_they_smile_and_hold_hands.jpg')
DUET_CHECK_SHA256 = 'c9d01899ce5f5c9dc03f5da1d3c481e5bdd858054a32d78fcabf435e9b20fd5f'

MEDIAPIPE_MODELS = 'https://storage.googleapis.com/mediapipe-models'
HAND_REVIEW_MODELS = {
    'hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task':
        'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1',
    'pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task':
        '5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1',
}
OPENCV_MODELS = 'https://media.githubusercontent.com/media/opencv/opencv_zoo/48b10fa505aeb51fbd127fc60a5c660040e823b7/models'
FACE_REVIEW_MODELS = {
    f'face_detection_yunet/{DETECTOR_FILE}': 'ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0',
    f'face_recognition_sface/{COMPARATOR_FILE}': '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
}


def _with_reference_review(base_image):
    return base_image.pip_install('mediapipe==0.10.32', 'opencv-contrib-python==5.0.0.93').run_commands(
        f'mkdir -p {MODEL_DIR}',
        *[
            f'curl -fL --retry 3 --connect-timeout 15 -o {MODEL_DIR / Path(path).name} {origin}/{path}'
            f' && echo "{digest}  {MODEL_DIR / Path(path).name}" | sha256sum -c -'
            for origin, models in ((MEDIAPIPE_MODELS, HAND_REVIEW_MODELS), (OPENCV_MODELS, FACE_REVIEW_MODELS))
            for path, digest in models.items()
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
wan_14b_text_image = image.run_commands(*[
    f'curl -fL --retry 3 --connect-timeout 20 -o {COMFY}/models/{path} {WAN_14B}/{path}'
    for path in WAN_14B_TEXT_MODELS
])
image_editor = image.run_commands(*[
    f'mkdir -p {COMFY}/models/{Path(path).parent} && '
    f'curl -fL --retry 3 --connect-timeout 20 -o {COMFY}/models/{path} {url}'
    for path, url in (
        ('diffusion_models/flux-2-klein-4b-fp8.safetensors',
         'https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/c30fa39/flux-2-klein-4b-fp8.safetensors'),
        ('text_encoders/qwen_3_4b.safetensors',
         'https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors'),
        ('vae/flux2-vae.safetensors',
         'https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors'),
    )
])
image = _with_reference_review(image)
wan_14b_image = _with_reference_review(wan_14b_image)
extended_image = modal.Image.debian_slim(python_version='3.12').apt_install('ffmpeg').pip_install('pillow', 'numpy<3')
if modal.is_local():
    workflows = Path(__file__).resolve().parents[2] / 'deploy/runpod/workflows'
    image = image.add_local_python_source('silver_screen').add_local_dir(
        workflows, remote_path='/opt/workflows'
    )
    wan_14b_image = wan_14b_image.add_local_python_source('silver_screen').add_local_dir(
        workflows, remote_path='/opt/workflows'
    )
    wan_14b_text_image = wan_14b_text_image.add_local_python_source('silver_screen').add_local_dir(
        workflows, remote_path='/opt/workflows'
    )
    image_editor = image_editor.add_local_python_source('silver_screen').add_local_dir(
        workflows, remote_path='/opt/workflows'
    )
    extended_image = extended_image.add_local_python_source('silver_screen')
app = modal.App('silver-screen-renderer')
clips_volume = modal.Volume.from_name(CLIPS_VOLUME, create_if_missing=True)


@app.function(
    image=image, gpu='L40S', max_containers=1, scaledown_window=12, timeout=STANDARD_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def render(kind: str, prompt: str, settings: dict) -> bytes | dict:
    return _render(kind, prompt, settings)


@app.function(
    image=image_editor, gpu='L40S', max_containers=1, scaledown_window=5, timeout=STANDARD_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def edit_image(kind: str, prompt: str, settings: dict) -> bytes | dict:
    if kind not in {'image', 'image_edit'}:
        raise VideoGenerationError('Choose an image render.')
    if kind == 'image_edit' and not settings.get('image'):
        raise VideoGenerationError('Upload the image you want to edit.')
    return _render(kind, prompt, settings, image_workflow='flux-klein-text.json')


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
    image=wan_14b_text_image, gpu=WAN_14B_GPUS, max_containers=1, scaledown_window=5, timeout=WAN_14B_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def render_wan14_text(kind: str, prompt: str, settings: dict) -> bytes | dict:
    if kind != 'video' or settings.get('image') or settings.get('end_image'):
        raise VideoGenerationError('Choose Wan 2.2 14B with a starting photo to use a reference.')
    if not 2 <= settings.get('duration', 0) <= WAN_14B_MAX_SECONDS:
        raise VideoGenerationError(f'Choose a 2- to {WAN_14B_MAX_SECONDS}-second clip for Wan 2.2 14B.')
    return _render(kind, prompt, settings, wan_14b=True)


@app.function(
    image=extended_image, cpu=2, memory=2048, timeout=EXTENDED_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def render_wan14_extended(kind: str, prompt: str, settings: dict) -> dict:
    if kind != 'video' or settings.get('end_image') and not settings.get('image'):
        raise VideoGenerationError('Choose an extended video with a starting photo to set an ending photo.')
    render_id = modal.current_function_call_id() or ''
    progress_file = CLIPS_MOUNT / render_progress_filename(render_id)

    def render_segment(direction: str, segment: dict) -> bytes | dict:
        renderer = render_wan14 if segment.get('image') else render_wan14_text
        return renderer.remote(kind, direction, segment)

    def progress(complete: int, total: int) -> None:
        try:
            write_render_progress(clips_volume, CLIPS_MOUNT, render_id, complete, total)
        except (modal.exception.Error, OSError, RuntimeError):
            logging.warning('Could not save the extended clip progress.', exc_info=True)

    try:
        result = render_extended_clip(prompt, settings, render_segment, progress)
    finally:
        try:
            if progress_file.exists():
                progress_file.unlink(missing_ok=True)
                clips_volume.commit()
        except (modal.exception.Error, OSError, RuntimeError):
            logging.warning('Could not remove the completed progress record.', exc_info=True)
    return _archive_result(result, image=False) if settings.get('save_clip') else result


@app.function(
    image=wan_14b_image, gpu=WAN_14B_GPUS, max_containers=1, scaledown_window=5, timeout=MOTION_CHECK_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def check_wan14_motion(seed: int) -> str:
    """Run one reproducible 14B quality check from the private Modal dashboard."""
    return _check_motion(seed, MOTION_CHECK_ASSET, MOTION_CHECK_SHA256, 200_000, (
        'A steady full-body shot of an athlete holding a yoga pose at the beach. '
        'He slowly lowers his left arm toward his waist with natural finger movement. '
        'His right arm, body and gaze remain steady. Subtle waves move behind him. '
        'Consistent daylight and a locked camera.'
    ))


@app.function(
    image=wan_14b_image, gpu=WAN_14B_GPUS, max_containers=1, scaledown_window=5, timeout=MOTION_CHECK_TIMEOUT,
    retries=0, volumes={str(CLIPS_MOUNT): clips_volume},
)
def check_wan14_duet(seed: int) -> str:
    """Render a three-second two-person shot from a licensed reference."""
    # Reference photo by Shixart1985, CC BY 2.0, via Wikimedia Commons.
    return _check_motion(seed, DUET_CHECK_ASSET, DUET_CHECK_SHA256, 11_000_000, (
        'One continuous full-body shot of two adults rehearsing a dance. '
        'They let go of their clasped hands, take a quick sideways step apart, then open their palms at chest level. '
        'The man in denim stays on the left and the woman in plaid stays on the right. '
        'Natural weight shifts and finger movement, a steady eye-level camera, and consistent daylight.'
    ))


def _check_motion(seed: int, image_url: str, digest: str, byte_limit: int, prompt: str) -> str:
    from hashlib import sha256
    from io import BytesIO

    from silver_screen.comfyui import WAN_14B_STEPS, WAN_MOTION_NEGATIVE_PROMPT
    from silver_screen.direct_generation import prepare_wan_reference

    if type(seed) is not int or not 0 <= seed < 2**53:
        raise ValueError('Choose a whole-number seed from 0 to 9007199254740991.')
    request = urllib.request.Request(image_url, headers={'User-Agent': 'Silver-Screen/1.0 (render validation)'})
    with urllib.request.urlopen(request, timeout=25) as response:
        source = response.read(byte_limit + 1)
    if len(source) > byte_limit or sha256(source).hexdigest() != digest:
        raise RuntimeError('The public reference photo for the motion check changed.')
    reference, size = prepare_wan_reference(BytesIO(source), None, wan_14b=True)
    result = _render('video', prompt, {
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


def _review_body_and_hands(data: bytes, reference: str) -> dict:
    try:
        photo = base64.b64decode(reference.partition(',')[2], validate=True)
        with ExitStack() as resources:
            return HandReviewer(MODEL_DIR, photo, resources).review_continuity(data, check_body=True)
    except Exception:
        logging.warning('Could not check body or hand continuity for this clip.', exc_info=True)
        return {'body_review': BODY_REVIEW_UNAVAILABLE.copy(), 'hand_review': HAND_REVIEW_UNAVAILABLE.copy()}


def _review_faces(data: bytes, reference: str) -> dict:
    try:
        photo = base64.b64decode(reference.partition(',')[2], validate=True)
        return FaceReviewer(MODEL_DIR, photo).review(data)
    except Exception:
        logging.warning('Could not check face continuity for this clip.', exc_info=True)
        return FACE_REVIEW_UNAVAILABLE.copy()


def _better_reference_take(before: dict, after: dict) -> bool:
    bad = {'hand_review': 'possible_loss', 'face_review': 'possible_drift', 'body_review': 'possible_distortion'}
    good = {'hand_review': 'tracked', 'face_review': 'matched', 'body_review': 'tracked'}
    flagged = [name for name, status in bad.items() if before.get(name, {}).get('status') == status]
    required = [name for name, status in good.items() if name in flagged or before.get(name, {}).get('status') == status]
    if not flagged or any(after.get(name, {}).get('status') != good[name] for name in required):
        return False
    return all(after.get(name, {}).get('status') != status for name, status in bad.items())


def _render_take(client, prompt: str, settings: dict, *, verify_fps: int | None) -> bytes:
    from silver_screen.direct_generation import verify_direct_clip, verify_direct_output

    prediction = client.wait(client.create_prediction(prompt, **settings))
    data = client.read_output(prediction)
    if verify_fps is not None:
        verify_direct_clip(
            data, expected_seconds=settings.get('duration', 8), minimum_fps=verify_fps,
            expected_size=settings.get('video_size'),
        )
    elif settings.get('image_size') is not None:
        verify_direct_output(data, image=True, expected_size=settings['image_size'])
    return data


def _archive_result(result: dict, *, image: bool) -> dict:
    try:
        render_id = modal.current_function_call_id() or ''
        if image:
            from silver_screen.direct_generation import verify_direct_output

            verify_direct_output(result['data'], image=True)
            save_completed_image(clips_volume, CLIPS_MOUNT, render_id, result['data'])
        else:
            metadata = {name: result[name] for name in (*REVIEW_FIELDS, 'render_stats') if name in result}
            save_completed_clip(clips_volume, CLIPS_MOUNT, render_id, result['data'], **metadata)
    except (modal.exception.Error, OSError, RuntimeError, ValueError):
        logging.exception('Could not store a completed Silver Screen render.')
        return dict(result, archive_failed=True)
    return dict(result, archived=True)


def _gpu_names() -> list[str]:
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                                capture_output=True, text=True, timeout=3, check=True)
        return sorted({line.strip()[:120] for line in result.stdout.splitlines() if line.strip()})
    except (OSError, subprocess.SubprocessError):
        return []


@contextmanager
def _start_comfy_server():
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
            yield process
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


# The standard Modal worker processes one request at a time per container.
_ad_server = None


def _close_ad_server() -> None:
    global _ad_server
    if _ad_server:
        _, _, stack = _ad_server
        _ad_server = None
        stack.close()


@contextmanager
def _comfy_server(ad_session: str | None):
    global _ad_server
    if _ad_server and (_ad_server[0] != ad_session or _ad_server[1].poll() is not None):
        _close_ad_server()
    if not ad_session:
        with _start_comfy_server():
            yield False
        return
    reused = _ad_server is not None
    if not reused:
        with ExitStack() as stack:
            process = stack.enter_context(_start_comfy_server())
            _ad_server = ad_session, process, stack.pop_all()
    try:
        yield reused
    except BaseException:
        _close_ad_server()
        raise


def _render(
    kind: str, prompt: str, settings: dict, *, wan_14b: bool = False,
    timeout: int | None = None, allow_retake: bool = True, image_workflow: str = 'flux-schnell.json',
) -> bytes | dict:
    from silver_screen.comfyui import ComfyUIClient

    if kind not in {'image', 'image_edit', 'video'}:
        raise ValueError('Unsupported render type.')
    settings = dict(settings)
    save_clip = settings.pop('save_clip', False) and kind == 'video'
    save_image = settings.pop('save_image', False) and kind in {'image', 'image_edit'}
    record_stats = settings.pop('record_stats', False) and kind == 'video'
    ad_session = settings.pop('ad_render_session', None)
    if kind != 'video' or wan_14b or not isinstance(ad_session, str) or not re.fullmatch(r'[0-9a-f]{64}', ad_session):
        ad_session = None
    check_hands = settings.pop('check_hands', False) and kind == 'video'
    check_face = settings.pop('check_face', False) and kind == 'video'
    check_body = settings.pop('check_body', False) and kind == 'video'
    face_reference = settings.pop('face_reference', None) or settings.get('image')
    allow_retake = settings.pop('allow_retake', True) and allow_retake
    timeout = timeout or (WAN_14B_TIMEOUT if wan_14b else STANDARD_TIMEOUT)
    started = time.monotonic()
    reference = face_reference if save_clip or check_hands else None
    face_reference = face_reference if check_face else None
    reviews = {}

    def review(data: bytes) -> dict:
        result = {}
        if face_reference and check_body:
            result.update(_review_body_and_hands(data, face_reference))
        elif reference:
            result['hand_review'] = _review_hands(data, reference)
        if face_reference:
            result['face_review'] = _review_faces(data, face_reference)
        return result

    reference_workflow = 'wan-reference-14b.json' if wan_14b else 'wan-reference.json'
    video_workflow = 'wan-text-14b.json' if wan_14b and not settings.get('image') else 'wan-video.json'
    fps = WAN_14B_FPS if wan_14b else 24
    os.environ.update(
        SILVER_SCREEN_COMFYUI_URL='http://127.0.0.1:8188',
        SILVER_SCREEN_COMFYUI_IMAGE_WORKFLOW=f'/opt/workflows/{image_workflow}',
        SILVER_SCREEN_COMFYUI_IMAGE_EDIT_WORKFLOW='/opt/workflows/flux-klein-edit.json',
        SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW=f'/opt/workflows/{video_workflow}',
        SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW=f'/opt/workflows/{reference_workflow}',
        SILVER_SCREEN_COMFYUI_FPS=str(fps),
    )
    with _comfy_server(ad_session) as reused_server:
        try:
            client = ComfyUIClient(kind=kind)
            if wan_14b:
                client.video_sizes = WAN_14B_VIDEO_SIZES
                client.timeout_seconds = min(2400, timeout - 150)
            verify_in_worker = save_clip or check_hands or check_face or (record_stats and not ad_session)
            data = _render_take(client, prompt, settings, verify_fps=fps if verify_in_worker else None)
            reviews = review(data)
            elapsed = time.monotonic() - started
            remaining = timeout - elapsed - RETAKE_RESERVE
            flagged = any(item['status'] in {'possible_loss', 'possible_drift', 'possible_distortion'}
                          for item in reviews.values())
            if save_clip and allow_retake and flagged and elapsed < remaining:
                client.timeout_seconds = int(remaining)
                for item in reviews.values():
                    item['retake_attempted'] = True
                try:
                    repair = dict(settings, seed=(int(settings.get('seed', 0)) + 1) % (2**53))
                    alternate = _render_take(client, prompt, repair, verify_fps=fps)
                    alternate_reviews = review(alternate)
                    if _better_reference_take(reviews, alternate_reviews):
                        data, reviews = alternate, alternate_reviews
                        for item in reviews.values():
                            item.update(selected_seed=repair['seed'], retake_attempted=True)
                except (VideoGenerationError, OSError, subprocess.SubprocessError):
                    logging.warning('A reference-continuity retake did not complete.', exc_info=True)
        except PredictionPending as exc:
            raise VideoGenerationError('The GPU render reached its time limit. Try a shorter clip or a smaller frame size.') from exc
    result = {'data': data, **reviews}
    if record_stats:
        gpu_names = _gpu_names()
        execution = round(time.monotonic() - started, 2)
        width, height = settings.get('video_size') or (832, 480)
        result['render_stats'] = {
            'model': 'Wan 2.2 14B' if wan_14b else 'Wan 2.2 5B', 'segments': 1, 'fps': fps,
            'width': width, 'height': height, 'requested_seconds': settings.get('duration', 8),
            'gpu_worker_seconds': execution, 'gpus': gpu_names, 'execution_seconds': execution,
        }
    if record_stats and ad_session:
        result['render_stats']['reused_comfy_server'] = reused_server
    if save_clip or save_image:
        return _archive_result(result, image=save_image)
    return result if reviews or record_stats else data
