"""Media rendering plus optional authorized voice production."""
from __future__ import annotations
import io
import os
import tempfile
import textwrap
from pathlib import Path
from typing import Any
from .ai_video import generate_ai_video
from .voice_config import _load_json as _load_voice_json
from .voice_config import _paths as _voice_paths
from .voice_studio import merge_voice_result, process_voice_production, voice_capabilities
HAS_PIL = False
HAS_VIDEO = False
Image = ImageDraw = ImageFont = ImageOps = None
np = None
ImageClip = concatenate_videoclips = None
try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    HAS_PIL = True
except Exception:
    pass
try:
    import numpy as np
    try:
        from moviepy import ImageClip, concatenate_videoclips
    except Exception:
        from moviepy.editor import ImageClip, concatenate_videoclips
    HAS_VIDEO = True
except Exception:
    pass
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
VIDEO_MODES = {'cards', 'preview', 'preview-film', 'ai-video'}

def media_capabilities() -> dict[str, Any]:
    return {'pillow': HAS_PIL, 'localPreview': HAS_VIDEO, 'aiVideo': bool(os.getenv('REPLICATE_API_TOKEN')), 'aiProvider': 'replicate', 'aiModel': os.getenv('SILVER_SCREEN_VIDEO_MODEL', 'google/veo-3.1-fast'), 'resumableVideo': True, 'voiceStudio': voice_capabilities(), 'modes': sorted(VIDEO_MODES)}

def _font(size: int, bold: bool=False):
    candidates = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf']
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()

def _genre_color(genre: str) -> tuple[int, int, int]:
    return {'scifi': (18, 36, 72), 'noir': (24, 24, 28), 'drama': (58, 40, 48), 'thriller': (56, 20, 26), 'fantasy': (42, 30, 72), 'horror': (28, 12, 18), 'romance': (66, 38, 58), 'western': (76, 52, 30), 'comedy': (78, 52, 74)}.get((genre or 'drama').lower().replace('-', ''), (30, 30, 50))

def _read_upload(upload: Any) -> bytes:
    if isinstance(upload, (str, os.PathLike)):
        path = Path(upload)
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError('Image exceeds 20 MB')
        return path.read_bytes()
    size = getattr(upload, 'size', None)
    if isinstance(size, int) and size > MAX_UPLOAD_BYTES:
        raise ValueError('Image exceeds 20 MB')
    if not hasattr(upload, 'read'):
        raise TypeError('Unsupported image input')
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if hasattr(upload, 'seek'):
        upload.seek(0)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('Image exceeds 20 MB')
    return data

def _load_image(upload: Any):
    image = Image.open(io.BytesIO(_read_upload(upload)))
    image.load()
    return image.convert('RGB')

def _draw_wrapped(draw, text: str, xy: tuple[int, int], font, width: int, max_lines: int) -> None:
    words = (text or '').replace('\n', ' ').split()
    lines: list[str] = []
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        try:
            measured = draw.textbbox((0, 0), candidate, font=font)[2]
        except Exception:
            measured = len(candidate) * 10
        if current and measured > width:
            lines.append(current)
            current = word
        else:
            current = candidate
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines:
        lines[-1] = textwrap.shorten(lines[-1], width=max(12, len(lines[-1]) - 1), placeholder='...')
    y = xy[1]
    for line in lines:
        draw.text((xy[0], y), line, font=font, fill=(230, 230, 230))
        y += 40

def _chapter_summary(state: dict[str, Any], chapter_number: int) -> str:
    scenes = [scene for scene in state.get('scenes') or [] if isinstance(scene, dict) and int(scene.get('chapter', 1)) == chapter_number]
    return ' '.join((str(scene.get('summary') or '') for scene in scenes[:3])) or str(state.get('premise') or '')

def _make_card(state: dict[str, Any], chapter: dict[str, Any], summary: str, portrait=None):
    size = (1280, 720)
    if portrait is not None:
        resampling = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS')
        card = ImageOps.fit(portrait, size, method=resampling, centering=(0.5, 0.45))
        card = Image.alpha_composite(card.convert('RGBA'), Image.new('RGBA', size, (0, 0, 0, 150))).convert('RGB')
    else:
        card = Image.new('RGB', size, _genre_color(str(state.get('genre') or 'drama')))
    draw = ImageDraw.Draw(card)
    accent = (226, 216, 190)
    draw.rectangle((0, 0, 18, size[1]), fill=accent)
    draw.text((70, 68), str(state.get('title') or 'Silver-Screen')[:80], font=_font(56, True), fill=accent)
    draw.text((72, 160), str(chapter.get('title') or 'Chapter')[:100], font=_font(38, True), fill=(255, 255, 255))
    _draw_wrapped(draw, summary, (72, 245), _font(28), size[0] - 150, 8)
    draw.text((72, 650), 'SILVER-SCREEN | REPARODYNAMICS | TGRM', font=_font(20, True), fill=accent)
    return card

def _with_duration(clip, seconds: float):
    return clip.with_duration(seconds) if hasattr(clip, 'with_duration') else clip.set_duration(seconds)

def _write_preview(clip, path: Path) -> Path | None:
    for kwargs in ({'fps': 12, 'codec': 'libx264', 'audio': False, 'logger': None}, {'fps': 12, 'codec': 'libx264', 'audio': False}):
        try:
            clip.write_videofile(str(path), **kwargs)
            if path.exists() and path.stat().st_size > 0:
                return path
        except Exception:
            continue
    return None

def _voice_request_exists(root: Path, voice_inputs: list[Any]) -> bool:
    if any((isinstance(item, dict) and bool(item.get('enabled')) for item in voice_inputs)):
        return True
    existing = _load_voice_json(_voice_paths(root)['config'])
    return bool(existing and existing.get('enabled'))

def process_media(state: dict[str, Any] | None=None, images: list[Any] | None=None, voices: list[Any] | None=None, out_dir: str | os.PathLike[str] | None=None, max_chapters: int=4, video_mode: str='cards', *, target_runtime_seconds: int | None=None, video_max_shots: int | None=None, video_batch_size: int | None=None, video_max_retries: int | None=None, video_max_provider_calls: int | None=None, video_max_spend_usd: float | None=None, video_cost_per_second_usd: float | None=None, video_continuous: bool=False, video_resume: bool=True, video_use_continuity: bool | None=None, video_progress: Any=None) -> dict[str, Any]:
    """Create media and, when authorized, build a resumable voice layer."""
    state = state or {}
    image_inputs = list(images or [])
    voice_inputs = list(voices or [])
    mode = video_mode if video_mode in VIDEO_MODES else 'cards'
    output = Path(out_dir or tempfile.mkdtemp(prefix='silverscreen_media_')).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if mode == 'ai-video':
        result = generate_ai_video(state, output, max_shots=video_max_shots, duration=int(os.getenv('SILVER_SCREEN_VIDEO_DURATION', '8')), target_runtime_seconds=target_runtime_seconds, batch_size=video_batch_size, max_retries_per_shot=video_max_retries, max_provider_calls=video_max_provider_calls, max_spend_usd=video_max_spend_usd, cost_per_second_usd=video_cost_per_second_usd, use_continuity_frames=video_use_continuity, continuous=video_continuous, resume=video_resume, initial_image=image_inputs[0] if image_inputs else None, progress=video_progress)
        result.update({'mode': mode, 'card_paths': [], 'out_dir': str(output), 'portraits_used': 1 if image_inputs else 0, 'voices_count': len(voice_inputs), 'capabilities': media_capabilities()})
        if _voice_request_exists(output, voice_inputs):
            try:
                voice = process_voice_production(state, result, output, voice_inputs=voice_inputs)
            except Exception as exc:
                voice = {'enabled': True, 'status': 'blocked', 'metrics': {}, 'msil': {'verdict': 'attention'}, 'silent_video_path': result.get('final_video_path') or result.get('partial_video_path') or result.get('hero_path'), 'warnings': [str(exc)], 'error': str(exc), 'capabilities': voice_capabilities()}
            result = merge_voice_result(result, voice)
        else:
            result['voice'] = {'enabled': False, 'status': 'disabled', 'metrics': {}, 'msil': {'verdict': 'disabled'}, 'warnings': [], 'error': None, 'capabilities': voice_capabilities()}
        return result
    warnings: list[str] = []
    result: dict[str, Any] = {'ok': False, 'status': 'initializing', 'mode': mode, 'chapter_paths': [], 'card_paths': [], 'video_paths': [], 'hero_path': None, 'final_video_path': None, 'partial_video_path': None, 'out_dir': str(output), 'portraits_used': 0, 'voices_count': len(voice_inputs), 'warnings': warnings, 'error': None, 'capabilities': media_capabilities(), 'voice': {'enabled': False, 'status': 'not_applicable', 'metrics': {}, 'msil': {'verdict': 'not_applicable'}}}
    if voice_inputs:
        warnings.append('Voice Studio requires verified AI-video clips; voice inputs were not used in preview/card mode.')
    if not HAS_PIL:
        result.update(status='failed', error='Pillow is unavailable', note='No media was produced.')
        return result
    portraits = []
    for index, upload in enumerate(image_inputs):
        try:
            portraits.append(_load_image(upload))
        except Exception as exc:
            warnings.append(f'Portrait {index + 1} was skipped: {exc}')
    result['portraits_used'] = len(portraits)
    chapters = [item for item in state.get('chapters') or [] if isinstance(item, dict)] or [{'number': 1, 'title': 'Chapter 1'}]
    limit = max(1, min(int(max_chapters), len(chapters), 12))
    clips = []
    for index, chapter in enumerate(chapters[:limit]):
        number = int(chapter.get('number', index + 1) or index + 1)
        portrait = portraits[index % len(portraits)] if portraits else None
        card = _make_card(state, chapter, _chapter_summary(state, number), portrait)
        card_path = output / f'chapter_{number:02d}.png'
        card.save(card_path, 'PNG', optimize=True)
        result['card_paths'].append(str(card_path.resolve()))
        result['chapter_paths'].append(str(card_path.resolve()))
        if mode in {'preview', 'preview-film'}:
            if not HAS_VIDEO or ImageClip is None or np is None:
                if 'MoviePy is unavailable; static cards were retained.' not in warnings:
                    warnings.append('MoviePy is unavailable; static cards were retained.')
                continue
            clip = _with_duration(ImageClip(np.array(card)), 2.8)
            video_path = output / f'preview_{number:02d}.mp4'
            written = _write_preview(clip, video_path)
            if written:
                result['video_paths'].append(str(written.resolve()))
                result['chapter_paths'][-1] = str(written.resolve())
                clips.append(clip)
            else:
                clip.close()
                warnings.append(f'Preview {number} could not be encoded.')
    if mode == 'preview-film' and clips and (concatenate_videoclips is not None):
        hero = None
        try:
            hero = concatenate_videoclips(clips, method='compose')
            written = _write_preview(hero, output / 'preview_film.mp4')
            if written:
                result['hero_path'] = str(written.resolve())
                result['final_video_path'] = str(written.resolve())
        finally:
            if hero is not None:
                hero.close()
    for clip in clips:
        try:
            clip.close()
        except Exception:
            pass
    result['ok'] = bool(result['card_paths'])
    result['status'] = 'complete' if result['ok'] else 'failed'
    result['note'] = f"Generated {len(result['card_paths'])} static card(s) and {len(result['video_paths'])} local preview clip(s). These previews are not AI-generated footage."
    return result

def process_uploads(images=None, voices=None, film=None, out_dir=None):
    return process_media(film or {}, images=images, voices=voices, out_dir=out_dir, video_mode='cards')
