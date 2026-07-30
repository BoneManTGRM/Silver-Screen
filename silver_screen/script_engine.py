"""Deterministic story and screenplay blueprint generation.

The engine does not depend on a remote model. A brief and seed produce the same
story state every time, which makes the TGRM verification loop reproducible and
testable. External generative providers can be layered on later without changing
the state contract used by the pipeline.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .science import FORMATS, format_meta

FIRST_NAMES = [
    "Elena",
    "Marcus",
    "Sofia",
    "Kai",
    "Vera",
    "Diego",
    "Ava",
    "Rafael",
    "Luna",
    "Orion",
    "Iris",
    "Caleb",
    "Nia",
    "Jonas",
    "Mira",
    "Noah",
    "Seraph",
    "Quinn",
    "Imani",
    "Theo",
]
SURNAMES = [
    "Cross",
    "Vale",
    "Reyes",
    "Kade",
    "Morrow",
    "Solis",
    "Hart",
    "Quill",
    "Voss",
    "Navarro",
    "Ash",
    "Lane",
    "Cortez",
    "Frost",
    "Blackwood",
    "Sato",
    "Okoye",
    "Mercer",
    "Duarte",
    "Shin",
]

LOCATIONS: dict[str, list[str]] = {
    "noir": [
        "RAIN-SOAKED ALLEY",
        "SMOKY BAR",
        "DETECTIVE'S OFFICE",
        "MUNICIPAL ARCHIVE",
        "ROOFTOP",
    ],
    "scifi": [
        "NEON CORRIDOR",
        "ORBITAL DOCK",
        "DATA VAULT",
        "BRIDGE DECK",
        "GENE LAB",
        "ABANDONED RELAY",
    ],
    "drama": [
        "FAMILY KITCHEN",
        "HOSPITAL HALL",
        "EMPTY THEATER",
        "CITY BUS",
        "COMMUNITY CENTER",
    ],
    "thriller": [
        "SAFEHOUSE",
        "SUBWAY PLATFORM",
        "SERVER ROOM",
        "PARKING GARAGE",
        "BORDER CHECKPOINT",
    ],
    "fantasy": [
        "ANCIENT LIBRARY",
        "CLIFF TEMPLE",
        "MOONLIT FOREST",
        "THRONE HALL",
        "SUNKEN ROAD",
    ],
    "western": [
        "DUSTY SALOON",
        "OPEN PRAIRIE",
        "SHERIFF'S OFFICE",
        "RAIL CAMP",
        "DRY RIVERBED",
    ],
    "romance": [
        "BOOKSHOP",
        "RAINY CAFE",
        "FERRY DECK",
        "ROOFTOP GARDEN",
        "EMPTY GALLERY",
    ],
    "horror": [
        "ABANDONED WING",
        "FOGGY CEMETERY",
        "BASEMENT CORRIDOR",
        "LOCKED NURSERY",
        "FOREST SERVICE ROAD",
    ],
}

ROLE_SETS: dict[str, list[str]] = {
    "noir": [
        "Hard-boiled investigator",
        "Client with divided loyalties",
        "Corrupt lieutenant",
        "Archivist who notices patterns",
    ],
    "scifi": [
        "Systems repair specialist",
        "AI companion",
        "Syndicate fixer",
        "Mission controller",
    ],
    "drama": [
        "Estranged family member",
        "Quiet caregiver",
        "Ambitious sibling",
        "Long-time witness",
    ],
    "thriller": [
        "Whistleblower",
        "Shadow agent",
        "Handler",
        "Forensic analyst",
    ],
    "fantasy": [
        "Reluctant heir",
        "Wandering seer",
        "Fallen knight",
        "Keeper of forbidden records",
    ],
    "western": [
        "Drifter",
        "Town doctor",
        "Rail baron",
        "Deputy with a private debt",
    ],
    "romance": [
        "Returning artist",
        "Night-shift baker",
        "Old flame",
        "Friend who sees the truth",
    ],
    "horror": [
        "Skeptical researcher",
        "Local guide",
        "The presence",
        "Survivor who remembers the first event",
    ],
}

TONE_LINES: dict[str, list[str]] = {
    "cinematic": [
        "The frame holds longer than comfort allows.",
        "Light cuts the room like a blade.",
        "The world seems to wait for a decision.",
    ],
    "intimate": [
        "They speak in almost-whispers.",
        "A hand almost reaches, then stops.",
        "The smallest silence says the most.",
    ],
    "epic": [
        "The sky answers with thunder.",
        "History turns on this threshold.",
        "A private choice becomes a public reckoning.",
    ],
    "melancholy": [
        "Rain keeps the appointments they broke.",
        "Memory arrives late.",
        "Even the room seems to miss who they were.",
    ],
    "tense": [
        "A clock ticks under the floorboards.",
        "Every exit is a rumor.",
        "The next breath could expose them.",
    ],
    "hopeful": [
        "Morning finds a crack in the curtain.",
        "Someone chooses kindness anyway.",
        "The damaged thing still has a future.",
    ],
}

TITLE_CORES: dict[str, list[str]] = {
    "noir": ["Silver Rain", "Last Alibi", "Night Receipt", "The Quiet Ledger"],
    "scifi": ["Quiet Orbit", "Static Horizon", "Null Protocol", "Glass Comet"],
    "drama": ["Soft Exit", "Unsaid Hours", "Borrowed Light", "The Other Room"],
    "thriller": ["Red Margin", "Dead Switch", "Second Key", "The Closed Channel"],
    "fantasy": ["Moon Ledger", "Salt Crown", "Ash Prophecy", "The Broken Oath"],
    "western": ["Iron Dust", "Last Train Out", "Dry Justice", "Noon Without Mercy"],
    "romance": ["Near Miss", "Dusk Ticket", "Afterglow Map", "The Long Way Home"],
    "horror": ["Below Floor", "The Listening", "Pale Threshold", "What Stayed Behind"],
}

ACT_TITLES = [
    {"title": "Fracture", "purpose": "Establish the world, wound, and unstable equilibrium."},
    {"title": "Gradient", "purpose": "Escalate pressure and make every choice cost more."},
    {"title": "Repair or Ruin", "purpose": "Force a verifiable choice and reveal the lasting scar."},
]

BEAT_LIBRARY = [
    ("Opening image", "Show the ordinary system and the hidden fault already inside it."),
    ("Inciting fracture", "A break becomes impossible to ignore and names the central threat."),
    ("Refusal", "The lead protects the old equilibrium even as evidence accumulates."),
    ("First threshold", "A choice closes the safest route back to the previous life."),
    ("Investigation", "The lead tests a theory and discovers the system is larger than expected."),
    ("Counterpressure", "The opposing force adapts and turns the lead's method against them."),
    ("Midpoint verification", "A partial truth is proven, but its meaning changes the objective."),
    ("Consequences", "The cost reaches an ally and makes the conflict personal."),
    ("Betrayal or reversal", "A trusted assumption fails under pressure."),
    ("Collapse", "The plan appears exhausted and the lead must confront the original wound."),
    ("Repair choice", "The lead chooses the smallest action that can still alter the outcome."),
    ("Confrontation", "The system tests whether the repaired belief survives real resistance."),
    ("Verification", "Consequences prove what changed and what cannot be restored."),
    ("New equilibrium", "The closing image records the scar and the improved state."),
]

STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "because",
    "before",
    "being",
    "between",
    "could",
    "discovers",
    "every",
    "from",
    "into",
    "itself",
    "must",
    "other",
    "their",
    "there",
    "these",
    "thing",
    "through",
    "under",
    "until",
    "when",
    "where",
    "which",
    "while",
    "with",
}


def _stable_int(*parts: Any) -> int:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:16], 16)


def derive_seed(premise: str, genre: str, tone: str, fmt: str) -> int:
    """Derive a stable 31-bit seed from the normalized brief."""

    return _stable_int(premise.strip().lower(), genre, tone, fmt) & 0x7FFFFFFF


def _identifier(prefix: str, seed: int, *parts: Any) -> str:
    digest = hashlib.sha256(
        "|".join([prefix, str(seed), *(str(part) for part in parts)]).encode("utf-8")
    ).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _pick(values: list[Any], seed: int, offset: int = 0) -> Any:
    if not values:
        raise ValueError("Cannot select from an empty list")
    return values[(seed + offset) % len(values)]


def extract_theme_terms(premise: str, limit: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", premise.lower())
    unique: list[str] = []
    for word in words:
        normalized = word.strip("'-")
        if normalized in STOPWORDS or normalized in unique:
            continue
        unique.append(normalized)
        if len(unique) >= limit:
            break
    return unique or ["repair", "truth", "memory"]


def invent_title(premise: str, genre: str, seed: int | None = None) -> str:
    actual_seed = seed if seed is not None else derive_seed(premise, genre, "cinematic", "short")
    return _pick(TITLE_CORES.get(genre, TITLE_CORES["drama"]), actual_seed)


def _extract_role_from_premise(premise: str) -> str | None:
    mapping = [
        ("technician", "Repair technician"),
        ("mechanic", "Mechanic"),
        ("engineer", "Engineer"),
        ("doctor", "Doctor"),
        ("detective", "Detective"),
        ("pilot", "Pilot"),
        ("journalist", "Journalist"),
        ("scientist", "Scientist"),
        ("soldier", "Soldier"),
        ("teacher", "Teacher"),
        ("hacker", "Hacker"),
        ("artist", "Artist"),
        ("lawyer", "Lawyer"),
        ("parent", "Parent"),
    ]
    lowered = premise.lower()
    for key, role in mapping:
        if key in lowered:
            return role
    return None


def _normalize_custom_cast(cast: list[dict[str, Any]] | None, seed: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(cast or []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "id": str(raw.get("id") or _identifier("char", seed, "custom", index, name)),
                "name": name[:80],
                "role": str(raw.get("role") or "Supporting character")[:120],
                "description": str(raw.get("description") or "")[:400],
                "arc": str(raw.get("arc") or "From fracture to a tested new belief")[:160],
            }
        )
        if len(normalized) >= 6:
            break
    return normalized


def generate_characters(
    premise: str,
    genre: str,
    fmt: str,
    seed: int,
    cast: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    custom = _normalize_custom_cast(cast, seed)
    if len(custom) >= 2:
        return custom

    roles = list(ROLE_SETS.get(genre, ROLE_SETS["drama"]))
    extracted = _extract_role_from_premise(premise)
    if extracted:
        roles[0] = extracted
    count = 3 if fmt in {"trailer", "short"} else 4
    arcs = [
        "From denial to responsible action",
        "From guarded loyalty to costly truth",
        "From control to exposed vulnerability",
        "From passive witness to active catalyst",
    ]
    generated: list[dict[str, Any]] = []
    for index in range(count):
        first = _pick(FIRST_NAMES, seed, index * 7)
        last = _pick(SURNAMES, seed // 3, index * 11)
        name = f"{first} {last}"
        if any(character["name"] == name for character in generated):
            last = _pick(SURNAMES, seed // 5, index * 17 + 3)
            name = f"{first} {last}"
        role = roles[index % len(roles)]
        generated.append(
            {
                "id": _identifier("char", seed, index, name),
                "name": name,
                "role": role,
                "description": f"{role} pulled into the central fracture: {premise[:160]}",
                "arc": arcs[index % len(arcs)],
            }
        )
    return [*custom, *generated][: max(count, len(custom))]


def generate_story_bible(
    premise: str,
    genre: str,
    tone: str,
    characters: list[dict[str, Any]],
    seed: int,
) -> dict[str, Any]:
    terms = extract_theme_terms(premise)
    lead = characters[0]["name"]
    pressure = characters[1]["name"] if len(characters) > 1 else "the opposing system"
    motif_pool = {
        "noir": ["rain on glass", "unpaid debts", "misfiled records"],
        "scifi": ["ghost signals", "repair lights", "memory residue"],
        "drama": ["unfinished meals", "old photographs", "closed doors"],
        "thriller": ["dead channels", "duplicated keys", "countdown clocks"],
        "fantasy": ["cracked seals", "salt circles", "names carved in stone"],
        "western": ["empty tracks", "wind-bent signs", "unclaimed bullets"],
        "romance": ["missed trains", "shared songs", "returned letters"],
        "horror": ["repeated knocks", "cold rooms", "voices behind walls"],
    }
    motif = _pick(motif_pool.get(genre, motif_pool["drama"]), seed)
    return {
        "theme": f"Repair requires truth about {terms[0]} and its cost.",
        "themeTerms": terms,
        "protagonistNeed": f"{lead} must replace control with accountable repair.",
        "opposingPressure": f"{pressure} benefits from the system remaining unstable.",
        "worldRule": "Every major correction leaves evidence and creates a new obligation.",
        "motif": motif,
        "continuityAnchors": [
            f"The first visible sign of {terms[0]}",
            "A promise that must be tested in the final act",
            f"The recurring motif of {motif}",
        ],
    }


def invent_logline(
    premise: str,
    genre: str,
    tone: str,
    characters: list[dict[str, Any]],
) -> str:
    lead = characters[0]["name"] if characters else "A stranger"
    foil = characters[1]["name"] if len(characters) > 1 else "a force from the past"
    consequence = "a damaged future becomes permanent" if tone != "hopeful" else "their last chance to rebuild disappears"
    clean = premise.strip().rstrip(".")
    return f"When {clean[:220]}, {lead} must confront {foil} before {consequence}."


def _beat_for_scene(index: int, count: int) -> tuple[str, str]:
    if count <= 1:
        return BEAT_LIBRARY[-1]
    library_index = round(index * (len(BEAT_LIBRARY) - 1) / (count - 1))
    return BEAT_LIBRARY[library_index]


def _act_for_scene(index: int, count: int, acts: int) -> int:
    return min(acts, (index * acts) // max(1, count) + 1)


def _chapter_for_scene(index: int, count: int, chapters: int) -> int:
    return min(chapters, (index * chapters) // max(1, count) + 1)


def _make_shot(
    seed: int,
    scene_number: int,
    shot_number: int,
    shot_type: str,
    description: str,
    dialogue: str | None,
    duration: float,
) -> dict[str, Any]:
    return {
        "id": _identifier("shot", seed, scene_number, shot_number, shot_type),
        "type": shot_type,
        "description": description,
        "dialogue": dialogue,
        "durationSec": duration,
    }


def rebuild_structure(scenes: list[dict[str, Any]], fmt: str) -> dict[str, list[dict[str, Any]]]:
    meta = format_meta(fmt)
    act_count = int(meta["acts"])
    chapter_count = int(meta["chapters"])
    acts: list[dict[str, Any]] = []
    for index in range(act_count):
        act_number = index + 1
        act_scenes = [scene for scene in scenes if int(scene.get("act", 1)) == act_number]
        descriptor = ACT_TITLES[index] if index < len(ACT_TITLES) else {
            "title": f"Act {act_number}",
            "purpose": "Advance the central conflict.",
        }
        acts.append(
            {
                "number": act_number,
                "title": descriptor["title"],
                "purpose": descriptor["purpose"],
                "sceneIds": [scene["id"] for scene in act_scenes],
                "scene_count": len(act_scenes),
            }
        )

    chapters: list[dict[str, Any]] = []
    for index in range(chapter_count):
        chapter_number = index + 1
        chapter_scenes = [
            scene for scene in scenes if int(scene.get("chapter", 1)) == chapter_number
        ]
        first_act = int(chapter_scenes[0].get("act", 1)) if chapter_scenes else 1
        chapters.append(
            {
                "number": chapter_number,
                "act": first_act,
                "title": f"Chapter {chapter_number}",
                "sceneIds": [scene["id"] for scene in chapter_scenes],
                "targetMinutes": round(
                    float(meta["minutes"]) / max(1, chapter_count), 2
                ),
            }
        )
    return {"acts": acts, "chapters": chapters}


def generate_scenes(
    premise: str,
    genre: str,
    tone: str,
    characters: list[dict[str, Any]],
    fmt: str,
    seed: int,
    story_bible: dict[str, Any],
) -> dict[str, Any]:
    meta = format_meta(fmt)
    scene_count = int(meta["scenes"])
    act_count = int(meta["acts"])
    chapter_count = int(meta["chapters"])
    locations = LOCATIONS.get(genre, LOCATIONS["drama"])
    tone_pool = TONE_LINES.get(tone, TONE_LINES["cinematic"])
    theme_terms = story_bible.get("themeTerms") or extract_theme_terms(premise)
    minutes_per_scene = float(meta["minutes"]) / max(1, scene_count)
    scenes: list[dict[str, Any]] = []

    for index in range(scene_count):
        scene_number = index + 1
        beat_name, beat_purpose = _beat_for_scene(index, scene_count)
        act = _act_for_scene(index, scene_count, act_count)
        chapter = _chapter_for_scene(index, scene_count, chapter_count)
        location = _pick(locations, seed, index * 5)
        time_of_day = "NIGHT" if (seed + index) % 3 else "DAY"
        interior = (seed + index) % 2 == 0
        slugline = f"{'INT.' if interior else 'EXT.'} {location} - {time_of_day}"
        lead = characters[0]
        counterpart = characters[1 + (index % max(1, len(characters) - 1))]
        focus_term = theme_terms[index % len(theme_terms)]
        motif = story_bible.get("motif", "a recurring detail")
        tone_line = _pick(tone_pool, seed // 7, index)

        conflict = (
            f"{lead['name']} needs proof about {focus_term}, while {counterpart['name']} "
            "needs the decision made before the evidence is complete."
        )
        turn = (
            f"A detail connected to {motif} changes the meaning of the previous scene "
            f"and forces {lead['name']} to accept a new cost."
        )
        summary = f"{beat_name}: {beat_purpose} The scene advances the premise through {focus_term}."
        action = (
            f"{tone_line} {lead['name']} enters with a specific objective and immediately "
            f"finds the environment resisting it. {counterpart['name']} reveals information "
            f"that complicates the premise: {premise[:180].rstrip('.')}. A physical detail tied "
            f"to {motif} confirms that the fracture has history. The characters test one small "
            "correction instead of attempting a total solution, but the result creates a visible "
            f"new obligation. By the end of the scene, {turn[0].lower() + turn[1:]}"
        )
        lead_dialogue = (
            f'{lead["name"]}: "I can verify the {focus_term}, but I cannot pretend the cost is zero."'
        )
        reply_dialogue = (
            f'{counterpart["name"]}: "Then choose which failure you are willing to own."'
        )
        shots = [
            _make_shot(
                seed,
                scene_number,
                1,
                "establishing",
                f"Reveal {location.lower()} and the motif of {motif}.",
                None,
                4.0,
            ),
            _make_shot(
                seed,
                scene_number,
                2,
                "medium",
                f"Hold {lead['name']} and {counterpart['name']} in the same unstable frame.",
                lead_dialogue,
                5.0,
            ),
            _make_shot(
                seed,
                scene_number,
                3,
                "closeup",
                f"Isolate the evidence connected to {focus_term}.",
                reply_dialogue,
                4.0,
            ),
            _make_shot(
                seed,
                scene_number,
                4,
                "transition",
                "End on the changed detail that carries continuity into the next scene.",
                None,
                3.0,
            ),
        ]
        setup_id = _identifier("setup", seed, max(1, scene_number - 2))
        payoff_id = _identifier("setup", seed, scene_number)
        scenes.append(
            {
                "id": _identifier("scene", seed, scene_number, beat_name),
                "number": scene_number,
                "act": act,
                "chapter": chapter,
                "slugline": slugline,
                "summary": summary,
                "dramaticPurpose": beat_purpose,
                "conflict": conflict,
                "turn": turn,
                "action": action,
                "location": location,
                "timeOfDay": time_of_day,
                "interior": interior,
                "characters": [lead["id"], counterpart["id"]],
                "shots": shots,
                "durationSec": sum(float(shot["durationSec"]) for shot in shots),
                "scriptMinutes": round(minutes_per_scene, 2),
                "setupIds": [payoff_id],
                "payoffIds": [setup_id] if scene_number > 2 else [],
                "continuityAnchors": [focus_term, motif],
            }
        )

    structure = rebuild_structure(scenes, fmt)
    return {"scenes": scenes, **structure}


def render_screenplay(state: dict[str, Any]) -> str:
    title = str(state.get("title") or "Untitled")
    logline = str(state.get("logline") or "")
    target_minutes = float(state.get("targetMinutes") or 0)
    characters = state.get("characters") or []
    scenes = state.get("scenes") or []
    bible = state.get("storyBible") or {}

    cast_lines = []
    for character in characters:
        if not isinstance(character, dict):
            continue
        cast_lines.append(
            f"  {str(character.get('name', '?')).upper()} - "
            f"{character.get('role', '')}. {character.get('arc', '')}"
        )
    cast = "\n".join(cast_lines)

    scene_blocks: list[str] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        shot_lines: list[str] = []
        for shot in scene.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            line = f"  [{str(shot.get('type', 'shot')).upper()}] {shot.get('description', '')}"
            if shot.get("dialogue"):
                line += f"\n\n                    {shot['dialogue']}"
            shot_lines.append(line)
        scene_blocks.append(
            "\n".join(
                [
                    f"ACT {scene.get('act', '?')} | CHAPTER {scene.get('chapter', '?')} | SCENE {scene.get('number', '?')}",
                    str(scene.get("slugline") or "INT. UNDEFINED SPACE - DAY"),
                    "",
                    str(scene.get("action") or scene.get("summary") or ""),
                    "",
                    f"CONFLICT: {scene.get('conflict', '')}",
                    f"TURN: {scene.get('turn', '')}",
                    "",
                    "\n\n".join(shot_lines),
                ]
            ).strip()
        )

    header = [
        title.upper(),
        "",
        "REPARODYNAMICS | TGRM SCREENPLAY BLUEPRINT",
        "",
        "LOGLINE",
        logline,
        "",
        "STORY BIBLE",
        f"Theme: {bible.get('theme', '')}",
        f"World rule: {bible.get('worldRule', '')}",
        f"Motif: {bible.get('motif', '')}",
        "",
        "CAST",
        cast,
        "",
        f"TARGET RUNTIME: {target_minutes:g} minutes",
        "",
        "FADE IN:",
        "",
    ]
    return "\n".join(header) + "\n\n\n".join(scene_blocks) + "\n\nFADE OUT.\n\nTHE END\n"


def format_screenplay(
    title: str,
    logline: str,
    characters: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    target_minutes: float,
) -> str:
    """Backward-compatible renderer for callers using the original signature."""

    return render_screenplay(
        {
            "title": title,
            "logline": logline,
            "characters": characters,
            "scenes": scenes,
            "targetMinutes": target_minutes,
            "storyBible": {},
        }
    )


def generate_outline(
    premise: str,
    genre: str = "scifi",
    fmt: str = "feature",
    tone: str = "cinematic",
    seed: int | None = None,
) -> dict[str, Any]:
    actual_seed = seed if seed is not None else derive_seed(premise, genre, tone, fmt)
    film = build_film_from_brief(
        premise=premise,
        genre=genre,
        tone=tone,
        fmt=fmt,
        seed=actual_seed,
    )
    return {
        "premise": film["premise"],
        "genre": film["genre"],
        "tone": film["tone"],
        "format": film["format"],
        "acts": film["acts"],
        "chapters": film["chapters"],
        "scenes": [
            {
                "number": scene["number"],
                "act": scene["act"],
                "chapter": scene["chapter"],
                "slugline": scene["slugline"],
                "summary": scene["summary"],
                "turn": scene["turn"],
            }
            for scene in film["scenes"]
        ],
    }


def build_film_from_brief(
    premise: str,
    genre: str = "scifi",
    tone: str = "cinematic",
    title: str | None = None,
    fmt: str = "short",
    scars: list[dict[str, Any]] | None = None,
    seed: int | None = None,
    cast: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_premise = (premise or "").strip()
    if not clean_premise:
        raise ValueError("A non-empty premise is required")
    clean_genre = genre if genre in LOCATIONS else "drama"
    clean_tone = tone if tone in TONE_LINES else "cinematic"
    clean_format = fmt if fmt in FORMATS else "short"
    actual_seed = seed if seed is not None else derive_seed(
        clean_premise, clean_genre, clean_tone, clean_format
    )
    meta = format_meta(clean_format)
    characters = generate_characters(
        clean_premise, clean_genre, clean_format, actual_seed, cast=cast
    )
    film_title = (title or "").strip() or invent_title(
        clean_premise, clean_genre, actual_seed
    )
    story_bible = generate_story_bible(
        clean_premise, clean_genre, clean_tone, characters, actual_seed
    )
    structure = generate_scenes(
        clean_premise,
        clean_genre,
        clean_tone,
        characters,
        clean_format,
        actual_seed,
        story_bible,
    )
    logline = invent_logline(clean_premise, clean_genre, clean_tone, characters)
    state: dict[str, Any] = {
        "id": _identifier("film", actual_seed, film_title, clean_format),
        "title": film_title,
        "premise": clean_premise,
        "genre": clean_genre,
        "tone": clean_tone,
        "format": clean_format,
        "seed": actual_seed,
        "logline": logline,
        "storyBible": story_bible,
        "characters": characters,
        "scenes": structure["scenes"],
        "acts": structure["acts"],
        "chapters": structure["chapters"],
        "targetMinutes": float(meta["minutes"]),
        "targetWords": int(meta["target_words"]),
        "scars": list(scars or []),
        "status": "scripted",
        "studio": "Reparodynamics",
        "pipeline": "TGRM",
    }
    state["script"] = render_screenplay(state)
    return state
