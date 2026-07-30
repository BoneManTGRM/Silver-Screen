from __future__ import annotations

import silver_screen.voice_studio as voice_studio
from silver_screen.voice_config import _ffmpeg_path

# Keep the private test helper available from the orchestration module without
# changing the public Voice Studio API.
voice_studio._ffmpeg_path = _ffmpeg_path
