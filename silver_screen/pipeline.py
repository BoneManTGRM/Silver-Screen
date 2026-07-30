"""End-to-end pipeline: brief → multi-act film → TGRM → media reels."""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from .script_engine import build_film_from_brief
from .tgrm import run_tgrm
from .media import process_media


def _normalize_genre(genre: str) -> str:
    g = (genre or "drama").lower().strip().replace("-", "").replace(" ", "")
    aliases = {
        "scifi": "scifi",
        "sciencefiction": "scifi",
        "sci": "scifi",
        "noir": "noir",
        "drama": "drama",
        "thriller": "thriller",
        "fantasy": "fantasy",
        "horror": "horror",
        "western": "western",
        "romance": "romance",
    }
    return aliases.get(g, "drama")


def _normalize_tone(tone: str) -> str:
    t = (tone or "cinematic").lower().strip()
    allowed = {"cinematic", "intimate", "epic", "melancholy", "tense", "hopeful"}
    if t == "poetic":
        return "cinematic"
    return t if t in allowed else "cinematic"


def run_pipeline(
    brief: Dict[str, Any],
    images: Optional[List[Any]] = None,
    voices: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Full path used by Streamlit.

    1. build_film_from_brief — multi-act screenplay
    2. run_tgrm — Detect → Minimal → Verify → Reinforce
    3. process_media — chapter/hero reels (optional; never raises)
    """
    premise = (brief.get("premise") or "").strip()
    genre = _normalize_genre(brief.get("genre", "scifi"))
    tone = _normalize_tone(brief.get("tone", "cinematic"))
    fmt = brief.get("format") or brief.get("fmt") or "short"
    title = (brief.get("title") or "").strip() or None

    film = build_film_from_brief(
        premise=premise,
        genre=genre,
        tone=tone,
        title=title,
        fmt=fmt,
        scars=brief.get("scars") or [],
    )

    tgrm_result = run_tgrm(film)
    repaired_state = tgrm_result.get("state") or film

    try:
        media = process_media(repaired_state, images=images, voices=voices)
    except Exception as e:
        media = {
            "ok": False,
            "note": f"Media step skipped: {e}",
            "chapter_paths": [],
            "hero_path": None,
            "error": str(e),
            "status": "error",
        }

    return {
        "film": film,
        "tgrm": tgrm_result,
        "state": repaired_state,
        "media": media,
        "metrics": tgrm_result.get("metrics", {}),
        "msil": tgrm_result.get("msil", {}),
        "log": tgrm_result.get("log", []),
        "scars": tgrm_result.get("scars", []),
    }
