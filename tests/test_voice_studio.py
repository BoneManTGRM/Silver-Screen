from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
import pytest
from silver_screen.voice_providers import diagnose_voice_error
from silver_screen.voice_studio import _ffmpeg_path, build_voice_cast, build_voice_plan, merge_voice_result, normalize_voice_config, process_voice_production, prepare_voice_config

def _make_video(path: Path, seconds: float=1.2) -> Path:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        pytest.skip('FFmpeg is required')
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, '-y', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=24', '-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', str(path)]
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return path

def _make_audio(path: Path, seconds: float=0.55) -> Path:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        pytest.skip('FFmpeg is required')
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', str(seconds), '-c:a', 'pcm_s16le', str(path)]
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return path

def _state() -> dict[str, Any]:
    return {'title': 'Moonie Moo', 'seed': 42, 'characters': [{'id': 'moonie', 'name': 'Moonie Moo', 'role': 'Celebrity cow'}, {'id': 'bully', 'name': 'Bully', 'role': 'Loyal bulldog'}], 'scenes': [{'number': 1, 'summary': 'Moonie steps onto the red carpet as Bully steals the cameras.', 'shots': [{'dialogue': 'Moonie Moo: "Darling, the spotlight always finds me."'}, {'dialogue': 'Bully: "Not if I find it first."'}]}]}

def _video_result(root: Path, count: int=2, status: str='complete') -> dict[str, Any]:
    shots = []
    for index in range(count):
        path = _make_video(root / 'clips' / f'shot_{index + 1:04d}.mp4')
        shots.append({'id': f'shot_{index + 1:04d}', 'order': index + 1, 'status': 'verified', 'sourceScene': {'number': 1, 'chapter': 1}, 'segment': index + 1, 'path': str(path.relative_to(root)), 'plannedDurationSeconds': 1.2, 'verifiedDurationSeconds': 1.2})
    final = _make_video(root / ('final_ai_film.mp4' if status == 'complete' else 'partial_ai_film.mp4'), seconds=max(1.2, count * 1.2))
    return {'status': status, 'mode': 'ai-video', 'queue': {'productionId': 'video_test', 'shots': shots}, 'metrics': {'plannedShots': count, 'verifiedShots': count, 'verifiedSeconds': count * 1.2}, 'final_video_path': str(final) if status == 'complete' else None, 'partial_video_path': str(final) if status != 'complete' else None, 'warnings': []}

class FakeProvider:
    name = 'fake'

    def __init__(self, audio_source: Path, *, fail_once: bool=False) -> None:
        self.audio_source = audio_source
        self.fail_once = fail_once
        self.calls = 0

    def synthesize(self, *, destination: Path, **kwargs: Any) -> Path:
        del kwargs
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError('HTTP 503 temporarily unavailable')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.audio_source, destination)
        return destination

def _request(**overrides: Any) -> dict[str, Any]:
    request = {'enabled': True, 'provider': 'openai', 'mode': 'dialogue+narration', 'lead_voice': 'coral', 'supporting_voice': 'onyx', 'narrator_voice': 'cedar', 'max_retries_per_line': 1, 'subtitles': True, 'authorization_confirmed': False}
    request.update(overrides)
    return request

def test_cast_and_plan_are_deterministic(tmp_path: Path) -> None:
    config = normalize_voice_config(_request())
    cast = build_voice_cast(_state(), config)
    video = _video_result(tmp_path)
    first = build_voice_plan(_state(), video, config, cast)
    second = build_voice_plan(_state(), video, config, cast)
    assert first['lines'][0]['speaker'] == 'Moonie Moo'
    assert first['lines'][0]['voice'] == 'coral'
    assert first['lines'][1]['speaker'] == 'Bully'
    assert first['lines'][1]['voice'] == 'onyx'
    assert [line['signature'] for line in first['lines']] == [line['signature'] for line in second['lines']]

def test_uploaded_voice_requires_authorization(tmp_path: Path) -> None:
    sample = tmp_path / 'sample.wav'
    sample.write_bytes(b'R' * 2048)
    with pytest.raises(Exception, match='authorization'):
        prepare_voice_config(tmp_path, [{**_request(custom_voice=True), 'voice_sample': str(sample)}])

def test_voice_generation_repairs_and_resumes_without_duplicate_calls(tmp_path: Path) -> None:
    audio_source = _make_audio(tmp_path / 'source.wav')
    provider = FakeProvider(audio_source, fail_once=True)
    video = _video_result(tmp_path)
    result = process_voice_production(_state(), video, tmp_path, voice_inputs=[_request()], provider_factory=lambda config: provider)
    assert result['status'] == 'complete'
    assert result['metrics']['dubbedClips'] == 2
    assert result['metrics']['providerCalls'] == 3
    assert result['metrics']['repairs'] == 1
    assert result['plan']['scars']
    assert Path(result['final_video_path']).exists()
    assert Path(result['subtitles_path']).exists()
    calls = provider.calls
    resumed = process_voice_production(_state(), video, tmp_path, voice_inputs=None, provider_factory=lambda config: provider)
    assert resumed['status'] == 'complete'
    assert provider.calls == calls

def test_manual_tracks_preserve_audio_type_and_build_film(tmp_path: Path) -> None:
    manual_a = _make_audio(tmp_path / 'manual-a.wav')
    manual_b = _make_audio(tmp_path / 'manual-b.wav')
    video = _video_result(tmp_path)
    result = process_voice_production(_state(), video, tmp_path, voice_inputs=[_request(provider='manual', authorization_confirmed=True, manual_tracks=[str(manual_a), str(manual_b)])])
    assert result['status'] == 'complete'
    assert len(result['line_audio_paths']) == 2
    assert all((Path(path).suffix == '.wav' for path in result['line_audio_paths']))
    assert Path(result['final_video_path']).exists()

def test_merge_preserves_original_silent_film(tmp_path: Path) -> None:
    silent = _make_video(tmp_path / 'silent.mp4')
    voiced = _make_video(tmp_path / 'voiced.mp4')
    merged = merge_voice_result({'final_video_path': str(silent), 'partial_video_path': None, 'hero_path': str(silent), 'warnings': []}, {'status': 'complete', 'final_video_path': str(voiced), 'partial_video_path': None, 'warnings': []})
    assert merged['final_video_path'] == str(voiced)
    assert merged['silent_video_path'] == str(silent)

def test_voice_diagnostics_are_actionable() -> None:
    billing = diagnose_voice_error('HTTP 429 insufficient_quota billing')
    temporary = diagnose_voice_error('HTTP 503 temporarily unavailable')
    consent = diagnose_voice_error('403 consent required')
    assert billing.code in {'billing_required', 'rate_limit'}
    assert temporary.retryable is True
    assert consent.code == 'consent'
