"""Silver-Screen — Reparodynamics · TGRM full-length AI movie studio."""

__version__ = "1.0.0"

from . import science, tgrm, script_engine, media, pipeline

__all__ = [
    "science",
    "tgrm",
    "script_engine",
    "media",
    "pipeline",
    "SCIENCE",
    "FIVE_LAWS",
    "FORMATS",
    "run_tgrm",
]

# Convenience re-exports
from .science import SCIENCE, FIVE_LAWS, FORMATS
from .tgrm import run_tgrm, detect_fractures, run_msil
