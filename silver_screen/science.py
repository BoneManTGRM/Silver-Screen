"""Reparodynamics science constants and types for Silver-Screen.
Provenance: Cody Ryan Jenkins / BoneManTGRM
- TGRM: Targeted Gradient Repair Mechanism
- RYE: Repair Yield per Energy
- MSIL: Meta Stability Intelligence Layer
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Literal, Optional

SCIENCE = {
    "studio": "Reparodynamics",
    "founder": "Cody Ryan Jenkins",
    "pipeline": "TGRM",
    "pipelineFull": "Targeted Gradient Repair Mechanism",
    "rye": "Repair Yield per Energy",
    "msil": "Meta Stability Intelligence Layer",
    "ara": "Autonomous Research Agent",
    "tau": 0.6,
    "maxCycles": 5,
    "ryeAccept": 0.12,
    "domainGate": 0.65,
    "dois": {
        "fiveLaw": "10.5281/zenodo.17538091",
        "tgrm": "10.5281/zenodo.17273433",
        "codingVelocity": "10.5281/zenodo.17336075",
    },
    "github": "https://github.com/BoneManTGRM/Silver-Screen",
    "corpus": "https://bonemantgrm.github.io/reparodynamics-corpus/",
    "x": "https://x.com/Reparodynamics",
    "credit": "A Reparodynamics Production · TGRM · RYE · MSIL",
    "tagline": "Self-repairing narrative systems — detect fractures, apply minimal corrections, verify continuity, reinforce scar memory.",
    "loop": ["DETECT", "MINIMAL_CORRECTION", "VERIFY", "REINFORCE"],
}

FIVE_LAWS = [
    {
        "id": "bounded_energy",
        "name": "Energy-Bounded Repair",
        "cinema": "Never over-write a scene when a micro-fix restores continuity.",
    },
    {
        "id": "minimal_gradient",
        "name": "Minimal Gradient",
        "cinema": "Change the smallest narrative unit (one beat, one line, one motive).",
    },
    {
        "id": "verified_delta",
        "name": "Verified ΔR",
        "cinema": "Only keep edits that improve measurable continuity / tension / clarity.",
    },
    {
        "id": "scar_memory",
        "name": "Scar Memory",
        "cinema": "Reinforce winning fixes so later acts inherit repaired character logic.",
    },
    {
        "id": "aligned_stability",
        "name": "Aligned Stability",
        "cinema": "MSIL rejects repairs that raise RYE locally but collapse global act structure.",
    },
]

# Aligned with TypeScript FORMATS in src/lib/types.ts
FORMATS: Dict[str, Dict[str, Any]] = {
    "trailer": {
        "label": "Trailer",
        "minutes": 2,
        "scenes": 5,
        "acts": 1,
        "chapters": 1,
        "hint": "2-min sizzle",
    },
    "short": {
        "label": "Short",
        "minutes": 12,
        "scenes": 8,
        "acts": 2,
        "chapters": 2,
        "hint": "12-min short",
    },
    "episode": {
        "label": "Episode",
        "minutes": 24,
        "scenes": 12,
        "acts": 3,
        "chapters": 3,
        "hint": "24-min episode",
    },
    "featurette": {
        "label": "Featurette",
        "minutes": 45,
        "scenes": 16,
        "acts": 3,
        "chapters": 4,
        "hint": "45-min mid-form",
    },
    "feature": {
        "label": "Feature",
        "minutes": 90,
        "scenes": 24,
        "acts": 3,
        "chapters": 8,
        "hint": "90-min full film",
    },
}

GENRES = ["noir", "scifi", "drama", "thriller", "fantasy", "western", "romance", "horror"]
TONES = ["cinematic", "intimate", "epic", "melancholy", "tense", "hopeful"]

FractureClass = Literal[
    "plot_hole",
    "character_drift",
    "timeline_break",
    "theme_noise",
    "pacing_collapse",
    "dialogue_redundancy",
    "act_imbalance",
]


@dataclass
class Fracture:
    id: str
    class_: FractureClass
    severity: float  # 0..1 compared against τ
    location: str
    description: str
    hint: str


@dataclass
class ScarMemory:
    key: str
    fracture_class: FractureClass
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
    notes: List[str] = field(default_factory=list)
    fracture: Optional[Fracture] = None
    correction: Optional[str] = None


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
    notes: List[str] = field(default_factory=list)


def format_meta(fmt: str) -> Dict[str, Any]:
    return FORMATS.get(fmt, FORMATS["feature"])
