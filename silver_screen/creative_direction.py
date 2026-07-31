"""Creative-direction contracts, anti-cliche gates, and script polishing.

Silver-Screen remains deterministic by default. This module gives the operator
explicit control over realism, dialogue, performance, camera language, pacing,
humor, melodrama, exposition, and scene-level prompt overrides. It also provides
an offline quality gate before any paid video request is authorized.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any


PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "grounded_prestige": {
        "label": "Grounded prestige - recommended",
        "medium": "photorealistic live action",
        "realism": "grounded",
        "dialogueStyle": "subtextual",
        "performanceStyle": "restrained",
        "cameraStyle": "classical handheld",
        "pacing": "deliberate",
        "colorLanguage": "motivated practical light, natural skin texture, controlled contrast",
        "humorLevel": 5,
        "melodramaLevel": 5,
        "expositionLevel": 10,
        "clicheTolerance": "none",
        "minimumScriptScore": 82,
        "minimumPromptScore": 82,
        "globalVisualDirection": (
            "Premium grounded drama. Use physically plausible movement, lived-in locations, "
            "motivated practical lighting, restrained acting, precise blocking, and shots that "
            "feel photographed rather than advertised. Let behavior carry the meaning."
        ),
        "avoid": [
            "theatrical posing",
            "superhero stance",
            "glossy commercial lighting",
            "random slow motion",
            "gratuitous lens flare",
            "motivational speech",
            "obvious moral lesson",
            "characters stating the theme",
            "melodramatic reaction shots",
            "generic inspirational music-video imagery",
        ],
    },
    "modern_spy_thriller": {
        "label": "Modern spy thriller",
        "medium": "photorealistic live action",
        "realism": "grounded",
        "dialogueStyle": "sharp minimal",
        "performanceStyle": "controlled",
        "cameraStyle": "elegant anamorphic",
        "pacing": "measured tension",
        "colorLanguage": "deep blacks, practical city light, restrained cool-warm contrast",
        "humorLevel": 3,
        "melodramaLevel": 3,
        "expositionLevel": 8,
        "clicheTolerance": "none",
        "minimumScriptScore": 84,
        "minimumPromptScore": 84,
        "globalVisualDirection": (
            "Contemporary prestige espionage thriller. Elegant but plausible locations, quiet "
            "threat, disciplined framing, controlled camera movement, tailored wardrobe, and "
            "minimal dialogue with subtext. No parody and no exaggerated action-movie posing."
        ),
        "avoid": [
            "campy spy gadgets",
            "villain monologue",
            "one-liner after violence",
            "explosion for spectacle",
            "superhero movement",
            "parody tuxedo posing",
            "generic luxury commercial",
            "characters explaining the mission to each other",
        ],
    },
    "naturalistic_drama": {
        "label": "Naturalistic adult drama",
        "medium": "photorealistic live action",
        "realism": "documentary naturalism",
        "dialogueStyle": "conversational",
        "performanceStyle": "underplayed",
        "cameraStyle": "observational handheld",
        "pacing": "patient",
        "colorLanguage": "available light, neutral color, honest texture",
        "humorLevel": 8,
        "melodramaLevel": 2,
        "expositionLevel": 5,
        "clicheTolerance": "none",
        "minimumScriptScore": 84,
        "minimumPromptScore": 80,
        "globalVisualDirection": (
            "Naturalistic adult drama. Imperfect pauses, practical behavior, ordinary rooms, "
            "subtle expressions, and dialogue that sounds overheard rather than written. Avoid "
            "visual grandstanding and let silence remain in the scene."
        ),
        "avoid": [
            "speechifying",
            "tearful confession on cue",
            "perfectly composed commercial interiors",
            "characters naming their emotions",
            "sentimental montage",
            "obvious symbolism",
        ],
    },
    "dark_psychological": {
        "label": "Dark psychological thriller",
        "medium": "photorealistic live action",
        "realism": "grounded heightened realism",
        "dialogueStyle": "minimal unsettling",
        "performanceStyle": "restrained tension",
        "cameraStyle": "controlled slow-burn",
        "pacing": "slow pressure",
        "colorLanguage": "low-key motivated light, muted color, dense shadow detail",
        "humorLevel": 0,
        "melodramaLevel": 4,
        "expositionLevel": 5,
        "clicheTolerance": "none",
        "minimumScriptScore": 84,
        "minimumPromptScore": 84,
        "globalVisualDirection": (
            "Serious psychological thriller. Build pressure through framing, withheld information, "
            "small behavioral changes, and precise sound perspective. Keep the threat plausible and "
            "avoid horror cliches, jump-scare staging, and exaggerated facial acting."
        ),
        "avoid": [
            "cheap jump scare",
            "creepy child trope",
            "mirror scare",
            "villain grin",
            "expository nightmare",
            "sudden screaming",
            "generic ominous hallway",
        ],
    },
    "premium_animation": {
        "label": "Premium cinematic animation",
        "medium": "high-end animated feature film",
        "realism": "stylized physical coherence",
        "dialogueStyle": "character specific",
        "performanceStyle": "expressive but controlled",
        "cameraStyle": "feature animation cinematography",
        "pacing": "confident",
        "colorLanguage": "designed color script, dimensional lighting, consistent materials",
        "humorLevel": 25,
        "melodramaLevel": 10,
        "expositionLevel": 12,
        "clicheTolerance": "low",
        "minimumScriptScore": 76,
        "minimumPromptScore": 80,
        "globalVisualDirection": (
            "Premium feature animation with consistent character design, dimensional light, clean "
            "silhouettes, specific acting choices, and humor based on character behavior rather than "
            "random slapstick. Keep the emotional moments sincere and visually restrained."
        ),
        "avoid": [
            "cheap television animation",
            "random slapstick",
            "rubber-hose deformation",
            "constant mugging",
            "children's-commercial pacing",
            "generic cute reaction",
            "visual noise",
        ],
    },
    "stylized_genre": {
        "label": "Stylized genre film",
        "medium": "stylized cinematic live action",
        "realism": "heightened",
        "dialogueStyle": "stylized concise",
        "performanceStyle": "controlled expressive",
        "cameraStyle": "graphic composed",
        "pacing": "dynamic",
        "colorLanguage": "intentional palette, graphic contrast, coherent production design",
        "humorLevel": 15,
        "melodramaLevel": 20,
        "expositionLevel": 15,
        "clicheTolerance": "low",
        "minimumScriptScore": 74,
        "minimumPromptScore": 78,
        "globalVisualDirection": (
            "A deliberate stylized genre film. Every heightened choice must be coherent with the "
            "same visual rules, production design, camera grammar, and performance level. Avoid "
            "generic spectacle and unsupported tonal shifts."
        ),
        "avoid": [
            "random style changes",
            "generic blockbuster imagery",
            "unmotivated camera spin",
            "visual clutter",
            "empty spectacle",
        ],
    },
    "custom": {
        "label": "Custom direction",
        "medium": "photorealistic live action",
        "realism": "grounded",
        "dialogueStyle": "naturalistic",
        "performanceStyle": "restrained",
        "cameraStyle": "classical",
        "pacing": "balanced",
        "colorLanguage": "motivated cinematic lighting",
        "humorLevel": 10,
        "melodramaLevel": 10,
        "expositionLevel": 10,
        "clicheTolerance": "low",
        "minimumScriptScore": 78,
        "minimumPromptScore": 78,
        "globalVisualDirection": "Grounded professional filmmaking with specific behavior and coherent visual rules.",
        "avoid": [],
    },
}

PROFILE_LABELS = {key: str(value["label"]) for key, value in PROFILE_PRESETS.items()}
SCRIPT_SOURCES = {"generated", "authored"}
CLICHE_LEVELS = {"none", "low", "standard"}

CORNY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bthe world seems to wait\b", "generic dramatic narration"),
    (r"\bhistory turns on this threshold\b", "grandiose stock phrase"),
    (r"\ba private choice becomes a public reckoning\b", "grandiose stock phrase"),
    (r"\bi can verify the .{0,60}cost is zero\b", "expository dialogue"),
    (r"\bwhich failure you are willing to own\b", "theme-stating dialogue"),
    (r"\bmust confront .{0,100} before\b", "generic logline construction"),
    (r"\blast chance to rebuild\b", "generic inspirational stakes"),
    (r"\bdamaged future becomes permanent\b", "abstract stakes"),
    (r"\bprotecting each other matters more\b", "stated moral"),
    (r"\bthe real .{0,25} was inside us\b", "stated moral"),
    (r"\bwe can still fix this\b", "generic hopeful line"),
    (r"\bthis ends now\b", "generic action line"),
    (r"\byou have no idea what you have done\b", "generic threat"),
    (r"\bit was never about .{0,35}\b", "generic reveal"),
    (r"\bi am not afraid anymore\b", "generic character declaration"),
    (r"\beverything changes tonight\b", "trailer cliche"),
)

ABSTRACT_MORAL_WORDS = {
    "truth",
    "destiny",
    "fate",
    "soul",
    "reckoning",
    "redemption",
    "obligation",
    "future",
    "sacrifice",
    "purpose",
    "legacy",
    "fracture",
    "repair",
    "cost",
}

GROUND_DIALOGUE: tuple[tuple[str, str], ...] = (
    ("How long has it been like this?", "Long enough that someone stopped asking."),
    ("You moved it.", "I moved what they could see."),
    ("Say it plainly.", "Not in this room."),
    ("That is not an answer.", "It is the one I can give you here."),
    ("Who else knows?", "Enough people to make the silence look organized."),
    ("We leave now.", "Then stop watching the door."),
    ("You expected me.", "I expected somebody. I hoped it was not you."),
    ("What did you change?", "The part that would have failed first."),
)

SPY_DIALOGUE: tuple[tuple[str, str], ...] = (
    ("Were you followed?", "I was expected."),
    ("The room is clean.", "That is what worries me."),
    ("You changed the route.", "The route changed first."),
    ("Who signed off?", "Nobody who will admit it tomorrow."),
    ("You brought a weapon.", "I brought an exit."),
    ("We have six minutes.", "Then use five."),
)

DRAMA_DIALOGUE: tuple[tuple[str, str], ...] = (
    ("You could have called.", "I thought about it."),
    ("Are you staying?", "I have not decided what that means."),
    ("You look tired.", "I did not come here to sleep."),
    ("That is not how I remember it.", "I know."),
    ("Do you want me to leave?", "I want you to stop asking me to decide for you."),
)

ANIMATION_DIALOGUE: tuple[tuple[str, str], ...] = (
    ("Was that part of the entrance?", "It is now."),
    ("You said this would be quiet.", "I said I would try."),
    ("Everyone is looking.", "Then give them something worth looking at."),
    ("That was your plan?", "It was the affordable version."),
    ("We are improvising.", "You are. I am surviving it."),
)


def _clamp_int(value: Any, default: int, low: int = 0, high: int = 100) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def normalize_avoid_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[\n,;]+", value)
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        parts = []
    result: list[str] = []
    for item in parts:
        cleaned = _clean_text(item, 120).strip(" .")
        if cleaned and cleaned.casefold() not in {entry.casefold() for entry in result}:
            result.append(cleaned)
        if len(result) >= 40:
            break
    return result


def normalize_scene_overrides(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        try:
            key = str(max(1, int(raw_key)))
        except (TypeError, ValueError):
            continue
        text = _clean_text(raw_value, 1800)
        if text:
            result[key] = text
        if len(result) >= 64:
            break
    return result


def parse_scene_override_text(value: str) -> dict[str, str]:
    """Parse user-friendly `1: direction` or `[1] direction` lines."""

    result: dict[str, str] = {}
    current: str | None = None
    for raw in str(value or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^(?:scene\s*)?\[?(\d{1,3})\]?\s*[:=-]\s*(.*)$", line, re.I)
        if match:
            current = str(max(1, int(match.group(1))))
            result[current] = _clean_text(match.group(2), 1800)
        elif current:
            result[current] = _clean_text(f"{result[current]} {line}", 1800)
    return result


def normalize_creative_direction(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    profile = str(raw.get("profile") or "grounded_prestige").strip().lower()
    if profile not in PROFILE_PRESETS:
        profile = "grounded_prestige"
    preset = copy.deepcopy(PROFILE_PRESETS[profile])
    source = str(raw.get("scriptSource") or "generated").strip().lower()
    if source not in SCRIPT_SOURCES:
        source = "generated"
    cliche = str(raw.get("clicheTolerance") or preset["clicheTolerance"]).strip().lower()
    if cliche not in CLICHE_LEVELS:
        cliche = str(preset["clicheTolerance"])
    avoid = normalize_avoid_list([*preset.get("avoid", []), *normalize_avoid_list(raw.get("avoid"))])
    overrides = normalize_scene_overrides(raw.get("scenePromptOverrides"))
    return {
        "schemaVersion": 1,
        "profile": profile,
        "profileLabel": str(preset["label"]),
        "scriptSource": source,
        "medium": _clean_text(raw.get("medium") or preset["medium"], 160),
        "realism": _clean_text(raw.get("realism") or preset["realism"], 160),
        "dialogueStyle": _clean_text(raw.get("dialogueStyle") or preset["dialogueStyle"], 160),
        "performanceStyle": _clean_text(raw.get("performanceStyle") or preset["performanceStyle"], 220),
        "cameraStyle": _clean_text(raw.get("cameraStyle") or preset["cameraStyle"], 220),
        "pacing": _clean_text(raw.get("pacing") or preset["pacing"], 120),
        "colorLanguage": _clean_text(raw.get("colorLanguage") or preset["colorLanguage"], 300),
        "humorLevel": _clamp_int(raw.get("humorLevel"), int(preset["humorLevel"])),
        "melodramaLevel": _clamp_int(raw.get("melodramaLevel"), int(preset["melodramaLevel"])),
        "expositionLevel": _clamp_int(raw.get("expositionLevel"), int(preset["expositionLevel"])),
        "clicheTolerance": cliche,
        "globalVisualDirection": _clean_text(
            raw.get("globalVisualDirection") or preset["globalVisualDirection"], 2400
        ),
        "directorNotes": _clean_text(raw.get("directorNotes"), 2400),
        "avoid": avoid,
        "scenePromptOverrides": overrides,
        "strictGate": bool(raw.get("strictGate", True)),
        "minimumScriptScore": _clamp_int(
            raw.get("minimumScriptScore"), int(preset["minimumScriptScore"]), 50, 100
        ),
        "minimumPromptScore": _clamp_int(
            raw.get("minimumPromptScore"), int(preset["minimumPromptScore"]), 50, 100
        ),
        "enforceApprovalGates": bool(raw.get("enforceApprovalGates", False)),
        "approvals": {
            "scriptApproved": bool((raw.get("approvals") or {}).get("scriptApproved"))
            if isinstance(raw.get("approvals"), dict)
            else False,
            "promptsApproved": bool((raw.get("approvals") or {}).get("promptsApproved"))
            if isinstance(raw.get("approvals"), dict)
            else False,
            "budgetApproved": bool((raw.get("approvals") or {}).get("budgetApproved"))
            if isinstance(raw.get("approvals"), dict)
            else False,
        },
    }


def creative_fingerprint(direction: dict[str, Any]) -> str:
    normalized = normalize_creative_direction(direction)
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _characters_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in state.get("characters") or []
        if isinstance(item, dict)
    }


def _dialogue_library(profile: str) -> tuple[tuple[str, str], ...]:
    if profile == "modern_spy_thriller":
        return SPY_DIALOGUE
    if profile == "naturalistic_drama":
        return DRAMA_DIALOGUE
    if profile == "premium_animation":
        return ANIMATION_DIALOGUE
    return GROUND_DIALOGUE


def _scene_focus(scene: dict[str, Any], state: dict[str, Any], index: int) -> str:
    anchors = [str(item) for item in scene.get("continuityAnchors") or [] if str(item).strip()]
    if anchors:
        return anchors[index % len(anchors)]
    terms = [str(item) for item in (state.get("storyBible") or {}).get("themeTerms") or []]
    return terms[index % len(terms)] if terms else "the evidence"


def _ground_scene(scene: dict[str, Any], state: dict[str, Any], direction: dict[str, Any], index: int) -> None:
    characters = _characters_by_id(state)
    ids = [str(item) for item in scene.get("characters") or []]
    lead = characters.get(ids[0], {}) if ids else {}
    other = characters.get(ids[1], {}) if len(ids) > 1 else {}
    lead_name = str(lead.get("name") or "The lead")
    other_name = str(other.get("name") or "The other person")
    location = str(scene.get("location") or scene.get("slugline") or "the location").lower()
    focus = _scene_focus(scene, state, index)
    motif = str((state.get("storyBible") or {}).get("motif") or "a small physical detail")
    premise = str(state.get("premise") or "").rstrip(".")
    profile = str(direction.get("profile") or "grounded_prestige")

    action_variants = (
        (
            f"The scene is already in motion when {lead_name} enters {location}. "
            f"{lead_name} clocks {other_name}, then the detail connected to {motif}. "
            f"A practical obstacle blocks the obvious route. Instead of explaining the situation, "
            f"{lead_name} tests one concrete action related to {focus}. The result is visible in the "
            f"room before either person comments on it. {other_name} changes position, not tone, and "
            f"that small movement reveals who currently has leverage. The exchange stays quiet and "
            f"specific. Nobody states the theme, summarizes the premise, or performs for the camera."
        ),
        (
            f"At {location}, {lead_name} arrives a few seconds too late to control the room. "
            f"The physical evidence tied to {motif} is present but easy to miss. {other_name} keeps "
            f"working while they speak, forcing {lead_name} to decide whether to interrupt or observe. "
            f"A concrete test involving {focus} produces an imperfect answer. The tension comes from "
            f"what both characters avoid saying about this situation: {premise[:150]}. The camera "
            f"stays motivated by behavior and holds long enough to catch the change in their decisions."
        ),
        (
            f"{lead_name} and {other_name} share the frame in {location}, but not the same objective. "
            f"A worn detail associated with {motif} gives the scene a history without a flashback. "
            f"{lead_name} checks {focus} in a direct, physical way. {other_name} allows the test, then "
            f"quietly removes one option from the room. The turn lands through blocking, eye line, and "
            f"a changed object position rather than a speech. Performances remain restrained and the "
            f"scene ends on a decision that can be carried into the next shot."
        ),
    )
    dialogue_pairs = _dialogue_library(profile)
    first, second = dialogue_pairs[index % len(dialogue_pairs)]
    if int(direction.get("expositionLevel", 10) or 10) >= 60:
        first = f"What does {focus} prove?"
        second = "Only that somebody expected us to look somewhere else."
    if int(direction.get("humorLevel", 0) or 0) >= 45 and profile != "naturalistic_drama":
        first, second = ANIMATION_DIALOGUE[index % len(ANIMATION_DIALOGUE)]

    scene["action"] = action_variants[index % len(action_variants)]
    scene["summary"] = (
        f"{lead_name} tests a concrete lead involving {focus}; {other_name} quietly changes the available options."
    )
    scene["conflict"] = (
        f"{lead_name} needs a usable answer about {focus}; {other_name} needs to control when and where that answer becomes visible."
    )
    scene["turn"] = (
        f"A physical detail tied to {motif} changes position or meaning, revealing that the next move has already been anticipated."
    )
    shots = [item for item in scene.get("shots") or [] if isinstance(item, dict)]
    if not shots:
        shots = [{"id": f"shot_{index + 1}_1"}, {"id": f"shot_{index + 1}_2"}, {"id": f"shot_{index + 1}_3"}, {"id": f"shot_{index + 1}_4"}]
    while len(shots) < 4:
        shots.append({"id": f"shot_{index + 1}_{len(shots) + 1}"})
    shots[0].update(
        type="establishing",
        description=(
            f"A restrained establishing view of {location}. Reveal the working geography, practical light sources, and {motif}; no spectacle."
        ),
        dialogue=None,
        durationSec=float(shots[0].get("durationSec", 4.0) or 4.0),
    )
    shots[1].update(
        type="medium",
        description=(
            f"Hold {lead_name} and {other_name} in one motivated composition. Let blocking and eye line carry the power shift."
        ),
        dialogue=f'{lead_name}: "{first}"',
        durationSec=float(shots[1].get("durationSec", 5.0) or 5.0),
    )
    shots[2].update(
        type="detail",
        description=(
            f"Cut only when the evidence connected to {focus} changes. Keep hands, object position, and screen direction physically coherent."
        ),
        dialogue=f'{other_name}: "{second}"',
        durationSec=float(shots[2].get("durationSec", 4.0) or 4.0),
    )
    shots[3].update(
        type="transition",
        description=(
            "End on a concrete action, glance, or object movement that provides a usable match point for the next clip."
        ),
        dialogue=None,
        durationSec=float(shots[3].get("durationSec", 3.0) or 3.0),
    )
    scene["shots"] = shots
    scene["durationSec"] = sum(float(item.get("durationSec", 0) or 0) for item in shots)


def _script_segments(script: str) -> list[dict[str, str]]:
    heading_re = re.compile(r"^(?:INT\.?|EXT\.?|INT\.?/EXT\.?|I/E\.?|SCENE\s+\d+)\b", re.I)
    segments: list[dict[str, str]] = []
    heading = ""
    body: list[str] = []
    for raw in str(script or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if heading_re.match(line.strip()):
            if heading or any(item.strip() for item in body):
                segments.append({"heading": heading, "body": "\n".join(body).strip()})
            heading = line.strip()
            body = []
        else:
            body.append(line)
    if heading or any(item.strip() for item in body):
        segments.append({"heading": heading, "body": "\n".join(body).strip()})
    if len(segments) <= 1:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n+", str(script or "")) if item.strip()]
        if len(paragraphs) > 1:
            segments = [{"heading": "", "body": item} for item in paragraphs]
    return segments or [{"heading": "", "body": str(script or "").strip()}]


def _split_longest_segment(segments: list[dict[str, str]]) -> bool:
    if not segments:
        return False
    index = max(range(len(segments)), key=lambda item: len(segments[item].get("body", "")))
    segment = segments[index]
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", segment.get("body", "")) if item.strip()]
    if len(sentences) < 2:
        words = segment.get("body", "").split()
        if len(words) < 20:
            return False
        cut = len(words) // 2
        left, right = " ".join(words[:cut]), " ".join(words[cut:])
    else:
        cut = max(1, len(sentences) // 2)
        left, right = " ".join(sentences[:cut]), " ".join(sentences[cut:])
    segments[index : index + 1] = [
        {"heading": segment.get("heading", ""), "body": left},
        {"heading": "", "body": right},
    ]
    return True


def _fit_segments(segments: list[dict[str, str]], count: int) -> list[dict[str, str]]:
    items = [dict(item) for item in segments if item.get("heading") or item.get("body")]
    count = max(1, count)
    while len(items) < count and _split_longest_segment(items):
        pass
    if len(items) < count:
        last = items[-1] if items else {"heading": "", "body": "A quiet reaction changes the next decision."}
        while len(items) < count:
            items.append(
                {
                    "heading": "",
                    "body": (
                        f"A brief reaction scene follows the previous action. The physical consequence remains visible. "
                        f"{last.get('body', '')[:240]}"
                    ),
                }
            )
    if len(items) > count:
        groups: list[list[dict[str, str]]] = [[] for _ in range(count)]
        for index, item in enumerate(items):
            group_index = min(count - 1, index * count // len(items))
            groups[group_index].append(item)
        items = [
            {
                "heading": next((part.get("heading", "") for part in group if part.get("heading")), ""),
                "body": "\n\n".join(part.get("body", "") for part in group if part.get("body")),
            }
            for group in groups
        ]
    return items[:count]


def _authored_dialogue(body: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    pending_speaker: str | None = None
    for raw in str(body or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        colon = re.match(r"^([A-Za-z0-9 ._'’-]{1,60})\s*:\s*(.+)$", line)
        if colon:
            result.append((" ".join(colon.group(1).split()), " ".join(colon.group(2).split()).strip('"“”')))
            pending_speaker = None
            continue
        if line.isupper() and 1 <= len(line.split()) <= 5 and len(line) <= 60:
            pending_speaker = " ".join(line.split())
            continue
        if pending_speaker:
            result.append((pending_speaker, " ".join(line.split()).strip('"“”')))
            pending_speaker = None
    return result


def _apply_authored_script(state: dict[str, Any], script: str, direction: dict[str, Any]) -> None:
    scenes = [item for item in state.get("scenes") or [] if isinstance(item, dict)]
    segments = _fit_segments(_script_segments(script), len(scenes))
    characters = _characters_by_id(state)
    for index, (scene, segment) in enumerate(zip(scenes, segments)):
        heading = _clean_text(segment.get("heading"), 180)
        body = str(segment.get("body") or "").strip()
        dialogue = _authored_dialogue(body)
        action_lines = []
        for raw in body.split("\n"):
            line = raw.strip()
            if not line:
                continue
            if re.match(r"^[A-Za-z0-9 ._'’-]{1,60}\s*:\s*.+$", line):
                continue
            if line.isupper() and len(line.split()) <= 5:
                continue
            action_lines.append(line)
        action = _clean_text(" ".join(action_lines) or body, 2400)
        if heading:
            scene["slugline"] = heading
        scene["action"] = action or "The characters complete the action described in the authored script."
        scene["summary"] = _clean_text(action or body, 300)
        scene["conflict"] = _clean_text(
            f"The authored scene places incompatible objectives in the same physical space: {action or body}", 500
        )
        scene["turn"] = _clean_text(
            "The final authored action or line changes what the next scene can safely assume.", 400
        )
        scene["authoredExcerpt"] = body[:4000]
        ids = [str(item) for item in scene.get("characters") or []]
        default_names = [str(characters.get(item, {}).get("name") or "Character") for item in ids]
        first_dialogue = dialogue[0] if dialogue else ((default_names[0] if default_names else "Character"), "")
        second_dialogue = dialogue[1] if len(dialogue) > 1 else ((default_names[1] if len(default_names) > 1 else first_dialogue[0]), "")
        shots = [item for item in scene.get("shots") or [] if isinstance(item, dict)]
        while len(shots) < 4:
            shots.append({"id": f"authored_{index + 1}_{len(shots) + 1}", "durationSec": 4.0})
        shots[0].update(
            type="establishing",
            description=_clean_text(
                f"Establish the authored location and exact physical circumstances. {action}", 800
            ),
            dialogue=None,
        )
        shots[1].update(
            type="performance",
            description="Stage the first authored beat with restrained, physically specific acting.",
            dialogue=(f'{first_dialogue[0]}: "{first_dialogue[1]}"' if first_dialogue[1] else None),
        )
        shots[2].update(
            type="reaction",
            description="Hold the reaction or physical consequence that changes the scene.",
            dialogue=(f'{second_dialogue[0]}: "{second_dialogue[1]}"' if second_dialogue[1] else None),
        )
        shots[3].update(
            type="transition",
            description="End on a usable visual match point from the exact authored action.",
            dialogue=None,
        )
        scene["shots"] = shots
        scene["durationSec"] = sum(float(item.get("durationSec", 4.0) or 4.0) for item in shots)
    exact = str(script or "").strip()
    if "FADE OUT" not in exact.upper():
        exact += "\n\nFADE OUT."
    if "THE END" not in exact.upper():
        exact += "\n\nTHE END"
    state["authoredScript"] = exact
    state["script"] = exact + "\n"
    state["scriptSource"] = "authored"


def apply_scene_overrides(state: dict[str, Any], direction: dict[str, Any]) -> None:
    overrides = normalize_scene_overrides(direction.get("scenePromptOverrides"))
    for scene in state.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        key = str(int(scene.get("number", 0) or 0))
        if key in overrides:
            scene["promptOverride"] = overrides[key]


def _dialogue_texts(state: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for scene in state.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for shot in scene.get("shots") or []:
            if isinstance(shot, dict) and str(shot.get("dialogue") or "").strip():
                result.append(str(shot["dialogue"]).strip())
    return result


def audit_screenplay(
    script: str,
    *,
    direction: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = normalize_creative_direction(direction)
    text = str(script or "")
    lowered = text.lower()
    words = re.findall(r"\b[\w'-]+\b", text)
    cliche_hits: list[dict[str, str]] = []
    for pattern, label in CORNY_PATTERNS:
        match = re.search(pattern, lowered, re.I)
        if match:
            excerpt = text[max(0, match.start() - 45) : min(len(text), match.end() + 75)].replace("\n", " ")
            cliche_hits.append({"label": label, "excerpt": " ".join(excerpt.split())[:180]})
    custom_hits: list[str] = []
    for item in cfg.get("avoid") or []:
        if len(item) >= 4 and item.casefold() in lowered:
            custom_hits.append(item)
    dialogue = _dialogue_texts(state or {})
    if not dialogue:
        dialogue = re.findall(r"(?:^|\n)\s*[A-Z][A-Z0-9 ._'’-]{1,40}:\s*[^\n]+", text)
    dialogue_word_counts = [len(re.findall(r"\b[\w'-]+\b", item)) for item in dialogue]
    average_dialogue = sum(dialogue_word_counts) / len(dialogue_word_counts) if dialogue_word_counts else 0.0
    exclamations = text.count("!")
    abstract_count = sum(1 for word in words if word.casefold() in ABSTRACT_MORAL_WORDS)
    abstract_density = abstract_count / max(1, len(words))
    normalized_dialogue = [re.sub(r"\W+", " ", item.casefold()).strip() for item in dialogue]
    repeats = len(normalized_dialogue) - len(set(normalized_dialogue))

    score = 100.0
    score -= min(36.0, len(cliche_hits) * 7.0)
    score -= min(18.0, len(custom_hits) * 5.0)
    if average_dialogue > 20:
        score -= min(15.0, (average_dialogue - 20) * 1.2)
    elif average_dialogue > 14 and cfg.get("dialogueStyle") in {"subtextual", "sharp minimal", "minimal unsettling"}:
        score -= min(10.0, (average_dialogue - 14) * 1.1)
    if abstract_density > 0.035 and int(cfg.get("expositionLevel", 10) or 10) < 30:
        score -= min(18.0, (abstract_density - 0.035) * 300)
    score -= min(10.0, max(0, exclamations - 2) * 1.5)
    score -= min(12.0, repeats * 4.0)
    score = round(max(0.0, min(100.0, score)), 1)
    minimum = int(cfg.get("minimumScriptScore", 80) or 80)
    suggestions: list[str] = []
    if cliche_hits:
        suggestions.append("Replace stock declarations with a concrete action, withheld answer, or specific physical consequence.")
    if custom_hits:
        suggestions.append("The screenplay contains an item from the operator's avoid list.")
    if average_dialogue > 18:
        suggestions.append("Shorten dialogue and move information into behavior, props, blocking, or reaction shots.")
    if abstract_density > 0.035:
        suggestions.append("Replace abstract words such as truth, fate, cost, or destiny with specific people, objects, and consequences.")
    if exclamations > 2:
        suggestions.append("Reduce forced intensity. Let framing, timing, and performance create pressure.")
    if not suggestions:
        suggestions.append("The screenplay clears the configured anti-cliche and naturalism checks.")
    return {
        "score": score,
        "minimumScore": minimum,
        "passed": score >= minimum,
        "blocking": bool(cfg.get("strictGate")) and score < minimum,
        "metrics": {
            "wordCount": len(words),
            "dialogueLines": len(dialogue),
            "averageDialogueWords": round(average_dialogue, 2),
            "exclamationCount": exclamations,
            "abstractWordDensity": round(abstract_density, 4),
            "repeatedDialogueLines": repeats,
            "clicheHits": len(cliche_hits),
            "customAvoidHits": len(custom_hits),
        },
        "clicheFindings": cliche_hits,
        "customAvoidFindings": custom_hits,
        "suggestions": suggestions,
    }


def _replace_flagged_dialogue(state: dict[str, Any], direction: dict[str, Any]) -> None:
    library = _dialogue_library(str(direction.get("profile") or "grounded_prestige"))
    for scene_index, scene in enumerate(state.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        pair = library[scene_index % len(library)]
        dialogue_index = 0
        for shot in scene.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            line = str(shot.get("dialogue") or "")
            if not line:
                continue
            if any(re.search(pattern, line, re.I) for pattern, _ in CORNY_PATTERNS):
                speaker_match = re.match(r"^([^:]{1,80}):", line)
                speaker = speaker_match.group(1).strip() if speaker_match else "Character"
                replacement = pair[min(dialogue_index, 1)]
                shot["dialogue"] = f'{speaker}: "{replacement}"'
            dialogue_index += 1


def prompt_contract(
    direction: dict[str, Any] | None,
    *,
    scene: dict[str, Any] | None = None,
    shot: dict[str, Any] | None = None,
) -> str:
    cfg = normalize_creative_direction(direction)
    scene = scene or {}
    override = str(scene.get("promptOverride") or "").strip()
    pieces = [
        "CREATIVE CONTRACT:",
        f"Medium: {cfg['medium']}.",
        f"Realism: {cfg['realism']}.",
        f"Performance: {cfg['performanceStyle']}.",
        f"Camera grammar: {cfg['cameraStyle']}.",
        f"Pacing: {cfg['pacing']}.",
        f"Color and light: {cfg['colorLanguage']}.",
        f"Dialogue behavior: {cfg['dialogueStyle']}; exposition level {cfg['expositionLevel']}/100; melodrama {cfg['melodramaLevel']}/100; humor {cfg['humorLevel']}/100.",
        str(cfg.get("globalVisualDirection") or ""),
        f"Director notes: {cfg['directorNotes']}." if cfg.get("directorNotes") else "",
        (
            "SCENE-SPECIFIC DIRECTOR OVERRIDE: " + override
            if override
            else ""
        ),
        (
            "Avoid: " + "; ".join(cfg.get("avoid") or []) + "."
            if cfg.get("avoid")
            else ""
        ),
        "Use one motivated action and one readable emotional objective. Do not make the actor pose for the audience. Do not state the moral of the scene.",
    ]
    return " ".join(item for item in pieces if item)[:3400]


def negative_prompt(direction: dict[str, Any] | None) -> str:
    cfg = normalize_creative_direction(direction)
    base = [
        "text overlays",
        "subtitles",
        "logos",
        "watermarks",
        "malformed hands",
        "duplicate people",
        "flicker",
        "identity drift",
        "wardrobe drift",
        "theatrical posing",
        "generic inspirational imagery",
        "exaggerated facial acting",
        "random slow motion",
        "unmotivated camera spin",
    ]
    return ", ".join(normalize_avoid_list([*base, *(cfg.get("avoid") or [])]))[:1800]


def audit_prompt(prompt: str, direction: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = normalize_creative_direction(direction)
    text = str(prompt or "")
    lowered = text.casefold()
    concrete_terms = sum(
        lowered.count(term)
        for term in ("camera", "light", "action", "frame", "position", "wardrobe", "object", "movement")
    )
    cliche_hits = sum(1 for pattern, _ in CORNY_PATTERNS if re.search(pattern, lowered, re.I))
    custom_hits = [item for item in cfg.get("avoid") or [] if item.casefold() in lowered]
    adjective_stack = len(re.findall(r"\b(?:epic|stunning|breathtaking|incredible|amazing|glamorous|dramatic|cinematic)\b", lowered))
    score = 100.0
    score -= cliche_hits * 10
    score -= len(custom_hits) * 5
    score -= max(0, adjective_stack - 4) * 3
    if concrete_terms < 4:
        score -= 16
    if "creative contract" not in lowered:
        score -= 12
    score = round(max(0.0, min(100.0, score)), 1)
    minimum = int(cfg.get("minimumPromptScore", 80) or 80)
    return {
        "score": score,
        "minimumScore": minimum,
        "passed": score >= minimum,
        "blocking": bool(cfg.get("strictGate")) and score < minimum,
        "metrics": {
            "concreteDirectionTerms": concrete_terms,
            "clicheHits": cliche_hits,
            "customAvoidHits": len(custom_hits),
            "adjectiveStack": adjective_stack,
        },
        "customAvoidFindings": custom_hits,
    }


def apply_creative_direction(
    state: dict[str, Any],
    direction: dict[str, Any] | None = None,
    *,
    authored_script: str | None = None,
    render_screenplay_fn: Any = None,
) -> dict[str, Any]:
    """Apply an explicit creative contract to a structured film state."""

    cfg = normalize_creative_direction(direction)
    state["creativeDirection"] = cfg
    state["creativeFingerprint"] = creative_fingerprint(cfg)
    state["scriptSource"] = cfg["scriptSource"]
    state.setdefault("storyBible", {})["creativeContract"] = {
        "profile": cfg["profile"],
        "medium": cfg["medium"],
        "realism": cfg["realism"],
        "dialogueStyle": cfg["dialogueStyle"],
        "performanceStyle": cfg["performanceStyle"],
        "cameraStyle": cfg["cameraStyle"],
        "pacing": cfg["pacing"],
    }
    if cfg["scriptSource"] == "authored" and str(authored_script or "").strip():
        _apply_authored_script(state, str(authored_script), cfg)
    else:
        for index, scene in enumerate(state.get("scenes") or []):
            if isinstance(scene, dict):
                _ground_scene(scene, state, cfg, index)
        if callable(render_screenplay_fn):
            state["script"] = render_screenplay_fn(state)
    apply_scene_overrides(state, cfg)
    state["creativeQuality"] = audit_screenplay(
        str(state.get("script") or ""), direction=cfg, state=state
    )
    return state


def finalize_creative_state(state: dict[str, Any], *, render_screenplay_fn: Any = None) -> dict[str, Any]:
    """Reapply the anti-cliche gate after TGRM has changed a scene."""

    cfg = normalize_creative_direction(state.get("creativeDirection"))
    _replace_flagged_dialogue(state, cfg)
    apply_scene_overrides(state, cfg)
    if state.get("scriptSource") == "authored" and state.get("authoredScript"):
        state["script"] = str(state["authoredScript"]).rstrip() + "\n"
    elif callable(render_screenplay_fn):
        state["script"] = render_screenplay_fn(state)
    state["creativeQuality"] = audit_screenplay(
        str(state.get("script") or ""), direction=cfg, state=state
    )
    return state


def approval_gate_errors(direction: dict[str, Any] | None) -> list[str]:
    cfg = normalize_creative_direction(direction)
    if not cfg.get("enforceApprovalGates"):
        return []
    approvals = cfg.get("approvals") or {}
    errors = []
    if not approvals.get("scriptApproved"):
        errors.append("The screenplay has not been approved")
    if not approvals.get("promptsApproved"):
        errors.append("The visual prompts have not been approved")
    if not approvals.get("budgetApproved"):
        errors.append("The paid render budget has not been approved")
    return errors
