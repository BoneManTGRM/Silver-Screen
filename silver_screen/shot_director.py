"""Shot-specific direction, prompt ledgers, audio strategy, and coverage gates."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Any

from .creative_direction import (
    negative_prompt as creative_negative_prompt,
    normalize_creative_direction,
)
from .transition_engine import prompt_directive

AUDIO_STRATEGIES: dict[str, dict[str, Any]] = {
    "dub_later": {
        "label": "Professional dub later - ambience only",
        "generateAudio": True,
        "instruction": (
            "Natural production ambience and environmental sound only. No intelligible "
            "spoken words. Keep mouths relaxed or use non-speaking reactions so precise "
            "dialogue and narration can be added later in Script Sync."
        ),
    },
    "native_dialogue": {
        "label": "Native generated dialogue",
        "generateAudio": True,
        "instruction": (
            "Generate natural synchronized production audio. When an exact dialogue cue is "
            "provided, speak only that line, once, with no added words or overlapping voices."
        ),
    },
    "silent": {
        "label": "Silent picture",
        "generateAudio": False,
        "instruction": "Generate silent picture only. No speech, music, or environmental audio.",
    },
}
DEFAULT_AUDIO_STRATEGY = "dub_later"
MAX_SHOT_OVERRIDES = 900
MAX_OVERRIDE_CHARS = 1800
MAX_RAW_OVERRIDE_CHARS = 120_000

COMMON_PROMPT_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "with",
    "for",
    "from",
    "this",
    "that",
    "same",
    "shot",
    "scene",
    "film",
    "camera",
    "cinematic",
    "keep",
    "use",
    "no",
    "one",
    "character",
    "characters",
    "exact",
    "preserve",
    "natural",
    "professional",
    "continuity",
    "audio",
    "light",
    "lighting",
    "movement",
}


class ShotDirectorError(RuntimeError):
    """Raised when an approved shot contract is missing or has drifted."""


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _creative_config(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get("creativeDirection")
    cfg = normalize_creative_direction(raw)
    if not isinstance(raw, dict):
        cfg["medium"] = (
            "cinematic image-to-video matching the authorized reference medium; "
            "photographic references remain live action and illustrated references "
            "remain premium feature animation"
        )
    return cfg


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _split_override_header(line: str) -> tuple[str | None, str]:
    """Parse bounded `Clip 1: direction`, `[1] = direction`, or `shot_0001: ...`."""

    text = line.strip()
    if not text:
        return None, ""
    lowered = text.casefold()
    for prefix in ("clip ", "shot "):
        if lowered.startswith(prefix):
            text = text[len(prefix) :].lstrip()
            break
    if text.startswith("["):
        close = text.find("]", 1, 8)
        if close == -1:
            return None, ""
        key = text[1:close].strip()
        rest = text[close + 1 :].lstrip()
    else:
        index = 0
        while index < len(text) and (
            text[index].isdigit()
            or text[index]
            in "_-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        ):
            index += 1
        key = text[:index].strip()
        rest = text[index:].lstrip()
    if not key:
        return None, ""
    if not (key.isdigit() or re.fullmatch(r"shot_\d{1,4}", key.casefold())):
        return None, ""
    if not rest or rest[0] not in ":=-":
        return None, ""
    return key.casefold(), rest[1:].strip()


def parse_shot_override_text(value: str) -> dict[str, str]:
    """Parse human-friendly shot overrides without ambiguous whitespace regexes."""

    result: dict[str, str] = {}
    current: str | None = None
    raw_text = str(value or "").replace("\r\n", "\n")[:MAX_RAW_OVERRIDE_CHARS]
    for raw in raw_text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        key, body = _split_override_header(line)
        if key is not None:
            current = str(max(1, int(key))) if key.isdigit() else key
            result[current] = _clean_text(body, MAX_OVERRIDE_CHARS)
        elif current:
            result[current] = _clean_text(
                f"{result[current]} {line}", MAX_OVERRIDE_CHARS
            )
        if len(result) >= MAX_SHOT_OVERRIDES:
            break
    return {key: value for key, value in result.items() if value}


def normalize_shot_overrides(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        return parse_shot_override_text(value)
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip().casefold()
        if key.isdigit():
            key = str(max(1, int(key)))
        elif not re.fullmatch(r"shot_\d{1,4}", key):
            continue
        text = _clean_text(raw_value, MAX_OVERRIDE_CHARS)
        if text:
            result[key] = text
        if len(result) >= MAX_SHOT_OVERRIDES:
            break
    return result


def normalize_shot_direction(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    audio = str(
        raw.get("audioStrategy")
        or os.getenv("SILVER_SCREEN_SHOT_AUDIO_STRATEGY")
        or DEFAULT_AUDIO_STRATEGY
    ).strip().casefold()
    if audio not in AUDIO_STRATEGIES:
        audio = DEFAULT_AUDIO_STRATEGY
    try:
        similarity = float(
            raw.get(
                "maximumPromptSimilarity",
                os.getenv("SILVER_SCREEN_SHOT_MAX_SIMILARITY", "0.92"),
            )
        )
    except (TypeError, ValueError):
        similarity = 0.92
    try:
        distinct = float(
            raw.get(
                "minimumDistinctShotRatio",
                os.getenv("SILVER_SCREEN_SHOT_MIN_DISTINCT_RATIO", "0.82"),
            )
        )
    except (TypeError, ValueError):
        distinct = 0.82
    ledger = raw.get("approvedPromptLedger")
    if not isinstance(ledger, dict):
        ledger = {}
    return {
        "schemaVersion": 1,
        "audioStrategy": audio,
        "audioLabel": AUDIO_STRATEGIES[audio]["label"],
        "shotPromptOverrides": normalize_shot_overrides(
            raw.get("shotPromptOverrides")
        ),
        "coverageGate": _bool_value(
            raw.get("coverageGate"),
            os.getenv("SILVER_SCREEN_SHOT_COVERAGE_GATE", "1")
            .strip()
            .casefold()
            not in {"0", "false", "no", "off"},
        ),
        "maximumPromptSimilarity": max(0.55, min(0.99, similarity)),
        "minimumDistinctShotRatio": max(0.35, min(1.0, distinct)),
        "enforcePromptLedger": _bool_value(
            raw.get("enforcePromptLedger"), False
        ),
        "approvedPromptLedger": ledger,
        "approvedLedgerHash": _clean_text(raw.get("approvedLedgerHash"), 128),
    }


def _scene_by_number(state: dict[str, Any], number: int) -> dict[str, Any]:
    scenes = [item for item in state.get("scenes") or [] if isinstance(item, dict)]
    for scene in scenes:
        if int(scene.get("number", -1) or -1) == int(number):
            return scene
    if not scenes:
        raise ShotDirectorError("The film state contains no scenes")
    return scenes[min(len(scenes) - 1, max(0, int(number) - 1))]


def _cast_details(state: dict[str, Any], scene: dict[str, Any]) -> str:
    characters = {
        str(item.get("id")): item
        for item in state.get("characters") or []
        if isinstance(item, dict)
    }
    details: list[str] = []
    for character_id in scene.get("characters") or []:
        character = characters.get(str(character_id), {})
        name = _clean_text(character.get("name"), 80)
        description = _clean_text(character.get("description"), 420)
        if name:
            details.append(f"{name}: {description}" if description else name)
    return "; ".join(details)


def select_shot_blueprint(
    state: dict[str, Any],
    scene: dict[str, Any],
    shot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Map each generated clip to a concrete screenplay shot rather than the whole scene."""

    runtime_shot = shot or {}
    scene_shots = [
        item for item in scene.get("shots") or [] if isinstance(item, dict)
    ]
    segment = max(1, int(runtime_shot.get("segment", 1) or 1))
    order = max(1, int(runtime_shot.get("order", segment) or segment))
    if scene_shots:
        index = (segment - 1) % len(scene_shots)
        cycle = (segment - 1) // len(scene_shots)
        source = scene_shots[index]
    else:
        index = 0
        cycle = 0
        source = {
            "type": "continuous",
            "description": scene.get("action") or scene.get("summary") or "",
            "dialogue": None,
            "durationSec": runtime_shot.get("plannedDurationSeconds", 8),
        }
    shot_type = _clean_text(source.get("type") or "continuous", 80)
    description = _clean_text(
        source.get("description")
        or scene.get("action")
        or scene.get("summary")
        or "",
        1100,
    )
    dialogue = _clean_text(source.get("dialogue"), 700)
    alternate = ""
    if cycle:
        alternates = (
            "Use a tighter complementary angle while preserving the same screen direction.",
            "Use a restrained reverse or reaction angle without resetting the action.",
            "Use a physically motivated detail insert that advances the same action.",
        )
        alternate = alternates[(cycle - 1) % len(alternates)]
    shot_direction = normalize_shot_direction(state.get("shotDirection"))
    overrides = shot_direction.get("shotPromptOverrides") or {}
    override = (
        overrides.get(str(order))
        or overrides.get(str(runtime_shot.get("id") or "").casefold())
        or ""
    )
    return {
        "shotId": str(runtime_shot.get("id") or f"shot_{order:04d}"),
        "order": order,
        "scene": int(scene.get("number", 1) or 1),
        "segment": segment,
        "sourceIndex": index,
        "coverageCycle": cycle,
        "type": shot_type,
        "description": description,
        "dialogue": dialogue,
        "durationSeconds": float(
            runtime_shot.get("plannedDurationSeconds")
            or source.get("durationSec")
            or 8
        ),
        "alternateCoverage": alternate,
        "override": _clean_text(override, MAX_OVERRIDE_CHARS),
    }


def _dialogue_seconds(dialogue: str) -> float:
    if not dialogue:
        return 0.0
    words = re.findall(r"\b[\w'-]+\b", dialogue)
    return round(len(words) / 2.5, 3)


def _creative_summary(state: dict[str, Any]) -> str:
    cfg = _creative_config(state)
    pieces = [
        f"Creative profile: {cfg.get('profileLabel') or cfg.get('profile')}.",
        f"Medium: {cfg.get('medium')}.",
        f"Realism: {cfg.get('realism')}.",
        f"Performance: {cfg.get('performanceStyle')}.",
        f"Camera grammar: {cfg.get('cameraStyle')}.",
        f"Pacing: {cfg.get('pacing')}.",
        f"Color and light: {cfg.get('colorLanguage')}.",
        _clean_text(cfg.get("globalVisualDirection"), 620),
        (
            "Director notes: " + _clean_text(cfg.get("directorNotes"), 360)
            if cfg.get("directorNotes")
            else ""
        ),
    ]
    return " ".join(item for item in pieces if item)


def _audio_instruction(
    state: dict[str, Any],
    blueprint: dict[str, Any],
) -> tuple[str, bool]:
    shot_direction = normalize_shot_direction(state.get("shotDirection"))
    strategy = shot_direction["audioStrategy"]
    config = AUDIO_STRATEGIES[strategy]
    instruction = str(config["instruction"])
    dialogue = str(blueprint.get("dialogue") or "").strip()
    if strategy == "native_dialogue" and dialogue:
        instruction += f' Exact dialogue cue: "{dialogue}".'
    elif strategy == "dub_later" and dialogue:
        seconds = _dialogue_seconds(dialogue)
        instruction += (
            f" Reserve about {max(1.0, seconds):.1f} seconds of readable non-speaking "
            "performance for the later voice line."
        )
    return instruction, bool(config["generateAudio"])


def render_negative_prompt(
    state: dict[str, Any],
    shot: dict[str, Any] | None = None,
) -> str:
    creative = _creative_config(state)
    direction = normalize_shot_direction(state.get("shotDirection"))
    items = [
        creative_negative_prompt(creative),
        "repeated scene reset",
        "generic trailer pose",
        "unmotivated camera spin",
        "random slow motion",
        "empty spectacle",
        "continuity jump",
        "wrong screen direction",
        "unrelated action",
    ]
    if direction["audioStrategy"] == "dub_later":
        items.extend(
            [
                "intelligible generated speech",
                "gibberish dialogue",
                "unrelated lip sync",
                "exaggerated mouth movement",
                "singing",
                "voiceover",
            ]
        )
    elif direction["audioStrategy"] == "native_dialogue":
        items.extend(
            [
                "added dialogue",
                "overlapping voices",
                "gibberish speech",
                "incorrect words",
                "singing",
            ]
        )
    else:
        items.extend(["speech", "music", "sound effects", "generated audio"])
    flattened: list[str] = []
    seen: set[str] = set()
    for block in items:
        for part in str(block or "").split(","):
            cleaned = _clean_text(part, 140).strip(" .")
            folded = cleaned.casefold()
            if cleaned and folded not in seen:
                seen.add(folded)
                flattened.append(cleaned)
    return ", ".join(flattened)[:1800]


def _retake_directive(shot: dict[str, Any] | None) -> str:
    active = (shot or {}).get("transitionRetake")
    if isinstance(active, dict):
        directive = _clean_text(active.get("directive"), 800)
        if directive:
            return "DIRECTOR REVIEW RETAKE: " + directive
    return ""


def render_directed_prompt(
    state: dict[str, Any],
    scene: dict[str, Any],
    shot: dict[str, Any] | None = None,
    repair: dict[str, Any] | None = None,
) -> str:
    """Render a concise shot-specific provider prompt with runtime continuity."""

    runtime_shot = shot or {}
    blueprint = select_shot_blueprint(state, scene, runtime_shot)
    cast = _cast_details(state, scene)
    audio_instruction, generate_audio = _audio_instruction(state, blueprint)
    creative = _creative_summary(state)
    continuity = prompt_directive(runtime_shot)
    retake = _retake_directive(runtime_shot)
    repair_suffix = _clean_text((repair or {}).get("promptSuffix"), 700)
    scene_context = _clean_text(
        scene.get("summary") or scene.get("action") or "", 520
    )
    conflict = _clean_text(scene.get("conflict"), 360)
    turn = _clean_text(scene.get("turn"), 360)

    pieces = [
        "PROFESSIONAL SHOT CONTRACT. CREATIVE CONTRACT.",
        creative,
        f"Genre and tone: {state.get('genre', 'drama')}, {state.get('tone', 'cinematic')}.",
        f"Setting: {_clean_text(scene.get('slugline'), 220)}.",
        f"Authorized cast continuity: {cast}." if cast else "",
        (
            f"Clip {blueprint['order']}, scene {blueprint['scene']}, coverage "
            f"segment {blueprint['segment']}. Shot type: {blueprint['type']}."
        ),
        f"SHOT OBJECTIVE: {blueprint['description']}.",
        (
            "ALTERNATE COVERAGE: " + blueprint["alternateCoverage"]
            if blueprint.get("alternateCoverage")
            else ""
        ),
        (
            "SHOT-SPECIFIC DIRECTOR OVERRIDE: " + blueprint["override"]
            if blueprint.get("override")
            else ""
        ),
        f"Scene context: {scene_context}." if scene_context else "",
        f"Immediate conflict: {conflict}." if conflict else "",
        f"End-state turn: {turn}." if turn else "",
        (
            "CAMERA AND PERFORMANCE: one motivated camera idea, physically plausible "
            "blocking, restrained reactions, stable geography, readable hands and object "
            "positions, and no actor posing for the audience."
        ),
        f"AUDIO PLAN: {audio_instruction}",
        continuity,
        retake,
        f"TGRM REPAIR: {repair_suffix}" if repair_suffix else "",
        (
            "Finish on a concrete action, eye line, or object position that can match the "
            "next clip. One continuous shot. No titles, captions, logos, watermarks, or "
            "unrequested text."
        ),
    ]
    prompt = " ".join(item for item in pieces if item)
    if isinstance(shot, dict):
        shot["shotBlueprint"] = blueprint
        shot["negativePrompt"] = render_negative_prompt(state, shot)
        shot["generateAudioPlanned"] = generate_audio
        shot["promptContractVersion"] = 1
    return prompt[:3500]


def prompt_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _runtime_variant_shot(
    shot: dict[str, Any],
    *,
    continuity_used: bool,
) -> dict[str, Any]:
    copy_shot = copy.deepcopy(shot)
    copy_shot["continuityUsed"] = bool(continuity_used)
    copy_shot.pop("transitionRetake", None)
    return copy_shot


def build_prompt_ledger(
    state: dict[str, Any],
    queue: dict[str, Any],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for raw_shot in queue.get("shots") or []:
        if not isinstance(raw_shot, dict):
            continue
        source = raw_shot.get("sourceScene") or {}
        scene = _scene_by_number(state, int(source.get("number", 1) or 1))
        without_shot = _runtime_variant_shot(raw_shot, continuity_used=False)
        with_shot = _runtime_variant_shot(raw_shot, continuity_used=True)
        without = render_directed_prompt(state, scene, without_shot)
        with_continuity = render_directed_prompt(state, scene, with_shot)
        negative = render_negative_prompt(state, raw_shot)
        blueprint = select_shot_blueprint(state, scene, raw_shot)
        entries.append(
            {
                "shotId": str(raw_shot.get("id") or ""),
                "order": int(raw_shot.get("order", 0) or 0),
                "scene": int(source.get("number", 1) or 1),
                "chapter": int(source.get("chapter", 1) or 1),
                "segment": int(raw_shot.get("segment", 1) or 1),
                "blueprint": blueprint,
                "promptWithoutContinuity": without,
                "promptWithContinuity": with_continuity,
                "promptHashWithoutContinuity": prompt_hash(without),
                "promptHashWithContinuity": prompt_hash(with_continuity),
                "negativePrompt": negative,
                "negativePromptHash": prompt_hash(negative),
            }
        )
    payload = {
        "schemaVersion": 1,
        "promptContractVersion": 1,
        "audioStrategy": normalize_shot_direction(
            state.get("shotDirection")
        )["audioStrategy"],
        "entries": entries,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    payload["ledgerHash"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return payload


def verify_ledger_hash(ledger: dict[str, Any]) -> bool:
    if not isinstance(ledger, dict):
        return False
    expected = str(ledger.get("ledgerHash") or "")
    payload = {key: value for key, value in ledger.items() if key != "ledgerHash"}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return bool(expected) and expected == hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()


def _ledger_entry(
    ledger: dict[str, Any], shot_id: str
) -> dict[str, Any] | None:
    for item in ledger.get("entries") or []:
        if isinstance(item, dict) and str(item.get("shotId") or "") == shot_id:
            return item
    return None


def enforce_prompt_ledger(
    state: dict[str, Any],
    scene: dict[str, Any],
    shot: dict[str, Any],
    repair: dict[str, Any] | None,
    current_prompt: str,
    current_negative: str,
) -> tuple[str, str]:
    direction = normalize_shot_direction(state.get("shotDirection"))
    if not direction.get("enforcePromptLedger"):
        return current_prompt, current_negative
    ledger = direction.get("approvedPromptLedger") or {}
    if not verify_ledger_hash(ledger):
        raise ShotDirectorError(
            "The approved prompt ledger is missing or its hash is invalid"
        )
    if (
        direction.get("approvedLedgerHash")
        and direction["approvedLedgerHash"] != ledger.get("ledgerHash")
    ):
        raise ShotDirectorError(
            "The approved prompt-ledger hash does not match the supplied ledger"
        )
    shot_id = str(shot.get("id") or "")
    entry = _ledger_entry(ledger, shot_id)
    if entry is None:
        raise ShotDirectorError(
            f"No approved prompt-ledger entry exists for {shot_id or 'this shot'}"
        )
    baseline_shot = copy.deepcopy(shot)
    baseline_shot.pop("transitionRetake", None)
    baseline = render_directed_prompt(state, scene, baseline_shot, repair=None)
    continuity = bool(shot.get("continuityUsed"))
    expected_hash = str(
        entry.get(
            "promptHashWithContinuity"
            if continuity
            else "promptHashWithoutContinuity"
        )
        or ""
    )
    if prompt_hash(baseline) != expected_hash:
        raise ShotDirectorError(
            f"The runtime prompt for {shot_id} drifted from the approved preview"
        )
    if prompt_hash(current_negative) != str(entry.get("negativePromptHash") or ""):
        raise ShotDirectorError(
            f"The runtime negative prompt for {shot_id} drifted from the approved preview"
        )
    if repair or isinstance(shot.get("transitionRetake"), dict):
        return current_prompt, current_negative
    locked_prompt = str(
        entry.get(
            "promptWithContinuity"
            if continuity
            else "promptWithoutContinuity"
        )
        or ""
    )
    return locked_prompt, str(entry.get("negativePrompt") or current_negative)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", str(value or "").casefold())
        if len(token) > 2 and token not in COMMON_PROMPT_WORDS
    }


def _jaccard(first: str, second: str) -> float:
    a, b = _tokens(first), _tokens(second)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def audit_prompt_set(
    ledger: dict[str, Any],
    shot_direction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = normalize_shot_direction(shot_direction)
    entries = [
        item for item in ledger.get("entries") or [] if isinstance(item, dict)
    ]
    signatures: list[str] = []
    missing = 0
    overflows: list[dict[str, Any]] = []
    for item in entries:
        blueprint = item.get("blueprint") or {}
        signature = " ".join(
            [
                str(blueprint.get("type") or ""),
                str(blueprint.get("description") or ""),
                str(blueprint.get("alternateCoverage") or ""),
                str(blueprint.get("override") or ""),
            ]
        ).strip()
        signatures.append(signature)
        if not str(blueprint.get("description") or "").strip():
            missing += 1
        if cfg["audioStrategy"] == "native_dialogue":
            spoken = _dialogue_seconds(str(blueprint.get("dialogue") or ""))
            available = max(
                0.5, float(blueprint.get("durationSeconds", 8) or 8) - 0.75
            )
            if spoken > available:
                overflows.append(
                    {
                        "shotId": item.get("shotId"),
                        "order": item.get("order"),
                        "estimatedSpeechSeconds": spoken,
                        "availableSeconds": round(available, 3),
                    }
                )
    unique = len({prompt_hash(item) for item in signatures if item})
    distinct_ratio = unique / len(signatures) if signatures else 0.0
    comparisons: list[dict[str, Any]] = []
    max_similarity = 0.0
    for previous, current, previous_item, current_item in zip(
        signatures,
        signatures[1:],
        entries,
        entries[1:],
    ):
        score = _jaccard(previous, current)
        max_similarity = max(max_similarity, score)
        if score > cfg["maximumPromptSimilarity"]:
            comparisons.append(
                {
                    "fromOrder": previous_item.get("order"),
                    "toOrder": current_item.get("order"),
                    "similarity": round(score, 4),
                }
            )
    exact_duplicates = max(0, len(signatures) - unique)
    passed = (
        bool(entries)
        and missing == 0
        and exact_duplicates == 0
        and distinct_ratio >= cfg["minimumDistinctShotRatio"]
        and not comparisons
        and not overflows
    )
    blocking = bool(cfg["coverageGate"]) and not passed
    findings: list[str] = []
    if missing:
        findings.append(f"{missing} clips have no concrete shot objective.")
    if exact_duplicates:
        findings.append(
            f"{exact_duplicates} clip plans repeat an identical shot contract."
        )
    if comparisons:
        findings.append(
            f"{len(comparisons)} consecutive clip pairs are too visually repetitive."
        )
    if distinct_ratio < cfg["minimumDistinctShotRatio"]:
        findings.append(
            "The distinct-shot ratio is below the configured coverage threshold."
        )
    if overflows:
        findings.append(
            f"{len(overflows)} native-dialogue cues are too long for their clips."
        )
    if not findings:
        findings.append(
            "Every planned clip has a distinct shot objective and fits the selected audio strategy."
        )
    return {
        "passed": passed,
        "blocking": blocking,
        "metrics": {
            "plannedShots": len(entries),
            "distinctShots": unique,
            "distinctShotRatio": round(distinct_ratio, 4),
            "maximumConsecutiveSimilarity": round(max_similarity, 4),
            "allowedSimilarity": cfg["maximumPromptSimilarity"],
            "minimumDistinctShotRatio": cfg["minimumDistinctShotRatio"],
            "missingBlueprints": missing,
            "exactDuplicates": exact_duplicates,
            "repetitivePairs": len(comparisons),
            "dialogueOverflows": len(overflows),
        },
        "repetitivePairs": comparisons,
        "dialogueOverflows": overflows,
        "findings": findings,
    }


def prompt_ledger_rows(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in ledger.get("entries") or []:
        if not isinstance(item, dict):
            continue
        blueprint = item.get("blueprint") or {}
        rows.append(
            {
                "Clip": item.get("order"),
                "Scene": item.get("scene"),
                "Segment": item.get("segment"),
                "Shot type": str(blueprint.get("type") or "").title(),
                "Objective": blueprint.get("description"),
                "Dialogue strategy": ledger.get("audioStrategy"),
                "Prompt hash": str(
                    item.get("promptHashWithContinuity") or ""
                )[:12],
                "Negative hash": str(item.get("negativePromptHash") or "")[:12],
            }
        )
    return rows


__all__ = [
    "AUDIO_STRATEGIES",
    "DEFAULT_AUDIO_STRATEGY",
    "ShotDirectorError",
    "audit_prompt_set",
    "build_prompt_ledger",
    "enforce_prompt_ledger",
    "normalize_shot_direction",
    "parse_shot_override_text",
    "prompt_hash",
    "prompt_ledger_rows",
    "render_directed_prompt",
    "render_negative_prompt",
    "select_shot_blueprint",
    "verify_ledger_hash",
]
