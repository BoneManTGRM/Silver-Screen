"""Shared production vocabulary and format definitions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

APP_VERSION = "9.0.0"

SCIENCE: dict[str, Any] = {
    "studio": "Reparodynamics",
    "founder": "Cody Ryan Jenkins",
    "pipeline": "TGRM",
    "pipelineFull": "Targeted Gradient Repair Mechanism",
    "rye": "Repair Yield per Energy",
    "msil": "Meta Stability Intelligence Layer",
    "ara": "Autonomous Research Agent",
    "tau": 0.6,
    "maxCycles": 8,
    "energyBudget": 40,
    "ryeAccept": 0.01,
    "domainGate": 0.6,
    "version": APP_VERSION,
    "dois": {
        "fiveLaw": "10.5281/zenodo.17538091",
        "tgrm": "10.5281/zenodo.17273433",
        "codingVelocity": "10.5281/zenodo.17336075",
    },
    "github": "https://github.com/BoneManTGRM/Silver-Screen",
    "corpus": "https://bonemantgrm.github.io/reparodynamics-corpus/",
    "x": "https://x.com/Reparodynamics",
    "credit": "A Reparodynamics Production | TGRM | RYE | MSIL",
    "tagline": (
        "A durable, memory-backed film-production system that plans and locks "
        "creative intent, generates resumable footage, detects fractures, applies "
        "bounded corrections, verifies improvement, and preserves accepted work."
    ),
    "loop": ["DETECT", "MINIMAL_CORRECTION", "VERIFY", "REINFORCE"],
}

FIVE_LAWS: list[dict[str, str]] = [
    {
        "id": "bounded_energy",
        "name": "Energy-Bounded Repair",
        "cinema": "Do not rewrite a scene when a smaller change restores continuity.",
    },
    {
        "id": "minimal_gradient",
        "name": "Minimal Gradient",
        "cinema": "Change the smallest useful narrative unit: a beat, line, motive, or transition.",
    },
    {
        "id": "verified_delta",
        "name": "Verified Delta R",
        "cinema": "Keep a correction only when the measured narrative score improves.",
    },
    {
        "id": "scar_memory",
        "name": "Scar Memory",
        "cinema": "Record successful fixes so later runs can reuse proven repair patterns.",
    },
    {
        "id": "aligned_stability",
        "name": "Aligned Stability",
        "cinema": "Reject local improvements that damage the global story structure.",
    },
]

FORMATS: dict[str, dict[str, Any]] = {
    "trailer": {
        "label": "Trailer",
        "minutes": 2,
        "scenes": 5,
        "acts": 1,
        "chapters": 1,
        "target_words": 550,
        "hint": "A compact sizzle and story promise",
    },
    "short": {
        "label": "Short",
        "minutes": 12,
        "scenes": 8,
        "acts": 2,
        "chapters": 2,
        "target_words": 1500,
        "hint": "A complete short-film blueprint",
    },
    "episode": {
        "label": "Episode",
        "minutes": 24,
        "scenes": 12,
        "acts": 3,
        "chapters": 3,
        "target_words": 2600,
        "hint": "An episodic production blueprint",
    },
    "featurette": {
        "label": "Featurette",
        "minutes": 45,
        "scenes": 16,
        "acts": 3,
        "chapters": 4,
        "target_words": 4200,
        "hint": "A mid-form production blueprint",
    },
    "feature": {
        "label": "Feature",
        "minutes": 90,
        "scenes": 24,
        "acts": 3,
        "chapters": 8,
        "target_words": 7000,
        "hint": "A feature-length scene and screenplay blueprint",
    },
}

GENRES = (
    "noir",
    "scifi",
    "drama",
    "comedy",
    "thriller",
    "fantasy",
    "western",
    "romance",
    "horror",
)
TONES = ("cinematic", "intimate", "epic", "melancholy", "tense", "hopeful")

FractureClass = Literal[
    "plot_hole",
    "character_drift",
    "timeline_break",
    "theme_noise",
    "pacing_collapse",
    "dialogue_redundancy",
    "act_imbalance",
    "placeholder_content",
    "missing_ending",
]


@dataclass(frozen=True)
class Fracture:
    id: str
    class_: FractureClass
    severity: float
    location: str
    description: str
    hint: str


@dataclass
class ScarMemory:
    key: str
    fracture_class: FractureClass | str
    fix: str
    rye: float
    uses: int = 1


@dataclass
class TgrmCycleLog:
    cycle: int
    phase: str
    verified: bool
    deltaR: float
    energy: float
    rye: float
    notes: list[str] = field(default_factory=list)
    fracture: Fracture | None = None
    correction: str | None = None


@dataclass
class RyeMetrics:
    deltaR: float
    energy: float
    rye: float
    rollingRye: float = 0.0
    medianRye: float = 0.0
    stabilityIndex: float = 0.0
    recoveryMomentum: float = 0.0
    cycles: int = 0
    microRepairs: int = 0
    fullRepairs: int = 0
    reinforcements: int = 0


@dataclass
class MsilReport:
    stabilityIndex: float
    oscillation: float = 0.0
    actBalance: float = 1.0
    continuity: float = 1.0
    themeCoherence: float = 1.0
    collapseRisk: float = 0.0
    verdict: Literal["stable", "repairing", "unstable"] = "stable"
    notes: list[str] = field(default_factory=list)


def format_meta(fmt: str) -> dict[str, Any]:
    return dict(FORMATS.get(fmt, FORMATS["feature"]))
