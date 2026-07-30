"""TGRM narrative repair for Silver-Screen (Python port of core TS logic).

Detect → Minimal correction → Verify → Reinforce with τ=0.6, RYE, MSIL, scar memory.
"""

from __future__ import annotations
import uuid
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from .science import SCIENCE

@dataclass
class Fracture:
    id: str
    class_: str
    severity: float
    location: str
    description: str
    hint: str

@dataclass
class ScarMemory:
    key: str
    fracture_class: str
    fix: str
    rye: float
    uses: int = 1

def _uid(prefix: str = "fx") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

def detect_fractures(state: Dict[str, Any]) -> List[Fracture]:
    fractures: List[Fracture] = []
    script = state.get("script", "") or ""
    premise = state.get("premise", "") or ""
    characters = state.get("characters", []) or []
    scenes = state.get("scenes", []) or []
    acts = state.get("acts", []) or []

    for c in characters:
        name = c.get("name", "") if isinstance(c, dict) else str(c)
        if not name:
            continue
        mentions = script.lower().count(name.lower())
        if mentions < max(1, max(1, len(scenes)) // 4):
            sev = min(0.95, 0.45 + 0.3 * (1 - mentions / max(1, len(scenes) or 1)))
            fractures.append(Fracture(
                id=_uid(),
                class_="character_drift",
                severity=sev,
                location=name,
                description=f"{name} under-serviced ({mentions} mentions).",
                hint=f"reinforce_presence:{name}",
            ))

    keywords = [w for w in premise.lower().split() if len(w) > 5][:6]
    if keywords and len(script) > 80:
        late = script[len(script)//2:].lower()
        hits = sum(1 for k in keywords if k in late)
        ratio = hits / max(1, len(keywords))
        if ratio < SCIENCE["domainGate"]:
            fractures.append(Fracture(
                id=_uid(),
                class_="theme_noise",
                severity=0.62,
                location="theme",
                description=f"Late premise echo {ratio*100:.0f}% < domainGate.",
                hint="echo_premise_late",
            ))

    if len(script.strip()) < 300:
        fractures.append(Fracture(
            id=_uid(),
            class_="plot_hole",
            severity=0.88,
            location="script",
            description="Screenplay under-developed for target format.",
            hint="expand_script",
        ))

    if len(acts) >= 3:
        sizes = [a.get("scene_count", 1) if isinstance(a, dict) else 1 for a in acts]
        if max(sizes) > min(sizes) * 2.5:
            fractures.append(Fracture(
                id=_uid(),
                class_="act_imbalance",
                severity=0.72,
                location="structure",
                description=f"Act sizes {sizes} unbalanced.",
                hint="rebalance_acts",
            ))

    fractures.sort(key=lambda f: f.severity, reverse=True)
    return fractures

def minimal_correction(state: Dict[str, Any], fracture: Fracture) -> tuple[Dict[str, Any], str]:
    next_state = dict(state)
    script = state.get("script", "") or ""
    next_state["scars"] = list(state.get("scars", []) or [])
    premise = (state.get("premise") or "").strip()

    if fracture.class_ == "character_drift":
        name = fracture.hint.split(":")[-1] if ":" in fracture.hint else fracture.location
        insert = (
            f"\n\n[REPAIR · presence]\n"
            f"{name} steps back into frame — unresolved motive intact.\n"
            f'                    {name}: "I never left this story."'
        )
        if "FADE OUT." in script:
            next_state["script"] = script.replace("FADE OUT.", insert + "\n\nFADE OUT.", 1)
        else:
            next_state["script"] = script + insert
        return next_state, f"Planted presence for {name}"

    if fracture.class_ == "theme_noise":
        insert = (
            f"\n\n[REPAIR · premise echo]\n"
            f"Late-act residual: {premise[:100]}\n"
            f'                    VOICE OVER: "The break still remembers."'
        )
        if "FADE OUT." in script:
            next_state["script"] = script.replace("FADE OUT.", insert + "\n\nFADE OUT.", 1)
        else:
            next_state["script"] = script + insert
        return next_state, "Late-act premise echo injected"

    if fracture.class_ == "plot_hole":
        insert = (
            "\n\n[REPAIR · continuity]\n"
            "Expanded beat restores setup for later acts. A detail earlier denied returns."
        )
        if "FADE OUT." in script:
            next_state["script"] = script.replace("FADE OUT.", insert + "\n\nFADE OUT.", 1)
        else:
            next_state["script"] = script + insert
        return next_state, "Expanded thin narrative body"

    if fracture.class_ == "act_imbalance":
        insert = "\n\n[REPAIR · structure]\nStructural note: rebalance act scene density across the fracture field."
        if "FADE OUT." in script:
            next_state["script"] = script.replace("FADE OUT.", insert + "\n\nFADE OUT.", 1)
        else:
            next_state["script"] = script + insert
        return next_state, "Rebalanced structural notes"

    insert = f"\n\n[REPAIR · {fracture.class_}]\nMicro-fix applied."
    if "FADE OUT." in script:
        next_state["script"] = script.replace("FADE OUT.", insert + "\n\nFADE OUT.", 1)
    else:
        next_state["script"] = script + insert
    return next_state, f"Minimal correction for {fracture.class_}"

def score_state(state: Dict[str, Any]) -> float:
    fracs = detect_fractures(state)
    sev = sum(f.severity for f in fracs)
    density = min(1.0, len((state.get("script") or "")) / 1800.0)
    return max(0.0, 1.0 - sev / 5.0) * 0.6 + density * 0.4

def verify_state(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    b = score_state(before)
    a = score_state(after)
    delta_r = a - b
    before_high = len([f for f in detect_fractures(before) if f.severity >= SCIENCE["tau"]])
    after_high = len([f for f in detect_fractures(after) if f.severity >= SCIENCE["tau"]])
    ok = (delta_r > 0 and after_high <= before_high) or (delta_r >= 0.02 and after_high == 0)
    return {
        "ok": ok,
        "deltaR": round(delta_r, 4),
        "notes": [f"score {b:.3f} → {a:.3f}", f"ΔR={delta_r:.4f}", f"high-sev {before_high}→{after_high}"],
    }

def run_msil(state: Dict[str, Any], rye_history: Optional[List[float]] = None) -> Dict[str, Any]:
    fracs = detect_fractures(state)
    continuity = max(0.0, 1.0 - len(fracs) * 0.08)
    oscillation = 0.0
    if rye_history and len(rye_history) > 1:
        diffs = [abs(rye_history[i] - rye_history[i-1]) for i in range(1, len(rye_history))]
        oscillation = sum(diffs) / len(diffs)
    stability = continuity * 0.7 + max(0.0, 1.0 - oscillation * 2) * 0.3
    stability = max(0.0, min(1.0, stability))
    collapse = max(0.0, min(1.0, 1.0 - stability + oscillation))
    if stability >= 0.72:
        verdict = "stable"
    elif stability >= 0.45:
        verdict = "repairing"
    else:
        verdict = "unstable"
    return {
        "stabilityIndex": round(stability, 4),
        "oscillation": round(oscillation, 4),
        "continuity": round(continuity, 4),
        "collapseRisk": round(collapse, 4),
        "verdict": verdict,
        "notes": [f"MSIL verdict: {verdict}"],
    }

def run_tgrm(state: Dict[str, Any], max_cycles: Optional[int] = None) -> Dict[str, Any]:
    if max_cycles is None:
        max_cycles = int(SCIENCE["maxCycles"])
    state = dict(state)
    state["scars"] = list(state.get("scars", []) or [])
    log: List[Dict[str, Any]] = []
    total_delta_r = 0.0
    total_energy = 0
    micro = full = reinf = 0
    rye_series: List[float] = []

    for cycle in range(1, max_cycles + 1):
        fractures = detect_fractures(state)
        total_energy += 1
        if not fractures:
            log.append({
                "cycle": cycle,
                "phase": "VERIFY",
                "verified": True,
                "deltaR": 0.0,
                "energy": 1,
                "rye": 0.0,
                "notes": ["No fractures — equilibrium"],
            })
            break

        fracture = fractures[0]
        before = dict(state)
        is_micro = fracture.severity < SCIENCE["tau"]
        energy_cost = 1 if is_micro else 5
        if is_micro:
            micro += 1
        else:
            full += 1

        state, note = minimal_correction(state, fracture)
        total_energy += energy_cost
        ver = verify_state(before, state)
        total_energy += 1
        total_delta_r += max(0.0, ver["deltaR"])
        cycle_e = 1 + energy_cost + 1
        rye = round(max(0.0, ver["deltaR"]) / cycle_e, 4)
        rye_series.append(rye)

        log.append({
            "cycle": cycle,
            "phase": "VERIFY" if ver["ok"] else "MINIMAL_CORRECTION",
            "fracture": asdict(fracture),
            "correction": note,
            "verified": ver["ok"],
            "deltaR": ver["deltaR"],
            "energy": cycle_e,
            "rye": rye,
            "notes": [
                f"DETECT {fracture.class_} sev={fracture.severity:.2f} {'MICRO' if is_micro else 'FULL'}",
                note,
            ] + ver["notes"],
        })

        if ver["ok"] and ver["deltaR"] > 0:
            scar = ScarMemory(
                key=f"{fracture.class_}:{fracture.hint}",
                fracture_class=fracture.class_,
                fix=note,
                rye=rye,
            )
            state["scars"].append(asdict(scar))
            reinf += 1
            if not any(f.severity >= SCIENCE["tau"] for f in detect_fractures(state)):
                break

    energy = max(1, total_energy)
    rye = round(max(0.0, total_delta_r) / energy, 4)
    msil = run_msil(state, rye_series)
    return {
        "state": state,
        "metrics": {
            "deltaR": round(total_delta_r, 4),
            "energy": energy,
            "rye": rye,
            "cycles": len(log),
            "microRepairs": micro,
            "fullRepairs": full,
            "reinforcements": reinf,
            "stabilityIndex": msil["stabilityIndex"],
        },
        "msil": msil,
        "log": log,
        "scars": state.get("scars", []),
    }
