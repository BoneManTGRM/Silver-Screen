"""Write short, original screenplays and hand them to the existing planner."""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

from .ai_video import VideoGenerationError
from .modal_renderer import modal_function, screenplay_writer_configured
from .science import GENRES, TONES
from .script_sync import estimate_speech_seconds


SCREENPLAY_LENGTHS = {20: 4, 30: 6}
IDEA_LIMIT = 1600
LEAD_LIMIT = 60


class ScreenplayError(ValueError):
    """The studio could not create or read a screenplay draft."""


class ScreenplayStatusUnavailable(ScreenplayError):
    """The existing writer job could not be reached."""


def _object(**fields: Any) -> dict[str, Any]:
    return {
        "type": "object", "properties": fields, "required": list(fields),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_CAST = _object(name=_STRING, role=_STRING, appearance=_STRING)


def _scene_array(**fields: Any) -> dict[str, Any]:
    return {
        "type": "array", "minItems": 5, "maxItems": 5,
        "items": _object(**fields),
    }


_FILM_SCHEMA = _object(
    title=_STRING, logline=_STRING, genre={"enum": list(GENRES)}, tone={"enum": list(TONES)},
    lead=_CAST, support=_CAST,
    scenes=_scene_array(
        heading=_STRING, action=_STRING,
        speaker={"enum": ["lead", "support", "none"]}, line=_STRING,
    ),
)
_AD_SCHEMA = _object(
    title=_STRING, logline=_STRING,
    scenes=_scene_array(setting=_STRING, action=_STRING, line=_STRING),
)
_COMMON = """Write five ordered shots in the language of the user's brief. Use headings like 'EXT. LOCATION - NIGHT'. Each action is one 15–28 word paragraph in present tense, with visible movement and specific objects. Vary the camera angle and blocking. Keep names, wardrobe, and props consistent. Each shot has at most one very short line. Use 'none' with an empty line when silence works better. Cast names must be distinct and use ordinary name characters. If the user supplies a lead name, use it exactly. Return only the requested JSON."""
_FILM = """You write original short films. Give the story a striking opening, an escalating problem, a reversal, a decisive choice by the lead, and a final image that pays off a physical detail from the opening. Respect the requested genre, casting, tone, and relationship. Show the lead by name in each action. If the user wants to star and leaves the name blank, invent a screen name for them. Avoid stock dialogue, generic spectacle, and reciting the brief. When the story calls for intimacy, portray consenting adults in a way that serves the characters and plot. For fictional danger, describe the visible movie action without operational instructions for real-world harm."""
_AD = """Write five shots for a short advertisement in the language of the brief. Give it a short title and a one-sentence logline. The five shots are a hook, a reveal, a concrete product demonstration, a detail, and a final product shot. For each shot give a setting, 15–25 words of visible action, and a short voiceover line or an empty string. Vary framing and camera movement. Keep the product design and color consistent. Use only supplied facts and avoid inventing performance claims, discounts, prices, reviews, or guarantees. The final shot leaves space below the product for an end card. Its voiceover uses the supplied call to action or an invitation to learn more. Return only the requested JSON."""
_SLUGLINE = re.compile(r"^(?:INT\.|EXT\.)\s+\S.*$", re.IGNORECASE)
_SPEAKER = re.compile(r"[\w .'’-]+")


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _timecode(seconds: int, milliseconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}.{milliseconds:03d}"


def script_sync_text(script: str, *, clip_seconds: int) -> str:
    """Anchor the generated speaker lines to their corresponding shots."""
    scenes = re.split(r"(?=^(?:INT\.|EXT\.)\s)", script, flags=re.MULTILINE)
    cues = []
    for order, scene in enumerate((item for item in scenes if item.strip()), 1):
        for line in scene.splitlines():
            if ":" not in line:
                continue
            speaker, _, words = line.partition(":")
            if not (_SPEAKER.fullmatch(speaker) and speaker == speaker.upper() and words.strip()):
                continue
            start = _timecode((order - 1) * clip_seconds, 120)
            end = _timecode(order * clip_seconds - 1, 780)
            cues.append(f"[{start} --> {end}] {line}")
    return "\n".join(cues)


def screenplay_request(
    idea: str, lead: str, seconds: int, seed: int, *,
    kind: str = "film", end_card: dict[str, str] | None = None,
) -> dict[str, Any]:
    idea, lead = idea.strip(), lead.strip()
    if len(idea) < 3 or len(idea) > IDEA_LIMIT:
        raise ScreenplayError(f"Enter a brief between 3 and {IDEA_LIMIT:,} characters.")
    if len(lead) > LEAD_LIMIT or (lead and not _SPEAKER.fullmatch(lead)):
        raise ScreenplayError("Use a short name with letters, spaces, or ordinary name punctuation.")
    if seconds not in SCREENPLAY_LENGTHS:
        raise ScreenplayError("Choose 20 or 30 seconds for the short screenplay.")
    if kind not in {"film", "ad"}:
        raise ScreenplayError("Choose a movie or advertisement.")
    clip = SCREENPLAY_LENGTHS[seconds]
    return {
        "model": "screenplay",
        "messages": [
            {"role": "system", "content": _AD if kind == "ad" else _FILM + "\n\n" + _COMMON},
            {"role": "user", "content": json.dumps({
                "brief": idea, "lead name": lead, "seconds per shot": clip,
                "maximum words in a line": clip * 2,
                **({"end card text": end_card} if kind == "ad" and end_card else {}),
            }, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "screenplay", "schema": _AD_SCHEMA if kind == "ad" else _FILM_SCHEMA},
        },
        "temperature": 0.9, "top_p": 0.9, "seed": seed, "max_tokens": 1250,
    }


def screenplay_draft(
    raw: dict[str, Any], *, idea: str, lead: str, seconds: int, seed: int,
    kind: str = "film", end_card: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate model output and produce the exact script used by production."""
    try:
        title, logline = _text(raw["title"], 120), _text(raw["logline"], 400)
        card = end_card if kind == "ad" and end_card else {}
        if kind == "ad":
            brand = _text(card.get("brand"), 36)
            title = title or (f"{brand} advertisement" if brand else "Product advertisement")
            # The production brief and the ad preview use the user's product
            # facts. A proposed logline can contain an unsupported claim.
            logline = _text(idea, 400)
            if len(logline) < 12:
                logline = f"Short advertisement for {title}."
            genre, tone = "drama", "cinematic"
            names = {"lead": lead or "Narrator"}
            cast = []
        else:
            genre, tone = raw["genre"], raw["tone"]
            names = {role: _text(raw[role]["name"], LEAD_LIMIT) for role in ("lead", "support")}
            if lead and names["lead"].casefold() != lead.casefold():
                raise ValueError
            if lead:
                names["lead"] = lead
            if names["lead"].casefold() == names["support"].casefold():
                raise ValueError
            cast = []
            for role, name in names.items():
                description = _text(raw[role]["appearance"], 400)
                job = _text(raw[role]["role"], 120)
                if not description or not job:
                    raise ValueError
                cast.append({"name": name, "role": job, "description": description})
        if (
            not title or len(logline) < 12 or genre not in GENRES or tone not in TONES
            or not all(_SPEAKER.fullmatch(name) for name in names.values())
            or len(raw["scenes"]) != 5
        ):
            raise ValueError
        scenes, overrides, seen = [], {}, set()
        clip_seconds = SCREENPLAY_LENGTHS[seconds]
        cta = _text(card.get("cta"), 64)
        for index, scene in enumerate(raw["scenes"], 1):
            action = _text(scene["action"], 520)
            line = _text(scene["line"], 180)
            if kind == "ad":
                heading = _text(scene["setting"], 120).upper() or "PRODUCT SET"
                if not _SLUGLINE.fullmatch(heading):
                    heading = f"INT. {heading}"
                speaker = "lead" if line else "none"
                if index == 5 and cta and estimate_speech_seconds(cta) <= clip_seconds - 0.35:
                    speaker, line = "lead", cta
            else:
                heading = _text(scene["heading"], 140).upper()
                speaker = scene["speaker"]
            if speaker == "none":
                line = ""
            if (
                not _SLUGLINE.fullmatch(heading) or len(action.split()) < 6
                or action.casefold() in seen or speaker not in {"lead", "support", "none"}
            ):
                raise ValueError
            if line and estimate_speech_seconds(line) > clip_seconds - 0.35:
                words = line.split()
                while words and estimate_speech_seconds(" ".join(words)) > clip_seconds - 0.55:
                    words.pop()
                line = " ".join(words).rstrip(".,;:") + "…" if words else ""
            seen.add(action.casefold())
            dialogue = f"{names[speaker].upper()}: {line}" if line else ""
            scenes.append("\n\n".join(part for part in (heading, action, dialogue) if part))
            overrides[str(index)] = f"{heading}. {action}"
    except (KeyError, TypeError, ValueError) as exc:
        raise ScreenplayError("The writer returned an incomplete draft. Try another version.") from exc
    shot_direction: dict[str, Any] = {"shotPromptOverrides": overrides}
    if kind == "ad":
        product = _text(card.get("brand"), 36) or title
        shot_direction["useAuthoredShotPrompts"] = True
        shot_direction["shotPromptOverrides"] = {
            order: (
                f"Product commercial for {product}. {action} "
                "Keep the product design and markings consistent with the reference. "
                "Use a clean commercial composition with no added graphic overlays."
            )
            for order, action in overrides.items()
        }
    return {
        "title": title, "premise": logline, "genre": genre, "tone": tone,
        "format": "trailer", "seed": seed, "cast": cast,
        "authoredScript": "\n\n\n".join(scenes),
        "creativeDirection": {"scriptSource": "authored", "strictGate": False},
        "shotDirection": shot_direction,
        "seconds": seconds, "kind": kind, "screenplayWriter": kind,
        **({"adEndCard": card} if card else {}),
    }


def submit_screenplay(
    idea: str, lead: str, seconds: int, *,
    kind: str = "film", end_card: dict[str, str] | None = None,
) -> dict[str, Any]:
    idea, lead = idea.strip(), " ".join(lead.split())
    seed = secrets.randbelow(2**31)
    request = screenplay_request(idea, lead, seconds, seed, kind=kind, end_card=end_card)
    if not screenplay_writer_configured():
        raise ScreenplayError("Connect the Modal screenplay writer to generate a draft.")
    try:
        modal, writer = modal_function("write_screenplay", app_setting="SILVER_SCREEN_SCREENPLAY_APP")
    except VideoGenerationError as exc:
        raise ScreenplayError(str(exc)) from exc
    try:
        call_id = writer.spawn(request).object_id
    except modal.exception.Error as exc:
        raise ScreenplayError(f"Modal could not start the writer ({type(exc).__name__}).") from exc
    return {"id": call_id, "idea": idea, "lead": lead, "seconds": seconds, "seed": seed, "kind": kind, "end_card": end_card}


def check_screenplay(job: dict[str, Any]) -> dict[str, Any] | None:
    import modal

    try:
        result = modal.FunctionCall.from_id(job["id"]).get(timeout=0)
    except TimeoutError:
        return None
    except (modal.exception.ConnectionError, modal.exception.ServiceError) as exc:
        raise ScreenplayStatusUnavailable("Could not check Modal. The same script job will be checked again.") from exc
    except modal.exception.Error as exc:
        raise ScreenplayError(f"Modal could not write the screenplay ({type(exc).__name__}).") from exc
    except (RuntimeError, OSError, ValueError) as exc:
        raise ScreenplayError("The screenplay model could not complete this draft. Try another draft.") from exc
    return screenplay_draft(result, **{key: value for key, value in job.items() if key != "id"})
