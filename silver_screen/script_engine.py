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
        ("android", "Synthetic being"),
        ("robot", "Synthetic being"),
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
    antag = characters[2]["name"] if len(characters) > 2 else foil
    support = characters[3]["name"] if len(characters) > 3 else lead
    lead_role = characters[0].get("role", "lead")
    short_p = premise[:90].rstrip(".")
    core = [
        f"{lead}, a {lead_role.lower()}, lives inside the wound of: {short_p}.",
        f"{foil} arrives with an offer that smells like destiny — and danger.",
        f"{lead} refuses the call. The world applies pressure around {short_p}.",
        f"Investigation expands. {lead} and {foil} map the fracture field.",
        f"Midpoint truth: {' '.join(premise.split()[:10])} is not what it seemed.",
        f"{antag} closes distance. Trust fractures. Energy cost rises.",
        f"Minimal corrections fail. {lead} faces the scar they denied.",
        f"Silence. {support} holds a final piece of evidence.",
        f"Final confrontation. {lead} chooses repair or ruin over: {short_p}.",
        f"New equilibrium. Scar memory remains. The city remembers {lead}.",
    ]
    beats: List[Dict[str, Any]] = []
    for i in range(n):
        template = core[min(i, len(core) - 1)]
        act_num = min(acts, int((i / max(1, n)) * acts) + 1)
        if i == 0:
            summary = template
        elif i == n - 1:
            summary = f"{lead} seals the outcome of: {short_p}."
        else:
            summary = f"{template} [beat {i + 1}/{n}]"
        beats.append({"act": act_num, "focus": f"beat{i}", "summary": summary})
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
        slugline = f"{'INT' if i % 2 == 0 else 'EXT'}. {loc} — {time}"
        tone_line = _pick(tone_pool, h + i)
        speaker = lead["name"] if i % 3 == 0 else (foil["name"] if i % 3 == 1 else antag["name"])
        if i == 0:
            dialogue_a = f'{speaker}: "Every system keeps a scar. I fix the break — then it starts to dream."'
        elif i == len(beats) - 1:
            dialogue_a = f'{speaker}: "Then we end it — repaired, or not at all."'
        else:
            dialogue_a = f'{speaker}: "{tone_line}"'
        shots: List[Dict[str, Any]] = []
        if i == 0:
            shots.append(_make_shot("title", f"Title card over {loc.lower()} atmosphere.", None, 3.2))
        elif i > 0 and beats[i - 1]["act"] != beat["act"]:
            act_title = ACT_TITLES[beat["act"] - 1]["title"] if beat["act"] <= len(ACT_TITLES) else "Movement"
            shots.append(_make_shot("actcard", f"Act {beat['act']} card — {act_title}.", None, 2.4))
        else:
            shots.append(_make_shot("establishing", f"Wide establish of {loc.lower()}. {tone_line}", None, 2.6))
        shots.append(_make_shot("medium", f"{lead['name']} ({lead.get('role', 'lead')}) in frame.", dialogue_a, 3.8))
        shots.append(_make_shot("wide", beat["summary"], None, 3.2))
        shots.append(
            _make_shot(
                "closeup",
                "Emotional beat — eyes, hands, decisive detail. Reparodynamic scar visible.",
                f'{lead["name"]}: "{premise[:40]}… ends here."' if i == len(beats) - 1 else None,
                2.6,
            )
        )
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
            "interior": i % 2 == 0,
            "shots": shots,
            "durationSec": sum(s["durationSec"] for s in shots),
            "scriptMinutes": round(minutes_per, 2),
        })
    acts: List[Dict[str, Any]] = []
    for i in range(int(meta["acts"])):
        meta_act = ACT_TITLES[i] if i < len(ACT_TITLES) else {"title": f"Act {i + 1}", "purpose": ""}
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
        body_parts.append(
            f"ACT {scene['act']} · CH {scene['chapter']}\n"
            f"{scene['slugline']}\n\n{scene['summary']}\n\n"
            + "\n\n".join(shot_lines)
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
        "outline": f"Multi-act outline for: {premise[:100]}",
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
