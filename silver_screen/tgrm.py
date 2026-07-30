"""Bounded narrative repair with verification, rollback, and scar memory."""

from __future__ import annotations

import copy
import hashlib
import math
import re
import statistics
from dataclasses import asdict
from typing import Any

from .science import FORMATS, SCIENCE, Fracture, ScarMemory, format_meta
from .script_engine import rebuild_structure, render_screenplay

PLACEHOLDER_PATTERNS = (
    "placeholder",
    "todo",
    "tbd",
    "lorem ipsum",
    "micro-fix applied",
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _fracture_id(class_name: str, location: str, description: str) -> str:
    digest = hashlib.sha256(
        f"{class_name}|{location}|{description}".encode("utf-8")
    ).hexdigest()[:10]
    return f"fx_{digest}"


def _new_fracture(
    class_name: str,
    severity: float,
    location: str,
    description: str,
    hint: str,
) -> Fracture:
    return Fracture(
        id=_fracture_id(class_name, location, description),
        class_=class_name,  # type: ignore[arg-type]
        severity=round(_clamp(severity), 4),
        location=location,
        description=description,
        hint=hint,
    )


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def _theme_terms(state: dict[str, Any]) -> list[str]:
    bible = state.get("storyBible") or {}
    terms = bible.get("themeTerms") if isinstance(bible, dict) else None
    if isinstance(terms, list):
        cleaned = [str(term).lower() for term in terms if str(term).strip()]
        if cleaned:
            return cleaned[:8]
    premise = str(state.get("premise") or "")
    return [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z'-]{4,}", premise)
    ][:6]


def _theme_coherence(state: dict[str, Any]) -> float:
    terms = _theme_terms(state)
    if not terms:
        return 1.0
    script = str(state.get("script") or "")
    late = script[len(script) // 2 :].lower()
    hits = sum(1 for term in terms if term in late)
    return _clamp(hits / len(terms))


def _act_balance(state: dict[str, Any]) -> float:
    acts = state.get("acts") or []
    counts: list[int] = []
    for act in acts:
        if isinstance(act, dict):
            counts.append(int(act.get("scene_count", len(act.get("sceneIds") or [])) or 0))
    if not counts:
        return 0.0
    if max(counts) == 0:
        return 0.0
    spread = max(counts) - min(counts)
    ideal = max(1.0, sum(counts) / len(counts))
    return _clamp(1.0 - spread / (ideal * 2.0))


def _character_coverage(state: dict[str, Any]) -> float:
    characters = state.get("characters") or []
    if not characters:
        return 0.0
    script = str(state.get("script") or "").lower()
    covered = 0
    for character in characters:
        name = str(character.get("name") if isinstance(character, dict) else character).strip()
        if name and script.count(name.lower()) >= 2:
            covered += 1
    return covered / len(characters)


def _dialogue_values(state: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    values: list[tuple[dict[str, Any], str]] = []
    for scene in state.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for shot in scene.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            dialogue = str(shot.get("dialogue") or "").strip()
            if dialogue:
                values.append((shot, dialogue))
    return values


def detect_fractures(state: dict[str, Any]) -> list[Fracture]:
    """Detect high-value narrative fractures in a structured film state."""

    fractures: list[Fracture] = []
    script = str(state.get("script") or "")
    scenes = [scene for scene in state.get("scenes") or [] if isinstance(scene, dict)]
    characters = [
        character
        for character in state.get("characters") or []
        if isinstance(character, dict)
    ]
    fmt = str(state.get("format") or "short")
    meta = format_meta(fmt)
    expected_scenes = int(meta["scenes"])
    target_words = int(state.get("targetWords") or meta.get("target_words") or 1000)

    if len(scenes) != expected_scenes:
        delta = abs(len(scenes) - expected_scenes)
        fractures.append(
            _new_fracture(
                "plot_hole",
                min(0.95, 0.58 + delta / max(1, expected_scenes)),
                "structure",
                f"Scene count is {len(scenes)} but format {fmt} requires {expected_scenes}.",
                f"restore_scene_count:{expected_scenes}",
            )
        )

    words = _word_count(script)
    minimum_words = max(220, int(target_words * 0.35))
    if words < minimum_words:
        fractures.append(
            _new_fracture(
                "plot_hole",
                min(0.92, 0.62 + (minimum_words - words) / max(1, minimum_words) * 0.25),
                "script",
                f"Screenplay blueprint has {words} words; minimum useful density is {minimum_words}.",
                "expand_scene_actions",
            )
        )

    if "FADE OUT." not in script.upper() or "THE END" not in script.upper():
        fractures.append(
            _new_fracture(
                "missing_ending",
                0.7,
                "ending",
                "The screenplay does not contain a verifiable closing marker.",
                "rerender_ending",
            )
        )

    lowered = script.lower()
    found_placeholders = [token for token in PLACEHOLDER_PATTERNS if token in lowered]
    if found_placeholders:
        fractures.append(
            _new_fracture(
                "placeholder_content",
                0.84,
                "script",
                f"Unfinished content markers remain: {', '.join(found_placeholders)}.",
                "remove_placeholders",
            )
        )

    numbers = [int(scene.get("number", 0) or 0) for scene in scenes]
    acts = [int(scene.get("act", 1) or 1) for scene in scenes]
    if numbers != list(range(1, len(scenes) + 1)) or any(
        acts[index] < acts[index - 1] for index in range(1, len(acts))
    ):
        fractures.append(
            _new_fracture(
                "timeline_break",
                0.78,
                "scene_order",
                "Scene numbers or act progression are not monotonic.",
                "normalize_timeline",
            )
        )

    act_count = int(meta["acts"])
    counts = [sum(1 for scene in scenes if int(scene.get("act", 1)) == act) for act in range(1, act_count + 1)]
    if counts and (min(counts) == 0 or max(counts) - min(counts) > 1):
        fractures.append(
            _new_fracture(
                "act_imbalance",
                0.72 if min(counts) else 0.88,
                "acts",
                f"Act scene counts are imbalanced: {counts}.",
                "rebalance_acts",
            )
        )

    minimum_mentions = max(2, math.ceil(len(scenes) / max(2, len(characters) * 2)))
    for character in characters:
        name = str(character.get("name") or "").strip()
        if not name:
            continue
        mentions = lowered.count(name.lower())
        if mentions < minimum_mentions:
            fractures.append(
                _new_fracture(
                    "character_drift",
                    min(0.86, 0.5 + (minimum_mentions - mentions) * 0.08),
                    name,
                    f"{name} has {mentions} mentions; minimum coverage is {minimum_mentions}.",
                    f"reinforce_presence:{name}",
                )
            )

    theme_score = _theme_coherence(state)
    if theme_score < float(SCIENCE["domainGate"]):
        fractures.append(
            _new_fracture(
                "theme_noise",
                max(0.61, 0.85 - theme_score * 0.35),
                "late_story",
                f"Late-story theme coherence is {theme_score:.2f}; required gate is {SCIENCE['domainGate']:.2f}.",
                "echo_theme_late",
            )
        )

    dialogue_counts: dict[str, int] = {}
    for _, dialogue in _dialogue_values(state):
        normalized = re.sub(r"\s+", " ", dialogue.lower())
        dialogue_counts[normalized] = dialogue_counts.get(normalized, 0) + 1
    repeats = [text for text, count in dialogue_counts.items() if count >= 3]
    if repeats:
        fractures.append(
            _new_fracture(
                "dialogue_redundancy",
                min(0.82, 0.58 + len(repeats) * 0.05),
                "dialogue",
                f"{len(repeats)} dialogue line pattern(s) repeat three or more times.",
                "vary_repeated_dialogue",
            )
        )

    durations = [float(scene.get("scriptMinutes", 0) or 0) for scene in scenes]
    positive_durations = [value for value in durations if value > 0]
    if positive_durations:
        ratio = max(positive_durations) / max(0.01, min(positive_durations))
        if ratio > 1.8:
            fractures.append(
                _new_fracture(
                    "pacing_collapse",
                    min(0.88, 0.55 + (ratio - 1.8) * 0.08),
                    "scene_runtime",
                    f"Scene runtime ratio is {ratio:.2f}; pacing is too uneven.",
                    "normalize_scene_runtime",
                )
            )

    fractures.sort(key=lambda fracture: (-fracture.severity, fracture.class_, fracture.location))
    return fractures


def score_breakdown(state: dict[str, Any]) -> dict[str, float]:
    fractures = detect_fractures(state)
    weighted_severity = sum(fracture.severity for fracture in fractures)
    continuity = _clamp(1.0 - weighted_severity / 7.0)
    theme = _theme_coherence(state)
    act_balance = _act_balance(state)
    character = _character_coverage(state)
    fmt = str(state.get("format") or "short")
    target = int(state.get("targetWords") or format_meta(fmt).get("target_words", 1000))
    completeness = _clamp(_word_count(str(state.get("script") or "")) / max(1, target * 0.45))
    ending = 1.0 if "FADE OUT." in str(state.get("script") or "").upper() else 0.0
    overall = (
        continuity * 0.32
        + theme * 0.18
        + act_balance * 0.18
        + character * 0.14
        + completeness * 0.12
        + ending * 0.06
    )
    return {
        "overall": round(_clamp(overall), 6),
        "continuity": round(continuity, 6),
        "themeCoherence": round(theme, 6),
        "actBalance": round(act_balance, 6),
        "characterCoverage": round(character, 6),
        "completeness": round(completeness, 6),
        "ending": ending,
    }


def score_state(state: dict[str, Any]) -> float:
    return score_breakdown(state)["overall"]


def _rerender(state: dict[str, Any]) -> None:
    state["script"] = render_screenplay(state)


def _normalize_scene_structure(state: dict[str, Any]) -> None:
    scenes = [scene for scene in state.get("scenes") or [] if isinstance(scene, dict)]
    fmt = str(state.get("format") or "short")
    meta = format_meta(fmt)
    count = len(scenes)
    acts = int(meta["acts"])
    chapters = int(meta["chapters"])
    for index, scene in enumerate(scenes):
        scene["number"] = index + 1
        scene["act"] = min(acts, (index * acts) // max(1, count) + 1)
        scene["chapter"] = min(chapters, (index * chapters) // max(1, count) + 1)
    structure = rebuild_structure(scenes, fmt)
    state["scenes"] = scenes
    state["acts"] = structure["acts"]
    state["chapters"] = structure["chapters"]


def _repair_scene_count(state: dict[str, Any], expected: int) -> str:
    scenes = [scene for scene in state.get("scenes") or [] if isinstance(scene, dict)]
    if not scenes:
        return "No source scene was available for structural repair"
    if len(scenes) > expected:
        del scenes[expected:]
    while len(scenes) < expected:
        source = copy.deepcopy(scenes[-1])
        new_number = len(scenes) + 1
        source["id"] = f"scene_repaired_{new_number:03d}"
        source["summary"] = (
            f"Repair bridge {new_number}: "
            "restore the missing causal step and carry verified evidence forward."
        )
        source["action"] = (
            str(source.get("action") or "")
            + " The added bridge scene makes the missing cause-and-effect relationship explicit."
        )
        source["shots"] = copy.deepcopy(source.get("shots") or [])
        scenes.append(source)
    state["scenes"] = scenes
    _normalize_scene_structure(state)
    _rerender(state)
    return f"Restored scene count to {expected}"


def minimal_correction(state: dict[str, Any], fracture: Fracture) -> tuple[dict[str, Any], str]:
    """Apply one bounded correction to a deep copy of the state."""

    next_state = copy.deepcopy(state)
    scenes = [scene for scene in next_state.get("scenes") or [] if isinstance(scene, dict)]

    if fracture.class_ == "character_drift":
        name = fracture.hint.split(":", 1)[-1] if ":" in fracture.hint else fracture.location
        target = scenes[-1] if scenes else None
        if target is not None:
            target["action"] = (
                str(target.get("action") or "")
                + f" {name} returns with evidence that changes the final decision and completes the character's causal role."
            )
            shots = target.setdefault("shots", [])
            shots.append(
                {
                    "id": f"shot_repair_presence_{len(shots) + 1}",
                    "type": "reaction",
                    "description": f"Hold on {name} as the repaired motive becomes visible.",
                    "dialogue": f'{name}: "My part of this does not disappear because the plan changed."',
                    "durationSec": 3.0,
                }
            )
            _rerender(next_state)
        return next_state, f"Reinforced causal presence for {name}"

    if fracture.class_ == "theme_noise":
        terms = _theme_terms(next_state)
        for index, scene in enumerate(scenes[len(scenes) // 2 :], start=1):
            term = terms[(index - 1) % len(terms)] if terms else "repair"
            scene["action"] = (
                str(scene.get("action") or "")
                + f" The late-story consequence explicitly returns to {term}, tying the choice back to the original premise."
            )
        _rerender(next_state)
        return next_state, "Restored late-story theme echoes"

    if fracture.class_ == "plot_hole" and fracture.hint.startswith("restore_scene_count:"):
        expected = int(fracture.hint.rsplit(":", 1)[-1])
        note = _repair_scene_count(next_state, expected)
        return next_state, note

    if fracture.class_ == "plot_hole":
        for scene in scenes:
            scene["action"] = (
                str(scene.get("action") or "")
                + " The scene now states the relevant cause, the observed evidence, and the consequence that carries forward."
            )
        _rerender(next_state)
        return next_state, "Expanded causal detail across scene actions"

    if fracture.class_ in {"timeline_break", "act_imbalance"}:
        scenes.sort(key=lambda scene: int(scene.get("number", 0) or 0))
        next_state["scenes"] = scenes
        _normalize_scene_structure(next_state)
        _rerender(next_state)
        return next_state, "Normalized scene order, act balance, and chapter allocation"

    if fracture.class_ == "dialogue_redundancy":
        seen: dict[str, int] = {}
        for scene in scenes:
            for shot in scene.get("shots") or []:
                if not isinstance(shot, dict) or not shot.get("dialogue"):
                    continue
                dialogue = str(shot["dialogue"])
                key = re.sub(r"\s+", " ", dialogue.lower())
                count = seen.get(key, 0)
                if count:
                    speaker = dialogue.split(":", 1)[0].strip() or "CHARACTER"
                    anchors = scene.get("continuityAnchors") or ["evidence"]
                    anchor = str(anchors[0]) if anchors else "evidence"
                    beat = str(scene.get("summary") or "this threshold").split(":", 1)[0].strip().lower()
                    shot["dialogue"] = (
                        f'{speaker}: "At {beat}, evidence tied to {anchor} changes what this costs. '
                        'I will not hide that consequence."'
                    )
                seen[key] = count + 1
        _rerender(next_state)
        return next_state, "Varied repeated dialogue with scene-specific consequences"

    if fracture.class_ == "pacing_collapse":
        meta = format_meta(str(next_state.get("format") or "short"))
        target = float(meta["minutes"]) / max(1, len(scenes))
        for scene in scenes:
            scene["scriptMinutes"] = round(target, 2)
        return next_state, "Normalized scene runtime targets"

    if fracture.class_ == "placeholder_content":
        def clean(value: str) -> str:
            cleaned = value
            for token in PLACEHOLDER_PATTERNS:
                cleaned = re.sub(re.escape(token), "resolved beat", cleaned, flags=re.IGNORECASE)
            return cleaned

        for scene in scenes:
            for field in ("summary", "action", "conflict", "turn"):
                if isinstance(scene.get(field), str):
                    scene[field] = clean(scene[field])
            for shot in scene.get("shots") or []:
                if not isinstance(shot, dict):
                    continue
                for field in ("description", "dialogue"):
                    if isinstance(shot.get(field), str):
                        shot[field] = clean(shot[field])
        _rerender(next_state)
        return next_state, "Removed unfinished content markers"

    if fracture.class_ == "missing_ending":
        _rerender(next_state)
        return next_state, "Restored screenplay closing markers"

    _rerender(next_state)
    return next_state, f"Applied bounded correction for {fracture.class_}"


def verify_state(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_score = score_state(before)
    after_score = score_state(after)
    delta = after_score - before_score
    before_high = sum(
        1 for fracture in detect_fractures(before) if fracture.severity >= float(SCIENCE["tau"])
    )
    after_high = sum(
        1 for fracture in detect_fractures(after) if fracture.severity >= float(SCIENCE["tau"])
    )
    accepted = delta >= 0.001 and after_high <= before_high
    return {
        "ok": accepted,
        "beforeScore": round(before_score, 6),
        "afterScore": round(after_score, 6),
        "deltaR": round(delta, 6),
        "beforeHighSeverity": before_high,
        "afterHighSeverity": after_high,
        "notes": [
            f"score {before_score:.4f} -> {after_score:.4f}",
            f"deltaR={delta:.6f}",
            f"high severity {before_high} -> {after_high}",
        ],
    }


def run_msil(state: dict[str, Any], rye_history: list[float] | None = None) -> dict[str, Any]:
    breakdown = score_breakdown(state)
    history = rye_history or []
    oscillation = 0.0
    if len(history) > 1:
        oscillation = statistics.mean(
            abs(history[index] - history[index - 1])
            for index in range(1, len(history))
        )
    stability = _clamp(
        breakdown["continuity"] * 0.32
        + breakdown["actBalance"] * 0.24
        + breakdown["themeCoherence"] * 0.2
        + breakdown["characterCoverage"] * 0.14
        + max(0.0, 1.0 - oscillation * 4.0) * 0.1
    )
    collapse_risk = _clamp(1.0 - stability + oscillation)
    if stability >= 0.78:
        verdict = "stable"
    elif stability >= 0.52:
        verdict = "repairing"
    else:
        verdict = "unstable"
    return {
        "stabilityIndex": round(stability, 6),
        "oscillation": round(oscillation, 6),
        "actBalance": breakdown["actBalance"],
        "continuity": breakdown["continuity"],
        "themeCoherence": breakdown["themeCoherence"],
        "characterCoverage": breakdown["characterCoverage"],
        "collapseRisk": round(collapse_risk, 6),
        "verdict": verdict,
        "notes": [
            f"MSIL verdict: {verdict}",
            f"Remaining fractures: {len(detect_fractures(state))}",
        ],
    }


def _reinforce_scar(
    scars: list[dict[str, Any]], fracture: Fracture, fix: str, rye: float
) -> None:
    key = f"{fracture.class_}:{fracture.hint}"
    for scar in scars:
        if isinstance(scar, dict) and scar.get("key") == key:
            scar["uses"] = int(scar.get("uses", 1)) + 1
            scar["rye"] = max(float(scar.get("rye", 0.0)), rye)
            scar["fix"] = fix
            return
    scars.append(
        asdict(
            ScarMemory(
                key=key,
                fracture_class=fracture.class_,
                fix=fix,
                rye=rye,
            )
        )
    )


def run_tgrm(
    state: dict[str, Any],
    max_cycles: int | None = None,
    energy_budget: int | None = None,
) -> dict[str, Any]:
    """Run TGRM with an explicit energy budget and rollback on regression."""

    cycle_limit = max(1, int(max_cycles or SCIENCE["maxCycles"]))
    budget = max(3, int(energy_budget or SCIENCE["energyBudget"]))
    current = copy.deepcopy(state)
    current["scars"] = copy.deepcopy(current.get("scars") or [])
    initial_score = score_state(current)
    total_delta = 0.0
    total_energy = 0
    micro_repairs = 0
    full_repairs = 0
    reinforcements = 0
    rolled_back = 0
    accepted_repairs = 0
    no_improvement_streak = 0
    rye_history: list[float] = []
    log: list[dict[str, Any]] = []
    stop_reason = "max_cycles"

    for cycle in range(1, cycle_limit + 1):
        if total_energy + 1 > budget:
            stop_reason = "energy_budget_exhausted"
            break
        fractures = detect_fractures(current)
        total_energy += 1
        if not fractures:
            stop_reason = "equilibrium"
            log.append(
                {
                    "cycle": cycle,
                    "phase": "VERIFY",
                    "verified": True,
                    "accepted": False,
                    "deltaR": 0.0,
                    "energy": 1,
                    "rye": 0.0,
                    "notes": ["No fractures detected; equilibrium verified."],
                }
            )
            break

        fracture = fractures[0]
        is_micro = fracture.severity < float(SCIENCE["tau"])
        correction_cost = 1 if is_micro else 5
        cycle_energy = 1 + correction_cost + 1
        if total_energy + correction_cost + 1 > budget:
            stop_reason = "energy_budget_exhausted"
            log.append(
                {
                    "cycle": cycle,
                    "phase": "BUDGET_GATE",
                    "verified": False,
                    "accepted": False,
                    "deltaR": 0.0,
                    "energy": 1,
                    "rye": 0.0,
                    "fracture": asdict(fracture),
                    "notes": [
                        f"Repair requires {correction_cost + 1} additional energy units; "
                        f"only {budget - total_energy} remain."
                    ],
                }
            )
            break

        before = copy.deepcopy(current)
        candidate, correction = minimal_correction(current, fracture)
        total_energy += correction_cost
        verification = verify_state(before, candidate)
        total_energy += 1
        accepted = bool(verification["ok"])
        positive_delta = max(0.0, float(verification["deltaR"])) if accepted else 0.0
        cycle_rye = round(positive_delta / cycle_energy, 6)
        rye_history.append(cycle_rye)

        if is_micro:
            micro_repairs += 1
        else:
            full_repairs += 1

        if accepted:
            current = candidate
            total_delta += positive_delta
            accepted_repairs += 1
            no_improvement_streak = 0
            _reinforce_scar(current["scars"], fracture, correction, cycle_rye)
            reinforcements += 1
            phase = "REINFORCE"
        else:
            current = before
            rolled_back += 1
            no_improvement_streak += 1
            phase = "ROLLBACK"

        log.append(
            {
                "cycle": cycle,
                "phase": phase,
                "verified": accepted,
                "accepted": accepted,
                "fracture": asdict(fracture),
                "correction": correction,
                "deltaR": round(float(verification["deltaR"]), 6),
                "energy": cycle_energy,
                "rye": cycle_rye,
                "beforeScore": verification["beforeScore"],
                "afterScore": verification["afterScore"],
                "notes": [
                    f"DETECT {fracture.class_} severity={fracture.severity:.2f}",
                    f"MODE {'MICRO' if is_micro else 'FULL'}",
                    correction,
                    *verification["notes"],
                    "Accepted and reinforced." if accepted else "Rejected and rolled back.",
                ],
            }
        )

        if accepted and not detect_fractures(current):
            stop_reason = "stable_after_repair"
            break
        if no_improvement_streak >= 2:
            stop_reason = "no_verified_improvement"
            break
    else:
        stop_reason = "max_cycles"

    final_score = score_state(current)
    remaining = detect_fractures(current)
    energy = max(1, total_energy)
    overall_rye = round(total_delta / energy, 6)
    msil = run_msil(current, rye_history)
    current["status"] = "stable" if not remaining else "repaired"
    current["tgrmStopReason"] = stop_reason

    return {
        "state": current,
        "metrics": {
            "deltaR": round(total_delta, 6),
            "energy": energy,
            "energyBudget": budget,
            "energyRemaining": max(0, budget - total_energy),
            "rye": overall_rye,
            "cycles": len(log),
            "microRepairs": micro_repairs,
            "fullRepairs": full_repairs,
            "acceptedRepairs": accepted_repairs,
            "rolledBackRepairs": rolled_back,
            "reinforcements": reinforcements,
            "initialScore": round(initial_score, 6),
            "finalScore": round(final_score, 6),
            "stabilityIndex": msil["stabilityIndex"],
            "remainingFractures": len(remaining),
            "stopReason": stop_reason,
        },
        "msil": msil,
        "log": log,
        "scars": current.get("scars", []),
        "remainingFractures": [asdict(fracture) for fracture in remaining],
    }
