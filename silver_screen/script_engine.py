"""Multi-act script engine for Silver-Screen.

Deterministic generation from premise + genre + tone + format.
Produces characters, scenes, acts, chapters, and a formatted screenplay.
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
    "noir": ["RAIN-SOAKED ALLEY", "SMOKY BAR", "DETECTIVE'S OFFICE", "ROOFTOP"],
    "scifi": ["NEON CORRIDOR", "ORBITAL DOCK", "DATA VAULT", "BRIDGE DECK", "GENE LAB"],
    "drama": ["FAMILY KITCHEN", "HOSPITAL HALL", "EMPTY THEATER", "CITY BUS"],
    "thriller": ["SAFEHOUSE", "SUBWAY PLATFORM", "SERVER ROOM", "PARKING GARAGE"],
    "fantasy": ["ANCIENT LIBRARY", "CLIFF TEMPLE", "MOONLIT FOREST", "THRONE HALL"],
    "western": ["DUSTY SALOON", "OPEN PRAIRIE", "SHERIFF'S OFFICE"],
    "romance": ["BOOKSHOP", "RAINY CAFE", "FERRY DECK"],
    "horror": ["ABANDONED WING", "FOGGY CEMETERY", "BASEMENT CORRIDOR"],
}
ROLE_SETS: Dict[str, List[str]] = {
    "noir": ["Hard-boiled detective", "Femme fatale client", "Corrupt lieutenant"],
    "scifi": ["Rogue pilot", "AI companion", "Syndicate fixer"],
    "drama": ["Estranged parent", "Quiet caregiver", "Ambitious sibling"],
    "thriller": ["Whistleblower", "Shadow agent", "Handler"],
    "fantasy": ["Reluctant heir", "Wandering seer", "Fallen knight"],
    "western": ["Drifter", "Town doctor", "Rail baron"],
    "romance": ["Returning artist", "Night-shift baker", "Old flame"],
    "horror": ["Skeptical researcher", "Local guide", "The presence"],
}
TONE_LINES: Dict[str, List[str]] = {
    "cinematic": ["The frame holds longer than comfort allows.", "Light cuts the room like a blade."],
    "intimate": ["They speak in almost-whispers.", "A hand almost reaches. Almost."],
    "epic": ["The sky answers with thunder.", "History turns on this threshold."],
    "melancholy": ["Rain keeps the appointments they broke.", "Memory arrives late."],
    "tense": ["A clock ticks under the floorboards.", "Every exit is a rumor."],
    "hopeful": ["Morning finds a crack in the curtain.", "Someone chooses kindness anyway."],
}
ACT_TITLES = [
    {"title": "Fracture", "purpose": "Establish world, wound, and unstable equilibrium."},
    {"title": "Gradient", "purpose": "Escalate pressure; minimal choices compound into fate."},
    {"title": "Repair or Ruin", "purpose": "Verify who they become; scar memory seals the ending."},
]
TITLE_CORES: Dict[str, List[str]] = {
    "noir": ["Silver Rain", "Last Alibi", "Night Receipt"],
    "scifi": ["Quiet Orbit", "Static Horizon", "Null Protocol", "Glass Comet"],
    "drama": ["Soft Exit", "Unsaid Hours", "Borrowed Light"],
    "thriller": ["Red Margin", "Dead Switch", "Second Key"],
    "fantasy": ["Moon Ledger", "Salt Crown", "Ash Prophecy"],
    "western": ["Iron Dust", "Last Train Out", "Dry Justice"],
    "romance": ["Near Miss", "Dusk Ticket", "Afterglow Map"],
    "horror": ["Below Floor", "The Listening", "Pale Threshold"],
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
    cores = TITLE_CORES.get(genre, TITLE_CORES["scifi"])
    return _pick(cores, _hash(premise + genre))


def invent_logline(
    premise: str, genre: str, tone: str, characters: List[Dict[str, Any]]
) -> str:
    lead = characters[0]["name"] if characters else "A stranger"
    foil = characters[1]["name"] if len(characters) > 1 else "a ghost of the past"
    ending = "dawn rewrites the rules" if tone == "hopeful" else "the night claims its due"
    clean = premise.strip().rstrip(".")
    return f"{lead} must confront {foil} before {ending} — {clean}."


def _extract_role_from_premise(premise: str) -> Optional[str]:
    p = (premise or "").lower()
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
    ]
    for key, role in mapping:
        if key in p:
            return role
    return None


def generate_characters(premise: str, genre: str, fmt: str) -> List[Dict[str, Any]]:
    h = _hash(premise + genre)
    roles = list(ROLE_SETS.get(genre, ROLE_SETS["drama"]))
    extracted = _extract_role_from_premise(premise)
    if extracted:
        roles[0] = extracted
    count = 3 if fmt in ("trailer", "short") else 4
    arcs = [
        "From denial to resolve",
        "From lure to truth",
        "From control to collapse",
        "From witness to catalyst",
    ]
    out: List[Dict[str, Any]] = []
    for i in range(min(count, len(roles))):
        name = f"{_pick(FIRST_NAMES, h + i * 17)} {_pick(SURNAMES, h + i * 41)}"
        out.append({
            "id": _uid("char"),
            "name": name,
            "role": roles[i],
            "description": f"{roles[i]} drawn into: {premise[:90]}",
            "arc": arcs[i] if i < len(arcs) else "From fracture to repair",
        })
    return out


def _make_shot(
    stype: str, description: str, dialogue: Optional[str], duration: float
) -> Dict[str, Any]:
    return {
        "id": _uid("shot"),
        "type": stype,
        "description": description,
        "dialogue": dialogue,
        "durationSec": duration,
    }


def _build_beats(
    premise: str, fmt: str, characters: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    meta = format_meta(fmt)
    n = int(meta["scenes"])
    acts = int(meta["acts"])
    lead = characters[0]["name"]
    foil = characters[1]["name"] if len(characters) > 1 else lead
    core = [
        f"{lead} faces: {premise[:100]}",
        f"{foil} raises the stakes.",
        f"{lead} resists then commits.",
        "Investigation expands across the fracture field.",
        "A verified truth rewrites the map.",
        "Pressure closes in; trust fractures.",
        f"All seems lost for {lead}.",
        f"Final confrontation. {lead} chooses repair or ruin.",
    ]
    beats: List[Dict[str, Any]] = []
    for i in range(n):
        act_num = min(acts, int((i / max(1, n)) * acts) + 1)
        beats.append({
            "act": act_num,
            "focus": f"beat{i}",
            "summary": core[min(i, len(core) - 1)],
        })
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
    beats = _build_beats(premise, fmt, characters)
    minutes_per = float(meta["minutes"]) / max(1, len(beats))
    tone_pool = TONE_LINES.get(tone, TONE_LINES["cinematic"])
    scenes: List[Dict[str, Any]] = []
    for i, beat in enumerate(beats):
        loc = _pick(locs, h + i * 9)
        time = "NIGHT" if i % 2 == 0 else "DAY"
        slugline = f"{'INT' if i % 2 == 0 else 'EXT'}. {loc} — {time}"
        if i == 0:
            dialogue = f'{lead["name"]}: "Every system keeps a scar. {premise[:40]}."'
        elif i == len(beats) - 1:
            dialogue = f'{lead["name"]}: "Then we end it — repaired, or not at all."'
        else:
            dialogue = f'{lead["name"]}: "{_pick(tone_pool, h + i)}"'
        shots = [
            _make_shot("medium", f"{lead['name']} in frame.", dialogue, 3.5),
            _make_shot("wide", beat["summary"], None, 3.0),
            _make_shot(
                "closeup",
                "Emotional beat — eyes, hands, a single decisive detail.",
                None,
                2.5,
            ),
        ]
        chapter = min(
            int(meta["chapters"]),
            int((i / max(1, len(beats))) * int(meta["chapters"])) + 1,
        )
        scenes.append({
            "id": _uid("scene"),
            "number": i + 1,
            "act": beat["act"],
            "chapter": chapter,
            "slugline": slugline,
            "summary": beat["summary"],
            "location": loc,
            "timeOfDay": time,
            "interior": i % 2 == 0,
            "shots": shots,
            "durationSec": sum(s["durationSec"] for s in shots),
            "scriptMinutes": round(minutes_per, 2),
        })
    acts: List[Dict[str, Any]] = []
    for i in range(int(meta["acts"])):
        meta_act = (
            ACT_TITLES[i]
            if i < len(ACT_TITLES)
            else {"title": f"Act {i + 1}", "purpose": ""}
        )
        acts.append({
            "number": i + 1,
            "title": meta_act["title"],
            "purpose": meta_act["purpose"],
            "sceneIds": [s["id"] for s in scenes if s["act"] == i + 1],
            "scene_count": len([s for s in scenes if s["act"] == i + 1]),
        })
    chapters: List[Dict[str, Any]] = []
    for i in range(int(meta["chapters"])):
        chapter_scenes = [s for s in scenes if s["chapter"] == i + 1]
        chapters.append({
            "number": i + 1,
            "act": chapter_scenes[0]["act"] if chapter_scenes else 1,
            "title": f"Chapter {i + 1}",
            "sceneIds": [s["id"] for s in chapter_scenes],
            "targetMinutes": round(
                float(meta["minutes"]) / max(1, int(meta["chapters"])), 2
            ),
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
        f"  {c['name'].upper()} — {c['role']}. {c.get('arc', '')}" for c in characters
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
            f"ACT {scene['act']} · CH {scene['chapter']}\n"
            f"{scene['slugline']}\n\n{scene['summary']}\n\n{shots_block}"
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


def generate_outline(
    premise: str, genre: str = "scifi", fmt: str = "feature"
) -> Dict[str, Any]:
    meta = format_meta(fmt)
    return {
        "premise": premise,
        "genre": genre,
        "format": fmt,
        "acts": meta["acts"],
        "chapters": meta["chapters"],
        "outline": f"Outline: {premise[:100]}",
    }


def build_film_from_brief(
    premise: str,
    genre: str = "scifi",
    tone: str = "cinematic",
    title: Optional[str] = None,
    fmt: str = "short",
    scars: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
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
