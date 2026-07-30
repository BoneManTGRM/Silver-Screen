"""Multi-act script engine for Silver-Screen (Python port of TS script-engine).

Deterministic generation from premise + genre + tone + format.
Produces characters, scenes with shots, acts, chapters, and a formatted screenplay.
"""

from __future__ import annotations
import uuid
from typing import Dict, Any, List, Optional
from .science import FORMATS, format_meta

FIRST_NAMES = [
    "Elena", "Marcus", "Sofia", "Kai", "Vera", "Diego", "Ava", "Rafael",
    "Luna", "Orion", "Iris", "Caleb", "Nia", "Jonas", "Mira", "Noah",
    "Seraph", "Quinn", "Imani", "Theo",
]
SURNAMES = [
    "Cross", "Vale", "Reyes", "Kade", "Morrow", "Solis", "Hart", "Quill",
    "Voss", "Navarro", "Ash", "Lane", "Cortez", "Frost", "Blackwood", "Sato",
    "Okoye", "Mercer", "Duarte", "Shin",
]

LOCATIONS: Dict[str, List[str]] = {
    "noir": ["RAIN-SOAKED ALLEY", "SMOKY BAR", "DETECTIVE'S OFFICE", "ROOFTOP", "POLICE ARCHIVE", "RIVER DOCKS", "COURT ANNEX"],
    "scifi": ["NEON CORRIDOR", "ORBITAL DOCK", "DATA VAULT", "WASTELAND OUTPOST", "BRIDGE DECK", "GENE LAB", "NULL MARKET"],
    "drama": ["FAMILY KITCHEN", "HOSPITAL HALL", "EMPTY THEATER", "CITY BUS", "COURTYARD", "SCHOOL GYM", "APARTMENT STAIR"],
    "thriller": ["SAFEHOUSE", "SUBWAY PLATFORM", "SERVER ROOM", "HIGHWAY OVERPASS", "HOTEL SUITE", "PARKING GARAGE", "BORDER CHECK"],
    "fantasy": ["ANCIENT LIBRARY", "CLIFF TEMPLE", "MARKET OF SHADOWS", "MOONLIT FOREST", "THRONE HALL", "SALT MINE", "MIRROR LAKE"],
    "western": ["DUSTY SALOON", "OPEN PRAIRIE", "SHERIFF'S OFFICE", "RAILROAD DEPOT", "CANYON PASS", "CLAIM SITE", "CHURCH STEPS"],
    "romance": ["BOOKSHOP", "RAINY CAFE", "FERRY DECK", "BALCONY AT DUSK", "TRAIN COMPARTMENT", "GALLERY OPENING", "NIGHT MARKET"],
    "horror": ["ABANDONED WING", "FOGGY CEMETERY", "BASEMENT CORRIDOR", "SEALED ATTIC", "LAKESHORE", "SERVICE TUNNEL", "OLD CHAPEL"],
}

ROLE_SETS: Dict[str, List[str]] = {
    "noir": ["Hard-boiled detective", "Femme fatale client", "Corrupt lieutenant", "Night clerk witness", "Debt collector"],
    "scifi": ["Rogue pilot", "AI companion", "Syndicate fixer", "Archivist of the Ring", "Bio-smuggler"],
    "drama": ["Estranged parent", "Quiet caregiver", "Ambitious sibling", "Old friend returned", "Neighbor who hears everything"],
    "thriller": ["Whistleblower", "Shadow agent", "Handler", "Journalist ally", "Double asset"],
    "fantasy": ["Reluctant heir", "Wandering seer", "Fallen knight", "Market thief", "Oath-bound spirit"],
    "western": ["Drifter", "Town doctor", "Rail baron", "Widow rancher", "Young deputy"],
    "romance": ["Returning artist", "Night-shift baker", "Old flame", "Ferry captain", "Sister who knows"],
    "horror": ["Skeptical researcher", "Local guide", "The presence", "Archivist of seals", "Last survivor of the first night"],
}

TONE_LINES: Dict[str, List[str]] = {
    "cinematic": [
        "The frame holds longer than comfort allows.",
        "Light cuts the room like a blade.",
        "Silence becomes the loudest score.",
    ],
    "intimate": [
        "They speak in almost-whispers.",
        "A hand almost reaches. Almost.",
        "The room shrinks to two breaths.",
    ],
    "epic": [
        "The sky answers with thunder.",
        "History turns on this threshold.",
        "Every step echoes for generations.",
    ],
    "melancholy": [
        "Rain keeps the appointments they broke.",
        "Memory arrives late and overdressed.",
        "The past wears yesterday's coat.",
    ],
    "tense": [
        "A clock ticks under the floorboards.",
        "Every exit is a rumor.",
        "They smile with their teeth only.",
    ],
    "hopeful": [
        "Morning finds a crack in the curtain.",
        "Someone chooses kindness anyway.",
        "The map redraws itself toward home.",
    ],
}

ACT_TITLES = [
    {"title": "Fracture", "purpose": "Establish world, wound, and unstable equilibrium."},
    {"title": "Gradient", "purpose": "Escalate pressure; minimal choices compound into fate."},
    {"title": "Repair or Ruin", "purpose": "Verify who they become; scar memory seals the ending."},
]

TITLE_CORES: Dict[str, List[str]] = {
    "noir": ["Silver Rain", "Last Alibi", "Night Receipt", "Ash Signal"],
    "scifi": ["Quiet Orbit", "Static Horizon", "Null Protocol", "Glass Comet"],
    "drama": ["Soft Exit", "Unsaid Hours", "Borrowed Light", "Paper Rooms"],
    "thriller": ["Red Margin", "Dead Switch", "Second Key", "Cold Ledger"],
    "fantasy": ["Moon Ledger", "Salt Crown", "Ash Prophecy", "Veilward"],
    "western": ["Iron Dust", "Last Train Out", "Dry Justice", "Canyon Coin"],
    "romance": ["Near Miss", "Dusk Ticket", "Afterglow Map", "Second Glance"],
    "horror": ["Below Floor", "The Listening", "Pale Threshold", "House Remembers"],
}


def _uid(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _hash(s: str) -> int:
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0x7FFFFFFF
    return abs(h)


def _pick(arr: List[Any], seed: int) -> Any:
    return arr[abs(seed) % len(arr)]


def invent_title(premise: str, genre: str) -> str:
    h = _hash(premise + genre)
    cores = TITLE_CORES.get(genre, TITLE_CORES["scifi"])
    return _pick(cores, h)


def invent_logline(premise: str, genre: str, tone: str, characters: List[Dict[str, Any]]) -> str:
    lead = characters[0]["name"] if characters else "A stranger"
    foil = characters[1]["name"] if len(characters) > 1 else "a ghost of the past"
    ending = "dawn rewrites the rules" if tone == "hopeful" else "the night claims its due"
    clean = premise.strip().rstrip(".")
    return f"{lead} must confront {foil} before {ending} — {clean}."


def generate_characters(premise: str, genre: str, fmt: str) -> List[Dict[str, Any]]:
    h = _hash(premise + genre)
    roles = ROLE_SETS.get(genre, ROLE_SETS["drama"])
    count = 3 if fmt in ("trailer", "short") else 5 if fmt == "feature" else 4
    arcs = [
        "From denial to resolve",
        "From lure to truth",
        "From control to collapse",
        "From witness to catalyst",
        "From scar to shield",
    ]
    out: List[Dict[str, Any]] = []
    for i in range(min(count, len(roles))):
        first = _pick(FIRST_NAMES, h + i * 17)
        last = _pick(SURNAMES, h + i * 41 + 3)
        name = "It" if (i == 2 and genre == "horror") else f"{first} {last}"
        out.append({
            "id": _uid("char"),
            "name": name,
            "role": roles[i],
            "description": f"{roles[i]} drawn into: {premise[:90]}{'…' if len(premise) > 90 else ''}",
            "arc": arcs[i] if i < len(arcs) else "From fracture to repair",
        })
    return out


def _make_shot(stype: str, description: str, dialogue: Optional[str], duration: float) -> Dict[str, Any]:
    return {
        "id": _uid("shot"),
        "type": stype,
        "description": description,
        "dialogue": dialogue,
        "durationSec": duration,
    }


def _build_beats(premise: str, fmt: str, characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    meta = format_meta(fmt)
    n = int(meta["scenes"])
    acts = int(meta["acts"])
    lead = characters[0]["name"]
    foil = characters[1]["name"] if len(characters) > 1 else lead
    antag = characters[2]["name"] if len(characters) > 2 else foil
    support = characters[3]["name"] if len(characters) > 3 else lead

    core = [
        {"focus": "setup", "summary": f"{lead} lives inside the unstable equilibrium of: {premise[:100]}."},
        {"focus": "inciting", "summary": f"{foil} offers a deal that smells like destiny and danger."},
        {"focus": "debate", "summary": f"{lead} resists the call; the world applies pressure."},
        {"focus": "fun_and_games", "summary": f"Investigation expands. {lead} and {foil} map the fracture field."},
        {"focus": "midpoint", "summary": f"A verified truth rewrites the map. {' '.join(premise.split()[:8])} is not what it seemed."},
        {"focus": "bad_guys", "summary": f"{antag} closes distance. Trust fractures. Energy cost rises."},
        {"focus": "all_is_lost", "summary": f"Minimal corrections fail. {lead} faces the scar they denied."},
        {"focus": "dark_night", "summary": f"Silence. {support} holds a final piece of evidence."},
        {"focus": "climax", "summary": f"Final confrontation. {lead} chooses repair or ruin."},
        {"focus": "resolution", "summary": "New equilibrium. Scar memory remains. The city remembers."},
    ]

    beats: List[Dict[str, Any]] = []
    for i in range(n):
        template = core[min(i, len(core) - 1)]
        act_num = min(acts, int((i / max(1, n)) * acts) + 1)
        if i == 0:
            summary = template["summary"]
        elif i == n - 1:
            summary = f"{lead} seals the outcome of: {premise[:80]}."
        else:
            summary = f"{template['summary']} [beat {i + 1}/{n}]"
        beats.append({"act": act_num, "focus": template["focus"], "summary": summary})
    return beats


def generate_scenes(
    premise: str,
    genre: str,
    tone: str,
    characters: List[Dict[str, Any]],
    fmt: str,
) -> Dict[str, Any]:
    h = _hash(premise + genre + tone + fmt)
    meta = format_meta(fmt)
    locs = LOCATIONS.get(genre, LOCATIONS["drama"])
    lead = characters[0]
    foil = characters[1] if len(characters) > 1 else lead
    antag = characters[2] if len(characters) > 2 else foil
    times = ["NIGHT", "DAY", "DUSK", "DAWN", "NIGHT", "DAY"]
    beats = _build_beats(premise, fmt, characters)
    minutes_per = float(meta["minutes"]) / max(1, len(beats))
    tone_pool = TONE_LINES.get(tone, TONE_LINES["cinematic"])

    scenes: List[Dict[str, Any]] = []
    for i, beat in enumerate(beats):
        loc = _pick(locs, h + i * 9)
        time = times[i % len(times)]
        interior = i % 2 == 0
        slugline = f"{'INT' if interior else 'EXT'}. {loc} — {time}"
        tone_line = _pick(tone_pool, h + i)
        speaker = lead["name"] if i % 3 == 0 else (foil["name"] if i % 3 == 1 else antag["name"])
        if i == 0:
            dialogue_a = f'{speaker}: "I didn\'t ask for this story. It found me."'
        elif i == len(beats) - 1:
            dialogue_a = f'{speaker}: "Then we end it. On our terms."'
        else:
            dialogue_a = f"{speaker}: {tone_line}"

        shots: List[Dict[str, Any]] = []
        is_title = i == 0
        is_act_open = i == 0 or beats[i - 1]["act"] != beat["act"]

        if is_title:
            shots.append(_make_shot("title", f"Title card over {loc.lower()} atmosphere.", None, 3.2))
        elif is_act_open:
            act_title = ACT_TITLES[beat["act"] - 1]["title"] if beat["act"] <= len(ACT_TITLES) else "Movement"
            shots.append(_make_shot("actcard", f"Act {beat['act']} card — {act_title}.", None, 2.4))
        else:
            shots.append(_make_shot("establishing", f"Wide establish of {loc.lower()}. {tone_line}", None, 2.6))

        shots.append(_make_shot("medium", f"{lead['name']} in frame. Wardrobe and posture telegraph {genre}.", dialogue_a, 3.8))
        shots.append(
            _make_shot(
                "closeup" if i == len(beats) - 1 else "wide",
                beat["summary"],
                f"{foil['name']}: {tone_line}" if i % 2 == 1 else None,
                3.2,
            )
        )
        shots.append(
            _make_shot(
                "closeup",
                "Emotional beat — eyes, hands, a single decisive detail. Reparodynamic scar visible.",
                f"{lead['name']}: {' '.join(premise.split()[:5])}… ends here." if i == len(beats) - 1 else None,
                2.6,
            )
        )
        if fmt in ("feature", "featurette"):
            shots.append(
                _make_shot(
                    "insert",
                    f"Insert detail that foreshadows act {min(int(meta['acts']), beat['act'] + 1)}.",
                    None,
                    2.0,
                )
            )

        duration = sum(s["durationSec"] for s in shots)
        chapter = min(int(meta["chapters"]), int((i / max(1, len(beats))) * int(meta["chapters"])) + 1)

        scenes.append({
            "id": _uid("scene"),
            "number": i + 1,
            "act": beat["act"],
            "chapter": chapter,
            "slugline": slugline,
            "summary": beat["summary"],
            "location": loc,
            "timeOfDay": time,
            "interior": interior,
            "shots": shots,
            "durationSec": duration,
            "scriptMinutes": round(minutes_per, 2),
        })

    acts: List[Dict[str, Any]] = []
    for i in range(int(meta["acts"])):
        number = i + 1
        meta_act = ACT_TITLES[i] if i < len(ACT_TITLES) else {"title": f"Act {number}", "purpose": "Advance the fracture field."}
        acts.append({
            "number": number,
            "title": meta_act["title"],
            "purpose": meta_act["purpose"],
            "sceneIds": [s["id"] for s in scenes if s["act"] == number],
            "scene_count": len([s for s in scenes if s["act"] == number]),
        })

    chapters: List[Dict[str, Any]] = []
    for i in range(int(meta["chapters"])):
        number = i + 1
        chapter_scenes = [s for s in scenes if s["chapter"] == number]
        act = chapter_scenes[0]["act"] if chapter_scenes else 1
        chapters.append({
            "number": number,
            "act": act,
            "title": f"Chapter {number}",
            "sceneIds": [s["id"] for s in chapter_scenes],
            "targetMinutes": round(float(meta["minutes"]) / max(1, int(meta["chapters"])), 2),
        })

    return {"scenes": scenes, "acts": acts, "chapters": chapters}


def format_screenplay(
    title: str,
    logline: str,
    characters: List[Dict[str, Any]],
    scenes: List[Dict[str, Any]],
    target_minutes: float,
) -> str:
    cast = "\n".join(
        f"  {c['name'].upper()} — {c['role']}. {c.get('arc', '')}." for c in characters
    )
    body_parts: List[str] = []
    for scene in scenes:
        shot_lines = []
        for sh in scene.get("shots", []):
            head = f"  [{sh['type'].upper()}] {sh['description']}"
            if sh.get("dialogue"):
                shot_lines.append(f"{head}\n\n                    {sh['dialogue']}")
            else:
                shot_lines.append(head)
        shots_block = "\n\n".join(shot_lines)
        body_parts.append(
            f"ACT {scene['act']} · CH {scene['chapter']}\n{scene['slugline']}\n\n{scene['summary']}\n\n{shots_block}"
        )
    body = "\n\n\n".join(body_parts)
    return (
        f"{title.upper()}\n\n"
        f"REPARODYNAMICS · TGRM SCREENPLAY\n"
        f"LOGLINE\n{logline}\n\n"
        f"CAST\n{cast}\n\n"
        f"TARGET RUNTIME: {target_minutes} min\n\n"
        f"FADE IN:\n\n{body}\n\nFADE OUT.\n\nTHE END\n"
    )


def generate_outline(premise: str, genre: str = "scifi", fmt: str = "feature") -> Dict[str, Any]:
    """Backward-compatible thin outline helper."""
    meta = format_meta(fmt)
    return {
        "premise": premise,
        "genre": genre,
        "format": fmt,
        "acts": meta["acts"],
        "chapters": meta["chapters"],
        "outline": f"Multi-act outline for: {premise[:100]}{'…' if len(premise) > 100 else ''}",
    }


def build_film_from_brief(
    premise: str,
    genre: str = "scifi",
    tone: str = "cinematic",
    title: Optional[str] = None,
    fmt: str = "short",
    scars: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Full film package before TGRM repair."""
    premise = (premise or "").strip() or "A stranger inherits a secret that rewrites the city."
    genre = genre if genre in LOCATIONS else "drama"
    tone = tone if tone in TONE_LINES else "cinematic"
    fmt = fmt if fmt in FORMATS else "short"
    meta = format_meta(fmt)
    characters = generate_characters(premise, genre, fmt)
    film_title = (title or "").strip() or invent_title(premise, genre)
    structure = generate_scenes(premise, genre, tone, characters, fmt)
    logline = invent_logline(premise, genre, tone, characters)
    script = format_screenplay(
        film_title, logline, characters, structure["scenes"], float(meta["minutes"])
    )
    return {
        "title": film_title,
        "premise": premise,
        "genre": genre,
        "tone": tone,
        "format": fmt,
        "logline": logline,
        "script": script,
        "characters": characters,
        "scenes": structure["scenes"],
        "acts": structure["acts"],
        "chapters": structure["chapters"],
        "targetMinutes": float(meta["minutes"]),
        "scars": list(scars or []),
        "status": "scripted",
        "studio": "Reparodynamics",
        "pipeline": "TGRM",
    }
