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

# Install the local cinematic continuity extension after the core modules have
# finished importing. It upgrades provider prompts and final assembly without
# changing the existing public pipeline API.
from .cinematic_continuity import install_cinematic_continuity

install_cinematic_continuity()

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
