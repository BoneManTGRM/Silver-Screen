"""Voice configuration, casting, planning, and durable state."""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable
from .runtime import atomic_write_json, slugify, utc_now
from .voice_providers import OPENAI_BUILTIN_VOICES, provider_capabilities
VOICE_SCHEMA_VERSION = 1
VOICE_CONFIG_FILENAME = 'voice_config.json'
VOICE_CAST_FILENAME = 'voice_cast.json'
VOICE_PLAN_FILENAME = 'voice_plan.json'
VOICE_RUNTIME_FILENAME = 'voice_runtime.json'
VOICE_SCARS_FILENAME = 'voice_scar_memory.json'
SUBTITLES_FILENAME = 'subtitles.srt'
VOICE_PROVIDERS = {'openai', 'elevenlabs', 'manual'}
VOICE_MODES = {'dialogue+narration', 'dialogue', 'narration'}
AUDIO_SUFFIXES = {'.mp3', '.wav', '.wave', '.m4a', '.aac', '.ogg', '.flac', '.webm', '.mp4'}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ProviderFactory = Callable[[dict[str, Any]], Any]

class VoiceStudioError(RuntimeError):
    """Raised when the requested voice layer cannot be produced safely."""

def _ffmpeg_path() -> str | None:
    path = shutil.which('ffmpeg')
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

def _ffprobe_path() -> str | None:
    path = shutil.which('ffprobe')
    if path:
        return path
    ffmpeg = _ffmpeg_path()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name('ffprobe')
        if sibling.exists():
            return str(sibling)
    return None

def voice_capabilities() -> dict[str, Any]:
    capabilities = provider_capabilities()
    capabilities.update({'ffmpeg': bool(_ffmpeg_path()), 'ffprobe': bool(_ffprobe_path()), 'audioMixing': bool(_ffmpeg_path()), 'subtitles': True, 'manualTracks': True, 'supportedModes': sorted(VOICE_MODES)})
    return capabilities

def _paths(root: Path) -> dict[str, Path]:
    audio = root / 'audio'
    return {'root': audio, 'config': audio / VOICE_CONFIG_FILENAME, 'cast': audio / VOICE_CAST_FILENAME, 'plan': audio / VOICE_PLAN_FILENAME, 'runtime': audio / VOICE_RUNTIME_FILENAME, 'scars': audio / VOICE_SCARS_FILENAME, 'subtitles': audio / SUBTITLES_FILENAME, 'inputs': audio / 'inputs', 'lines': audio / 'lines', 'dubbed': audio / 'dubbed_clips'}

def _safe_path(root: Path, value: str | os.PathLike[str]) -> Path:
    root = root.resolve()
    candidate = Path(value)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if path != root and root not in path.parents:
        raise VoiceStudioError('Voice artifact escaped the production workspace')
    return path

def _relative(root: Path, path: Path) -> str:
    root = root.resolve()
    resolved = path.resolve()
    if root not in resolved.parents:
        raise VoiceStudioError('Voice artifact escaped the production workspace')
    return resolved.relative_to(root).as_posix()

def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None

def _read_upload(upload: Any) -> bytes:
    if upload is None:
        raise VoiceStudioError('Required voice upload is missing')
    if isinstance(upload, (str, os.PathLike)):
        path = Path(upload)
        if not path.is_file():
            raise VoiceStudioError(f'Voice upload does not exist: {path}')
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise VoiceStudioError(f'Voice upload exceeds 20 MB: {path.name}')
        return path.read_bytes()
    size = getattr(upload, 'size', None)
    if isinstance(size, int) and size > MAX_UPLOAD_BYTES:
        raise VoiceStudioError('Voice upload exceeds 20 MB')
    if not hasattr(upload, 'read'):
        raise VoiceStudioError('Unsupported voice upload')
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if hasattr(upload, 'seek'):
        upload.seek(0)
    if len(data) > MAX_UPLOAD_BYTES:
        raise VoiceStudioError('Voice upload exceeds 20 MB')
    return data

def _upload_filename(upload: Any, fallback: str) -> str:
    raw = str(getattr(upload, 'name', '') or fallback)
    suffix = Path(raw).suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        suffix = Path(fallback).suffix or '.wav'
    return f'{slugify(Path(raw).stem, fallback=Path(fallback).stem)}{suffix}'

def _persist_upload(upload: Any, destination: Path) -> Path:
    data = _read_upload(upload)
    if len(data) < 256:
        raise VoiceStudioError(f'Voice upload is empty or too small: {destination.name}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', delete=False, dir=destination.parent, prefix=f'.{destination.name}.') as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, destination)
    return destination

def _extract_request(voice_inputs: list[Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    for item in voice_inputs or []:
        if isinstance(item, dict) and ('enabled' in item or 'provider' in item or 'mode' in item):
            request = dict(item)
            uploads = {'voice_sample': request.pop('voice_sample', None), 'consent_recording': request.pop('consent_recording', None), 'manual_tracks': list(request.pop('manual_tracks', None) or [])}
            return (request, uploads)
    return (None, {})

def _serializable_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key not in {'voice_sample', 'consent_recording', 'manual_tracks', 'uploads'}}

def normalize_voice_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    provider = str(source.get('provider') or 'openai').strip().lower()
    if provider not in VOICE_PROVIDERS:
        provider = 'openai'
    mode = str(source.get('mode') or 'dialogue+narration').strip().lower()
    if mode not in VOICE_MODES:
        mode = 'dialogue+narration'
    voice_map = source.get('voice_map')
    if not isinstance(voice_map, dict):
        voice_map = {}
    config = {'schemaVersion': VOICE_SCHEMA_VERSION, 'enabled': bool(source.get('enabled', False)), 'provider': provider, 'mode': mode, 'model': str(source.get('model') or '').strip(), 'lead_voice': str(source.get('lead_voice') or 'coral').strip(), 'supporting_voice': str(source.get('supporting_voice') or 'onyx').strip(), 'narrator_voice': str(source.get('narrator_voice') or 'cedar').strip(), 'voice_map': {str(key): str(value) for key, value in voice_map.items()}, 'instructions': str(source.get('instructions') or 'Deliver an expressive cinematic performance with clear diction.')[:2000], 'speed': max(0.7, min(1.3, float(source.get('speed', 1.0) or 1.0))), 'max_retries_per_line': max(0, min(5, int(source.get('max_retries_per_line', 1) or 0))), 'preserve_source_audio': bool(source.get('preserve_source_audio', False)), 'subtitles': bool(source.get('subtitles', True)), 'authorization_confirmed': bool(source.get('authorization_confirmed', False)), 'custom_voice': bool(source.get('custom_voice', False)), 'custom_voice_name': str(source.get('custom_voice_name') or 'Silver Screen Voice')[:120], 'custom_voice_id': str(source.get('custom_voice_id') or '').strip(), 'consent_id': str(source.get('consent_id') or '').strip(), 'language': str(source.get('language') or 'en-US')[:35], 'voice_sample_path': str(source.get('voice_sample_path') or ''), 'consent_recording_path': str(source.get('consent_recording_path') or ''), 'manual_track_paths': [str(item) for item in source.get('manual_track_paths') or [] if item], 'allow_original_fallback': bool(source.get('allow_original_fallback', True)), 'line_delay_seconds': max(0.0, min(2.0, float(source.get('line_delay_seconds', 0.2) or 0.0)))}
    if provider == 'openai':
        for key, fallback in (('lead_voice', 'coral'), ('supporting_voice', 'onyx'), ('narrator_voice', 'cedar')):
            value = str(config[key])
            if value not in OPENAI_BUILTIN_VOICES and (not value.startswith('voice_')):
                config[key] = fallback
    return config

def prepare_voice_config(root: Path, voice_inputs: list[Any] | None) -> dict[str, Any]:
    paths = _paths(root)
    existing = _load_json(paths['config'])
    request, uploads = _extract_request(voice_inputs)
    if request is None:
        return normalize_voice_config(existing)
    config = normalize_voice_config(request)
    sample = uploads.get('voice_sample')
    consent = uploads.get('consent_recording')
    tracks = uploads.get('manual_tracks') or []
    if (sample is not None or consent is not None or tracks) and (not config['authorization_confirmed']):
        raise VoiceStudioError('Uploaded or custom voice material requires explicit authorization confirmation')
    if sample is not None:
        filename = _upload_filename(sample, 'voice-sample.wav')
        path = _persist_upload(sample, paths['inputs'] / f'sample-{filename}')
        config['voice_sample_path'] = _relative(root, path)
    if consent is not None:
        filename = _upload_filename(consent, 'voice-consent.wav')
        path = _persist_upload(consent, paths['inputs'] / f'consent-{filename}')
        config['consent_recording_path'] = _relative(root, path)
    saved_tracks: list[str] = []
    for index, upload in enumerate(tracks, start=1):
        filename = _upload_filename(upload, f'track-{index:04d}.wav')
        path = _persist_upload(upload, paths['inputs'] / 'manual' / f'{index:04d}-{filename}')
        saved_tracks.append(_relative(root, path))
    if saved_tracks:
        config['manual_track_paths'] = saved_tracks
    if existing:
        for key in ('custom_voice_id', 'consent_id', 'voice_sample_path', 'consent_recording_path', 'manual_track_paths'):
            if not config.get(key) and existing.get(key):
                config[key] = existing[key]
    paths['root'].mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths['config'], _serializable_config(config))
    return config

def validate_voice_config(config: dict[str, Any]) -> None:
    if not config.get('enabled'):
        return
    capabilities = provider_capabilities()
    provider = str(config.get('provider') or '')
    if provider in {'openai', 'elevenlabs'} and (not capabilities.get(provider)):
        key = 'OPENAI_API_KEY' if provider == 'openai' else 'ELEVENLABS_API_KEY'
        raise VoiceStudioError(f'{key} is not configured for voice generation')
    if provider == 'elevenlabs':
        missing = [key for key in ('lead_voice', 'supporting_voice', 'narrator_voice') if not str(config.get(key) or '').strip()]
        if missing:
            raise VoiceStudioError('ElevenLabs requires voice IDs for: ' + ', '.join(missing))
    if provider == 'manual' and (not config.get('manual_track_paths')):
        raise VoiceStudioError('Manual voice mode requires uploaded audio tracks')
    if config.get('custom_voice'):
        if provider != 'openai':
            raise VoiceStudioError('In-app custom voice enrollment currently uses OpenAI consent')
        if not config.get('authorization_confirmed'):
            raise VoiceStudioError('Custom voice enrollment requires authorization confirmation')
        if not config.get('custom_voice_id') and (not config.get('voice_sample_path') or not config.get('consent_recording_path')):
            raise VoiceStudioError('Custom voice enrollment requires both a voice sample and consent recording')
    if not _ffmpeg_path():
        raise VoiceStudioError('FFmpeg is required to mix voices into generated video')

def build_voice_cast(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    mapping = dict(config.get('voice_map') or {})
    characters = [character for character in state.get('characters') or [] if isinstance(character, dict)]
    records: list[dict[str, Any]] = []
    for index, character in enumerate(characters):
        name = str(character.get('name') or f'Character {index + 1}')
        character_id = str(character.get('id') or name)
        voice = mapping.get(character_id) or mapping.get(name) or (config.get('lead_voice') if index == 0 else config.get('supporting_voice'))
        records.append({'characterId': character_id, 'character': name, 'role': character.get('role'), 'provider': config.get('provider'), 'voice': voice})
    return {'schemaVersion': VOICE_SCHEMA_VERSION, 'provider': config.get('provider'), 'model': config.get('model'), 'characters': records, 'narrator': {'characterId': 'narrator', 'character': 'Narrator', 'provider': config.get('provider'), 'voice': config.get('narrator_voice')}}

def _scene_map(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(scene.get('number', 0) or 0): scene for scene in state.get('scenes') or [] if isinstance(scene, dict)}

def _dialogue_lines(scene: dict[str, Any]) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for shot in scene.get('shots') or []:
        if not isinstance(shot, dict):
            continue
        raw = str(shot.get('dialogue') or '').strip()
        if not raw:
            continue
        match = re.match('^\s*([^:]{1,100}):\s*["“]?(.*?)["”]?\s*$', raw)
        if match:
            speaker = match.group(1).strip()
            text = match.group(2).strip().strip('"“”')
        else:
            speaker = 'Narrator'
            text = raw
        if text:
            lines.append((speaker, text))
    return lines

def _compact_text(text: str, duration: float, speed: float) -> str:
    cleaned = ' '.join(str(text or '').replace('\n', ' ').split()).strip(' "“”')
    if not cleaned:
        return ''
    target_words = max(5, int(max(1.0, duration - 0.4) * 2.35 * speed))
    words = cleaned.split()
    if len(words) <= target_words:
        return cleaned
    candidate = ' '.join(words[:target_words]).rstrip(',;:')
    if candidate[-1:] not in '.!?':
        candidate += '.'
    return candidate

def _voice_for_speaker(cast: dict[str, Any], speaker: str) -> str:
    for item in cast.get('characters') or []:
        if isinstance(item, dict) and str(item.get('character') or '').casefold() == speaker.casefold():
            return str(item.get('voice') or '')
    return str((cast.get('narrator') or {}).get('voice') or '')

def build_voice_plan(state: dict[str, Any], video_result: dict[str, Any], config: dict[str, Any], cast: dict[str, Any], existing: dict[str, Any] | None=None) -> dict[str, Any]:
    queue = video_result.get('queue') or {}
    scenes = _scene_map(state)
    previous = {str(line.get('shotId')): line for line in (existing or {}).get('lines') or [] if isinstance(line, dict)}
    lines: list[dict[str, Any]] = []
    for shot in sorted([item for item in queue.get('shots') or [] if isinstance(item, dict)], key=lambda item: int(item.get('order', 0) or 0)):
        shot_id = str(shot.get('id') or '')
        order = int(shot.get('order', 0) or 0)
        line_key = shot_id or f'shot_{order:04d}'
        scene_number = int((shot.get('sourceScene') or {}).get('number', 0) or 0)
        scene = scenes.get(scene_number, {})
        dialogues = _dialogue_lines(scene)
        mode = str(config.get('mode') or 'dialogue+narration')
        segment = int(shot.get('segment', 1) or 1)
        speaker = ''
        text = ''
        source = ''
        if mode in {'dialogue', 'dialogue+narration'} and dialogues:
            speaker, text = dialogues[(segment - 1) % len(dialogues)]
            source = 'dialogue'
        if not text and mode in {'narration', 'dialogue+narration'}:
            speaker = 'Narrator'
            text = str(scene.get('summary') or scene.get('action') or '')
            source = 'narration'
        target_duration = float(shot.get('verifiedDurationSeconds') or shot.get('plannedDurationSeconds') or 8)
        text = _compact_text(text, target_duration, float(config.get('speed', 1.0)))
        voice = _voice_for_speaker(cast, speaker or 'Narrator')
        signature = hashlib.sha256('|'.join([text, voice, str(config.get('provider') or ''), str(config.get('model') or ''), str(config.get('instructions') or ''), str(config.get('speed') or 1.0)]).encode('utf-8')).hexdigest()
        old = previous.get(shot_id, {})
        reusable = bool(old.get('signature') == signature and old.get('status') == 'verified' and old.get('audioPath') and old.get('dubbedPath'))
        lines.append({'id': f'voice_{line_key}', 'shotId': shot_id, 'order': order, 'scene': scene_number, 'speaker': speaker, 'source': source, 'text': text, 'voice': voice, 'provider': config.get('provider'), 'model': config.get('model'), 'signature': signature, 'videoStatus': str(shot.get('status') or 'pending'), 'targetDurationSeconds': target_duration, 'status': 'verified' if reusable else 'pending' if text else 'skipped', 'attempts': int(old.get('attempts', 0) or 0) if reusable else 0, 'audioPath': old.get('audioPath') if reusable else None, 'dubbedPath': old.get('dubbedPath') if reusable else None, 'audioDurationSeconds': old.get('audioDurationSeconds') if reusable else None, 'lastError': old.get('lastError') if reusable else None, 'repairs': list(old.get('repairs') or []) if reusable else [], 'createdAt': old.get('createdAt') or utc_now(), 'completedAt': old.get('completedAt') if reusable else None})
    return {'schemaVersion': VOICE_SCHEMA_VERSION, 'productionId': f"{queue.get('productionId') or 'video'}_voice", 'createdAt': (existing or {}).get('createdAt') or utc_now(), 'updatedAt': utc_now(), 'status': (existing or {}).get('status') or 'planned', 'config': _serializable_config(config), 'cast': cast, 'lines': lines, 'events': list((existing or {}).get('events') or []), 'scars': list((existing or {}).get('scars') or []), 'metrics': {}, 'msil': {}, 'artifacts': dict((existing or {}).get('artifacts') or {}), 'verifiedVideoShots': sum((1 for shot in queue.get('shots') or [] if isinstance(shot, dict) and shot.get('status') == 'verified'))}

def _record_event(plan: dict[str, Any], event: str, *, line_id: str | None=None, detail: str | None=None, data: dict[str, Any] | None=None) -> None:
    events = plan.setdefault('events', [])
    events.append({'at': utc_now(), 'event': event, 'lineId': line_id, 'detail': detail, 'data': data or {}})
    if len(events) > 2000:
        del events[:-2000]

def _update_metrics(plan: dict[str, Any]) -> None:
    lines = [line for line in plan.get('lines') or [] if isinstance(line, dict)]
    eligible = [line for line in lines if line.get('text') and line.get('videoStatus') == 'verified']
    completed = [line for line in eligible if line.get('status') == 'verified']
    blocked = [line for line in eligible if line.get('status') == 'blocked']
    verified_video = int(plan.get('verifiedVideoShots', 0) or 0)
    calls = sum((int(line.get('attempts', 0) or 0) for line in eligible))
    repairs = sum((len(line.get('repairs') or []) for line in eligible))
    seconds = round(sum((float(line.get('audioDurationSeconds', 0) or 0) for line in completed)), 3)
    completion = len(completed) / len(eligible) if eligible else 1.0
    coverage = min(1.0, len(completed) / max(1, verified_video))
    failure_rate = len(blocked) / len(eligible) if eligible else 0.0
    stability = max(0.0, min(1.0, completion * 0.6 + coverage * 0.2 + (1.0 - failure_rate) * 0.2))
    if blocked:
        verdict = 'attention'
    elif completion >= 1.0:
        verdict = 'stable'
    elif completed:
        verdict = 'checkpoint'
    else:
        verdict = 'planning'
    plan['metrics'] = {'plannedLines': len(eligible), 'generatedLines': len(completed), 'dubbedClips': len(completed), 'verifiedVideoShots': verified_video, 'providerCalls': calls, 'repairs': repairs, 'voiceSeconds': seconds, 'completionRatio': round(completion, 6), 'coverageRatio': round(coverage, 6), 'failedLines': len(blocked)}
    plan['msil'] = {'stabilityIndex': round(stability, 6), 'failureRate': round(failure_rate, 6), 'coverage': round(coverage, 6), 'verdict': verdict}

def _save(root: Path, plan: dict[str, Any], cast: dict[str, Any]) -> None:
    paths = _paths(root)
    paths['root'].mkdir(parents=True, exist_ok=True)
    plan['updatedAt'] = utc_now()
    _update_metrics(plan)
    atomic_write_json(paths['cast'], cast)
    atomic_write_json(paths['plan'], plan)
    atomic_write_json(paths['scars'], plan.get('scars') or [])
    atomic_write_json(paths['runtime'], {'schemaVersion': VOICE_SCHEMA_VERSION, 'productionId': plan.get('productionId'), 'status': plan.get('status'), 'updatedAt': plan.get('updatedAt'), 'metrics': plan.get('metrics') or {}, 'msil': plan.get('msil') or {}, 'artifacts': plan.get('artifacts') or {}, 'lastError': next((line.get('lastError') for line in plan.get('lines') or [] if isinstance(line, dict) and line.get('lastError')), None)})
