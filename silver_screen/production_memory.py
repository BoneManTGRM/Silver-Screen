"""Durable project memory and production-world continuity.

The memory is project-scoped rather than request-scoped. It stores stable world
facts, character and asset continuity, accepted-shot evidence, model outcomes,
repair scars, and operator preferences across runs. Collections are bounded and
compacted so memory can grow for long productions without becoming unmanageable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .runtime import RunWorkspace, atomic_write_json, resolve_runs_root, slugify, utc_now

PROJECTS_DIR = "_projects"
MEMORY_FILE = "production_memory.json"
PROJECT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,79}")


class ProductionMemoryError(RuntimeError):
    pass


def _clean(value: Any, limit: int = 700) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _limit(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def project_id_for(brief: dict[str, Any] | None = None, explicit: str | None = None) -> str:
    source = brief or {}
    if _clean(explicit, 120):
        candidate = slugify(_clean(explicit, 120), fallback="silver-screen-project")[:80]
    else:
        label = _clean(source.get("title"), 100) or _clean(source.get("premise"), 100)
        suffix = _hash({"title": source.get("title"), "premise": source.get("premise"), "genre": source.get("genre")})[:10]
        candidate = f"{slugify(label, fallback='silver-screen-project')[:64]}-{suffix}"[:80]
    if not PROJECT_ID_PATTERN.fullmatch(candidate):
        raise ProductionMemoryError("Project memory ID contains unsupported characters")
    return candidate


def project_directory(project_id: str, output_root: str | os.PathLike[str] | None = "runs") -> Path:
    root = resolve_runs_root(output_root)
    selected = project_id_for(explicit=project_id)
    target = (root / PROJECTS_DIR / selected).resolve()
    if root not in target.parents:
        raise ProductionMemoryError("Project memory escaped the runs directory")
    return target


def _new(project_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "projectId": project_id,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "identity": {},
        "preferences": {},
        "worldGraph": {"characters": {}, "locations": {}, "props": {}, "scenes": {}, "chronology": []},
        "lockedFacts": [],
        "shotMemory": {},
        "modelMemory": {},
        "scarMemory": [],
        "events": [],
        "compactSummary": "",
    }


def load_project_memory(project_id: str, output_root: str | os.PathLike[str] | None = "runs") -> dict[str, Any]:
    selected = project_id_for(explicit=project_id)
    path = project_directory(selected, output_root) / MEMORY_FILE
    if not path.exists():
        return _new(selected)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionMemoryError(f"Could not read project memory: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProductionMemoryError("Project memory must be a JSON object")
    payload.setdefault("projectId", selected)
    return compact_memory(payload)


def _summary(memory: dict[str, Any]) -> str:
    identity = memory.get("identity") or {}
    graph = memory.get("worldGraph") or {}
    accepted = sum(bool(item.get("accepted")) for item in (memory.get("shotMemory") or {}).values() if isinstance(item, dict))
    facts = [str(item.get("text") or "") for item in (memory.get("lockedFacts") or [])[-8:] if isinstance(item, dict)]
    return _clean(
        f"Project {identity.get('title') or memory.get('projectId')}. Genre {identity.get('genre')}; tone {identity.get('tone')}. "
        f"World memory: {len(graph.get('characters') or {})} characters, {len(graph.get('locations') or {})} locations, "
        f"{len(graph.get('props') or {})} props, {accepted} accepted shots. Locked facts: {'; '.join(facts)}",
        1800,
    )


def compact_memory(memory: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(memory)
    result.setdefault("worldGraph", {})
    result.setdefault("shotMemory", {})
    result.setdefault("modelMemory", {})
    result.setdefault("lockedFacts", [])
    result.setdefault("scarMemory", [])
    result.setdefault("events", [])
    max_shots = _limit("SILVER_SCREEN_MEMORY_MAX_SHOTS", 1200, 50, 10000)
    max_events = _limit("SILVER_SCREEN_MEMORY_MAX_EVENTS", 500, 50, 5000)
    max_scars = _limit("SILVER_SCREEN_MEMORY_MAX_SCARS", 300, 20, 3000)
    shots = sorted(
        ((key, item) for key, item in result["shotMemory"].items() if isinstance(item, dict)),
        key=lambda pair: (bool(pair[1].get("accepted")), int(pair[1].get("order", 0) or 0), str(pair[1].get("updatedAt") or "")),
    )[-max_shots:]
    result["shotMemory"] = dict(shots)
    result["events"] = list(result["events"])[-max_events:]
    result["scarMemory"] = list(result["scarMemory"])[-max_scars:]
    result["lockedFacts"] = list(result["lockedFacts"])[-300:]
    result["compactSummary"] = _summary(result)
    result["updatedAt"] = utc_now()
    return result


def save_project_memory(memory: dict[str, Any], output_root: str | os.PathLike[str] | None = "runs") -> Path:
    selected = project_id_for(explicit=str(memory.get("projectId") or ""))
    result = compact_memory(memory)
    result["projectId"] = selected
    directory = project_directory(selected, output_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MEMORY_FILE
    atomic_write_json(path, result)
    atomic_write_json(directory / "world_graph.json", result.get("worldGraph") or {})
    return path


def _character_key(value: Any) -> str:
    return slugify(_clean(value, 100), fallback="character")[:80]


def build_world_graph(state: dict[str, Any], *, brief: dict[str, Any] | None = None, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    graph = deepcopy(existing or {})
    characters = graph.setdefault("characters", {})
    locations = graph.setdefault("locations", {})
    props = graph.setdefault("props", {})
    scenes_out = graph.setdefault("scenes", {})
    chronology = graph.setdefault("chronology", [])
    for index, item in enumerate(state.get("characters") or []):
        if not isinstance(item, dict):
            continue
        key = _character_key(item.get("name") or item.get("id") or f"character-{index+1}")
        prior = characters.get(key) or {}
        characters[key] = {
            **prior,
            "id": item.get("id"),
            "name": _clean(item.get("name"), 100),
            "role": _clean(item.get("role"), 180),
            "description": _clean(item.get("description"), 1600),
            "arc": _clean(item.get("arc"), 500),
            "wardrobe": _clean(item.get("wardrobe"), 900),
            "identityInvariants": _clean(item.get("identityInvariants"), 1200),
            "updatedAt": utc_now(),
        }
    for scene in state.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        number = int(scene.get("number", 0) or 0)
        slugline = _clean(scene.get("slugline"), 240)
        location_key = slugify(slugline or f"location-{number}", fallback="location")[:100]
        locations[location_key] = {
            **(locations.get(location_key) or {}),
            "slugline": slugline,
            "description": _clean(scene.get("setting") or scene.get("action"), 1200),
            "updatedAt": utc_now(),
        }
        character_keys = []
        ids = {str(item.get("id")): _character_key(item.get("name")) for item in state.get("characters") or [] if isinstance(item, dict)}
        for value in scene.get("characters") or []:
            character_keys.append(ids.get(str(value), _character_key(value)))
        scenes_out[str(number)] = {
            "number": number,
            "act": scene.get("act"),
            "chapter": scene.get("chapter"),
            "slugline": slugline,
            "location": location_key,
            "characters": character_keys,
            "summary": _clean(scene.get("summary"), 900),
            "action": _clean(scene.get("action"), 1600),
            "conflict": _clean(scene.get("conflict"), 700),
            "turn": _clean(scene.get("turn"), 700),
        }
        if number not in chronology:
            chronology.append(number)
    for raw in (brief or {}).get("assets") or []:
        if not isinstance(raw, dict):
            continue
        key = slugify(_clean(raw.get("name"), 120), fallback="asset")[:100]
        props[key] = {**(props.get(key) or {}), **deepcopy(raw), "updatedAt": utc_now()}
    graph["updatedAt"] = utc_now()
    return graph


def merge_project_memory(memory: dict[str, Any], *, brief: dict[str, Any], state: dict[str, Any] | None = None, preferences: dict[str, Any] | None = None) -> dict[str, Any]:
    result = deepcopy(memory)
    result["identity"] = {
        "title": _clean((state or {}).get("title") or brief.get("title"), 160),
        "premise": _clean(brief.get("premise"), 1600),
        "genre": _clean(brief.get("genre"), 80),
        "tone": _clean(brief.get("tone"), 80),
        "format": _clean(brief.get("format") or brief.get("fmt"), 80),
    }
    if preferences:
        result.setdefault("preferences", {}).update(deepcopy(preferences))
    if state:
        result["worldGraph"] = build_world_graph(state, brief=brief, existing=result.get("worldGraph"))
    record_memory_event(result, "memory_merged", detail=f"Updated {result['identity'].get('title') or result.get('projectId')}")
    return compact_memory(result)


def record_memory_event(memory: dict[str, Any], event_type: str, *, detail: str = "", data: dict[str, Any] | None = None) -> None:
    memory.setdefault("events", []).append({"type": _clean(event_type, 100), "at": utc_now(), "detail": _clean(detail, 1000), "data": deepcopy(data or {})})


def lock_fact(memory: dict[str, Any], text: str, *, category: str = "world", source: str = "operator") -> None:
    cleaned = _clean(text, 700)
    if not cleaned:
        return
    key = _hash({"category": category, "text": cleaned})[:20]
    facts = memory.setdefault("lockedFacts", [])
    if any(str(item.get("id")) == key for item in facts if isinstance(item, dict)):
        return
    facts.append({"id": key, "category": _clean(category, 80), "text": cleaned, "source": _clean(source, 120), "lockedAt": utc_now()})


def record_shot_outcome(memory: dict[str, Any], *, shot: dict[str, Any], report: dict[str, Any] | None = None, route: dict[str, Any] | None = None, accepted: bool | None = None, repair: str | None = None) -> None:
    shot_id = _clean(shot.get("id"), 100) or f"shot-{shot.get('order', 0)}"
    memory.setdefault("shotMemory", {})[shot_id] = {
        "shotId": shot_id,
        "order": int(shot.get("order", 0) or 0),
        "scene": int((shot.get("sourceScene") or {}).get("number", 0) or 0),
        "chapter": int((shot.get("sourceScene") or {}).get("chapter", 0) or 0),
        "promptHash": _hash(str(shot.get("prompt") or "")),
        "accepted": bool(accepted if accepted is not None else (report or {}).get("accepted", shot.get("status") == "verified")),
        "report": deepcopy(report or {}),
        "route": deepcopy(route or {}),
        "repair": _clean(repair, 1600),
        "attempts": int(shot.get("attempts", 0) or 0),
        "providerPredictionId": _clean(shot.get("providerPredictionId"), 160),
        "updatedAt": utc_now(),
    }


def record_model_outcome(memory: dict[str, Any], *, model: str, task: str, success: bool, quality_score: float | None = None, latency_seconds: float | None = None, estimated_cost_usd: float | None = None) -> None:
    key = _clean(model, 160) or "unknown"
    record = memory.setdefault("modelMemory", {}).setdefault(key, {"calls": 0, "successes": 0, "failures": 0, "qualitySamples": 0, "averageQuality": 0.0, "latencySamples": 0, "averageLatencySeconds": 0.0, "estimatedCostUsd": 0.0, "tasks": {}})
    record["calls"] += 1
    record["successes" if success else "failures"] += 1
    task_key = _clean(task, 80) or "general"
    record["tasks"][task_key] = int(record["tasks"].get(task_key, 0) or 0) + 1
    if quality_score is not None:
        samples = int(record["qualitySamples"])
        record["averageQuality"] = round((float(record["averageQuality"]) * samples + float(quality_score)) / (samples + 1), 6)
        record["qualitySamples"] = samples + 1
    if latency_seconds is not None:
        samples = int(record["latencySamples"])
        record["averageLatencySeconds"] = round((float(record["averageLatencySeconds"]) * samples + float(latency_seconds)) / (samples + 1), 4)
        record["latencySamples"] = samples + 1
    if estimated_cost_usd is not None:
        record["estimatedCostUsd"] = round(float(record["estimatedCostUsd"]) + max(0.0, float(estimated_cost_usd)), 6)
    record["updatedAt"] = utc_now()


def record_scar(memory: dict[str, Any], *, domain: str, failure: str, repair: str, shot_id: str = "") -> None:
    memory.setdefault("scarMemory", []).append({"domain": _clean(domain, 100), "failure": _clean(failure, 1200), "repair": _clean(repair, 1600), "shotId": _clean(shot_id, 100), "at": utc_now()})


def memory_context(memory: dict[str, Any], *, scene_number: int | None = None, shot: dict[str, Any] | None = None, max_chars: int = 2600) -> str:
    graph = memory.get("worldGraph") or {}
    scene = (graph.get("scenes") or {}).get(str(scene_number or 0), {})
    characters = graph.get("characters") or {}
    character_lines = []
    for key in list(scene.get("characters") or [])[:6]:
        item = characters.get(key) or {}
        character_lines.append(_clean(f"{item.get('name')}: {item.get('description')}; wardrobe {item.get('wardrobe')}; invariants {item.get('identityInvariants')}", 420))
    location = (graph.get("locations") or {}).get(str(scene.get("location") or ""), {})
    facts = [_clean(item.get("text") if isinstance(item, dict) else item, 240) for item in (memory.get("lockedFacts") or [])[-12:]]
    parts = [
        "PERSISTENT PRODUCTION MEMORY.",
        _clean(memory.get("compactSummary"), 900),
        f"Current scene {scene.get('number')}: {scene.get('slugline')}. Beat: {scene.get('summary') or scene.get('action')}. Conflict: {scene.get('conflict')}. Turn: {scene.get('turn')}." if scene else "",
        f"Location lock: {location.get('slugline')}; {location.get('description')}." if location else "",
        "Character locks: " + " | ".join(character_lines) if character_lines else "",
        "Locked facts: " + "; ".join(facts) if facts else "",
        f"Current shot {shot.get('id')} order {shot.get('order')}; never contradict accepted memory." if shot else "",
    ]
    return _clean(" ".join(part for part in parts if part), max_chars)


def snapshot_memory_to_run(run_id: str, project_id: str, *, output_root: str | os.PathLike[str] | None = "runs") -> dict[str, str]:
    memory = load_project_memory(project_id, output_root)
    workspace = RunWorkspace.open_existing(output_root, run_id)
    memory_path = workspace.write_json("memory/production_memory.json", memory)
    graph_path = workspace.write_json("memory/world_graph.json", memory.get("worldGraph") or {})
    workspace.register_artifact("productionMemory", memory_path)
    workspace.register_artifact("productionWorldGraph", graph_path)
    return {"productionMemory": str(memory_path), "productionWorldGraph": str(graph_path)}


__all__ = [
    "ProductionMemoryError", "build_world_graph", "compact_memory", "load_project_memory",
    "lock_fact", "memory_context", "merge_project_memory", "project_directory", "project_id_for",
    "record_memory_event", "record_model_outcome", "record_scar", "record_shot_outcome",
    "save_project_memory", "snapshot_memory_to_run",
]
