"""End-to-end pipeline runner for Silver-Screen."""

from __future__ import annotations
from typing import Dict, Any
from .tgrm import run_tgrm
from .script_engine import generate_outline
from .media import process_uploads

def run_pipeline(brief: Dict[str, Any], images=None, voices=None) -> Dict[str, Any]:
    outline = generate_outline(
        brief.get("premise", ""),
        brief.get("genre", "sci-fi"),
        brief.get("format", "feature"),
    )
    state = {
        "title": brief.get("title", "Untitled"),
        "premise": brief.get("premise", ""),
        "genre": brief.get("genre", "sci-fi"),
        "tone": brief.get("tone", "cinematic"),
        "format": brief.get("format", "feature"),
        "script": outline.get("outline", ""),
        "characters": brief.get("characters", []),
        "scenes": [],
        "acts": [],
        "scars": [],
    }
    tgrm_result = run_tgrm(state)
    media = process_uploads(images, voices)
    return {
        "outline": outline,
        "tgrm": tgrm_result,
        "media": media,
    }
