"""Silver-Screen: an operational Reparodynamics story-production system."""

# Register first-class comedy assets before pipeline imports normalize or build a
# film. This keeps the deterministic engine backward compatible while allowing
# comedy briefs to remain comedy instead of falling back to drama.
from . import script_engine as _script_engine

_script_engine.LOCATIONS.setdefault(
    "comedy",
    [
        "CHAOTIC RED CARPET",
        "CELEBRITY DRESSING ROOM",
        "OVERBOOKED PRESS LINE",
        "LUXURY PET LOUNGE",
        "BACKSTAGE SERVICE HALL",
        "AFTER-PARTY BALLROOM",
    ],
)
_script_engine.ROLE_SETS.setdefault(
    "comedy",
    [
        "Image-conscious celebrity",
        "Blunt loyal sidekick",
        "Overeager publicist",
        "Reporter who notices every mistake",
    ],
)
_script_engine.TITLE_CORES.setdefault(
    "comedy",
    [
        "Queen of the Spotlight",
        "Red Carpet Trouble",
        "Famous for a Minute",
        "The Accidental Headliner",
    ],
)

from .pipeline import (
    BriefValidationError,
    PipelineError,
    resume_video_run,
    run_pipeline,
    run_pipeline_from_file,
    validate_brief,
    video_run_status,
)
from .science import APP_VERSION, FIVE_LAWS, FORMATS, SCIENCE
from .script_engine import build_film_from_brief, generate_outline
from .tgrm import detect_fractures, run_msil, run_tgrm

# Some hosted environments provide imageio's FFmpeg executable but no ffprobe.
# Install a portable metadata probe before the continuity engine starts using
# source durations, dimensions, and audio-presence information.
from . import transition_engine as _transition_engine
from .media_probe import probe_media as _probe_media

_transition_engine.probe = _probe_media

# Install the local cinematic continuity extension after the core modules have
# finished importing. It upgrades provider prompts and final assembly without
# changing the existing public pipeline API.
from .cinematic_continuity import install_cinematic_continuity

install_cinematic_continuity()

# Production resilience wraps the already-installed continuity layer. It adds
# bounded Replicate 429 backoff and consent-gated transition retakes while
# preserving the same public pipeline functions.
from .production_resilience import install_production_resilience

install_production_resilience()

# Creative controls wrap the deterministic script engine and provider prompt
# path. The default is grounded prestige filmmaking, while an advanced page can
# require explicit screenplay, prompt, and budget approval before paid work.
from .creative_control_install import install_creative_controls

install_creative_controls()

# Refresh package-level exports after extension installation so callers using
# `from silver_screen import run_pipeline` receive the patched functions.
from .pipeline import (
    run_pipeline as run_pipeline,
    validate_brief as validate_brief,
)
from .script_engine import build_film_from_brief as build_film_from_brief
from .tgrm import run_tgrm as run_tgrm

__version__ = APP_VERSION

__all__ = [
    "APP_VERSION",
    "BriefValidationError",
    "FIVE_LAWS",
    "FORMATS",
    "PipelineError",
    "SCIENCE",
    "build_film_from_brief",
    "detect_fractures",
    "generate_outline",
    "resume_video_run",
    "run_msil",
    "run_pipeline",
    "run_pipeline_from_file",
    "run_tgrm",
    "validate_brief",
    "video_run_status",
]
