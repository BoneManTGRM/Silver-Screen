"""Silver-Screen: an operational Reparodynamics story-production system."""

from .pipeline import (
    BriefValidationError,
    PipelineError,
    run_pipeline,
    run_pipeline_from_file,
    validate_brief,
)
from .science import APP_VERSION, FIVE_LAWS, FORMATS, SCIENCE
from .script_engine import build_film_from_brief, generate_outline
from .tgrm import detect_fractures, run_msil, run_tgrm

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
    "run_msil",
    "run_pipeline",
    "run_pipeline_from_file",
    "run_tgrm",
    "validate_brief",
]
