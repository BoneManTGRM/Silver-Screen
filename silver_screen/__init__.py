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

# Release metadata is patched before operational modules import it.
from . import science as _science

_science.APP_VERSION = "8.0.0"
_science.SCIENCE["version"] = _science.APP_VERSION

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

from . import transition_engine as _transition_engine
from .media_probe import probe_media as _probe_media

_transition_engine.probe = _probe_media

from .cinematic_continuity import install_cinematic_continuity

install_cinematic_continuity()

from .production_resilience import install_production_resilience

install_production_resilience()

from .creative_control_install import install_creative_controls

install_creative_controls()

from .shot_director_install import install_shot_director

install_shot_director()

# Install last so visual-quality repair directives are appended after the full
# creative, continuity, and approved-ledger prompt contract has been built.
from .visual_quality_install import install_visual_quality_supervisor

install_visual_quality_supervisor()

# Raise the default local overlap modestly and adapt it to measured boundary
# mismatch. This changes only local assembly and does not create provider calls.
from .gentler_transition_install import install_gentler_transition_smoothing

install_gentler_transition_smoothing()

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
