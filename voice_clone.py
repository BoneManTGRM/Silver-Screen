import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs import save
import io
from pydub import AudioSegment

load_dotenv()

class VoiceCloner:
    def __init__(self):
        api_key = os.getenv('ELEVENLABS_API_KEY')
        if not api_key:
            print('Warning: ELEVENLABS_API_KEY not set. Voice features will be disabled.')
            self.client = None
            return
        self.client = ElevenLabs(api_key=api_key)
        self.current_voice_id = None

    def clone_voice(self, reference_audio_path: str, name: str = 'Movie Narrator'):
        if not self.client:
            raise ValueError('ElevenLabs API key not configured')
        with open(reference_audio_path, 'rb') as f:
            audio_bytes = io.BytesIO(f.read())
        voice = self.client.voices.ivc.create(
            name=name,
            files=[audio_bytes],
            description='Cinematic movie trailer voice'
        )
        self.current_voice_id = voice.voice_id
        return self.current_voice_id

    def generate_speech(self, text: str, voice_id: str = None, output_path: str = 'voiceover.mp3'):
        if not self.client:
            raise ValueError('ElevenLabs API key not configured')
        if not voice_id and self.current_voice_id:
            voice_id = self.current_voice_id
        if not voice_id:
            raise ValueError('No voice_id')
        audio = self.client.generate(
            text=text,
            voice_id=voice_id,
            model='eleven_turbo_v2_5',
            output_format='mp3_44100_128'
        )
        save(audio, output_path)
        return output_path

    def combine_audio(self, voiceover_path: str, bg_music_path: str = None, output_path: str = 'final_audio.mp3'):
        voice = AudioSegment.from_mp3(voiceover_path)
        if bg_music_path:
            music = AudioSegment.from_file(bg_music_path) - 12
            music = music[:len(voice)]
            mixed = voice.overlay(music)
        else:
            mixed = voice
        mixed.export(output_path, format='mp3')
        return output_path
