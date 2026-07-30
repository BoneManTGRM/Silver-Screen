"""Media helpers stub (images / voices → frames / reels). Future extension."""

from __future__ import annotations
from typing import Any, List

def process_uploads(images: List[Any] = None, voices: List[Any] = None) -> dict:
    return {
        "images": len(images or []),
        "voices": len(voices or []),
        "status": "accepted (demo stub)",
    }
