"""Durable project memory and production-world graph for Silver-Screen.

The memory is factual production state supplied or generated inside a project.
It does not identify people or create biometric embeddings. A small immutable
prompt core is hashed for approval; mutable decisions and repair history can
grow without silently changing the approved world contract.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .runtime import (
    RunWorkspace,
    atomic_write_json,
    load_run,
    resolve_runs_root,
    slugify,
    utc_now,
)
from .video_runtime import load_video_queue, save_video_queue

PROJECTS_DIR = "_projects"
MEMORY_FILE = "production_memory.json"
MAX_DECISIONS = 800
MAX_SCARS = 400
MAX_HISTORY = 300
PROJECT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,79}")


class ProductionMemoryError(RuntimeError):
    """Raised when durable production memory cannot be loaded or persisted."""


def _clean(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _dedupe(items: list[Any], *, limit: int) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for item in items:
        raw = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def normalize_project_id(value: Any, *, fallback: str = "silver-screen-project") -> str:
    candidate = slugify(_clean(value, 100), fallback=fallback).replace("-", "_")
    candidate = re.sub(r"[^a-z0-9_.-]+", "_", candidate.casefold()).strip("._-")
    if len(candidate) < 3:
        candidate = fallback.replace("-", "_")
    candidate = candidate[:80]
    if not PROJECT_ID_PATTERN.fullmatch(candidate):
        raise ProductionMemoryError("project_id contains unsupported characters")
    return candidate


def project_id_for_state(state: dict[str, Any], requested: Any = None) -> str:
    if requested:
        return normalize_project_id(requested)
    title = _clean(state.get("title") or state.get("premise") or "silver-screen-project", 100)
    seed = int(state.get("seed", 0) or 0)
    digest = hashlib.sha256(f"{title}|{seed}".encode("utf-8")).hexdigest()[:8]
    return normalize_project_id(f"{slugify(title)}_{digest}")


def normalize_memory_seed(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    source = copy.deepcopy(value)
    world = source.get("world")
    if not isinstance(world, dict):
        world = {}
    return {
        "projectId": _clean(source.get("projectId"), 80),
        "projectNotes": _clean(source.get("projectNotes") or source.get("notes"), 8000),
        "world": world,
        "lockedAssets": (
            copy.deepcopy(source.get("lockedAssets"))
            if isinstance(source.get("lockedAssets"), dict)
            else {}
        ),
        "decisions": [
            item for item in source.get("decisions") or [] if isinstance(item, dict)
        ][:MAX_DECISIONS],
        "scars": [
            item for item in source.get("scars") or [] if isinstance(item, dict)
        ][:MAX_SCARS],
        "history": [
            item for item in source.get("history") or [] if isinstance(item, dict)
        ][:MAX_HISTORY],
    }


def _character_records(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(state.get("characters") or [], start=1):
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("name") or f"Character {index}", 100)
        key = _clean(raw.get("id"), 100) or normalize_project_id(
            f"character_{name}", fallback=f"character_{index}"
        )
        records[key] = {
            "id": key,
            "name": name,
            "role": _clean(raw.get("role"), 220),
            "description": _clean(raw.get("description"), 1800),
            "arc": _clean(raw.get("arc"), 800),
            "identityContract": _clean(raw.get("identityContract"), 1800),
            "wardrobeState": _clean(raw.get("wardrobe") or raw.get("wardrobeState"), 1200),
            "currentState": _clean(raw.get("currentState"), 1200),
            "relationships": [
                _clean(item, 500)
                for item in raw.get("relationships") or []
                if _clean(item, 500)
            ][:30],
        }
    return records


def _location_key(slugline: str, index: int) -> str:
    return normalize_project_id(
        f"location_{slugline or index}", fallback=f"location_{index}"
    )


def _world_from_state(state: dict[str, Any]) -> dict[str, Any]:
    characters = _character_records(state)
    locations: dict[str, dict[str, Any]] = {}
    chronology: list[dict[str, Any]] = []
    shot_contracts: list[dict[str, Any]] = []
    for index, raw in enumerate(state.get("scenes") or [], start=1):
        if not isinstance(raw, dict):
            continue
        number = int(raw.get("number", index) or index)
        slugline = _clean(raw.get("slugline") or f"SCENE {number}", 240)
        location_id = _location_key(slugline, number)
        locations.setdefault(
            location_id,
            {
                "id": location_id,
                "slugline": slugline,
                "description": _clean(raw.get("locationDescription"), 1200),
                "timeOfDay": _clean(raw.get("timeOfDay"), 100),
                "weather": _clean(raw.get("weather"), 180),
                "geography": _clean(raw.get("geography"), 1000),
            },
        )
        scene_characters = [
            _clean(item, 100) for item in raw.get("characters") or [] if _clean(item, 100)
        ]
        chronology.append(
            {
                "scene": number,
                "act": int(raw.get("act", 1) or 1),
                "chapter": int(raw.get("chapter", 1) or 1),
                "locationId": location_id,
                "slugline": slugline,
                "summary": _clean(raw.get("summary"), 1200),
                "action": _clean(raw.get("action"), 1800),
                "conflict": _clean(raw.get("conflict"), 900),
                "turn": _clean(raw.get("turn"), 900),
                "characters": scene_characters,
            }
        )
        for shot_index, shot in enumerate(raw.get("shots") or [], start=1):
            if not isinstance(shot, dict):
                continue
            shot_contracts.append(
                {
                    "scene": number,
                    "sourceIndex": shot_index,
                    "type": _clean(shot.get("type") or "continuous", 80),
                    "description": _clean(shot.get("description"), 1200),
                    "dialogue": _clean(shot.get("dialogue"), 800),
                    "durationSeconds": float(shot.get("durationSec", 8) or 8),
                }
            )
    creative = state.get("creativeDirection") or {}
    if not isinstance(creative, dict):
        creative = {}
    story_bible = state.get("storyBible") or {}
    if not isinstance(story_bible, dict):
        story_bible = {}
    return {
        "characters": characters,
        "locations": locations,
        "props": {},
        "vehicles": {},
        "wardrobe": {},
        "relationships": {},
        "chronology": chronology,
        "shotContracts": shot_contracts,
        "storyRules": {
            key: _clean(value, 1800)
            for key, value in story_bible.items()
            if _clean(value, 1800)
        },
        "visualStyle": {
            "profile": _clean(creative.get("profile"), 120),
            "medium": _clean(creative.get("medium"), 500),
            "realism": _clean(creative.get("realism"), 300),
            "performance": _clean(creative.get("performanceStyle"), 500),
            "camera": _clean(creative.get("cameraStyle"), 500),
            "pacing": _clean(creative.get("pacing"), 300),
            "color": _clean(creative.get("colorLanguage"), 500),
            "globalDirection": _clean(creative.get("globalVisualDirection"), 1400),
            "directorNotes": _clean(creative.get("directorNotes"), 1200),
        },
    }


def _merge_dict(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        elif value not in (None, "", [], {}):
            result[key] = copy.deepcopy(value)
        elif key not in result:
            result[key] = copy.deepcopy(value)
    return result


def _prompt_core(memory: dict[str, Any]) -> dict[str, Any]:
    world = memory.get("world") or {}
    return {
        "projectId": memory.get("projectId"),
        "projectNotes": memory.get("projectNotes"),
        "world": {
            "characters": world.get("characters") or {},
            "locations": world.get("locations") or {},
            "props": world.get("props") or {},
            "vehicles": world.get("vehicles") or {},
            "wardrobe": world.get("wardrobe") or {},
            "relationships": world.get("relationships") or {},
            "chronology": world.get("chronology") or [],
            "storyRules": world.get("storyRules") or {},
            "visualStyle": world.get("visualStyle") or {},
        },
        "lockedAssets": memory.get("lockedAssets") or {},
    }


def memory_fingerprint(memory: dict[str, Any]) -> str:
    payload = json.dumps(
        _prompt_core(memory), sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_production_memory(
    state: dict[str, Any],
    *,
    seed: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    seed_value = normalize_memory_seed(seed)
    existing_value = normalize_memory_seed(existing)
    current_world = _world_from_state(state)
    seeded_world = _merge_dict(existing_value.get("world") or {}, seed_value.get("world") or {})
    world = _merge_dict(seeded_world, current_world)
    selected_project = (
        project_id
        or seed_value.get("projectId")
        or existing_value.get("projectId")
        or state.get("projectId")
    )
    memory: dict[str, Any] = {
        "schemaVersion": 1,
        "projectId": project_id_for_state(state, selected_project),
        "title": _clean(state.get("title"), 160),
        "premise": _clean(state.get("premise"), 4000),
        "seed": int(state.get("seed", 0) or 0),
        "projectNotes": _clean(
            seed_value.get("projectNotes") or existing_value.get("projectNotes"), 8000
        ),
        "world": world,
        "lockedAssets": _merge_dict(
            existing_value.get("lockedAssets") or {},
            seed_value.get("lockedAssets") or {},
        ),
        "decisions": _dedupe(
            [
                *(existing_value.get("decisions") or []),
                *(seed_value.get("decisions") or []),
            ],
            limit=MAX_DECISIONS,
        ),
        "scars": _dedupe(
            [
                *(existing_value.get("scars") or []),
                *(seed_value.get("scars") or []),
            ],
            limit=MAX_SCARS,
        ),
        "history": _dedupe(
            [
                *(existing_value.get("history") or []),
                *(seed_value.get("history") or []),
            ],
            limit=MAX_HISTORY,
        ),
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "memoryVersion": max(
            1,
            int((existing or {}).get("memoryVersion", 0) or 0) + 1,
        ),
    }
    memory["promptCoreHash"] = memory_fingerprint(memory)
    state["projectId"] = memory["projectId"]
    state["productionMemory"] = memory
    return memory


def _scene_entry(memory: dict[str, Any], number: int) -> dict[str, Any]:
    for item in (memory.get("world") or {}).get("chronology") or []:
        if isinstance(item, dict) and int(item.get("scene", -1) or -1) == number:
            return item
    return {}


def memory_prompt_context(
    state: dict[str, Any],
    scene: dict[str, Any],
    shot: dict[str, Any] | None = None,
    *,
    max_chars: int = 1150,
) -> str:
    memory = state.get("productionMemory")
    if not isinstance(memory, dict):
        memory = build_production_memory(state)
    world = memory.get("world") or {}
    scene_number = int(scene.get("number", 1) or 1)
    chronology = _scene_entry(memory, scene_number)
    cast_ids = {
        _clean(item, 100) for item in scene.get("characters") or [] if _clean(item, 100)
    }
    character_records = []
    all_characters = [
        (key, item)
        for key, item in (world.get("characters") or {}).items()
        if isinstance(item, dict)
    ]
    selected_characters = [
        (key, item)
        for key, item in all_characters
        if not cast_ids
        or key in cast_ids
        or _clean(item.get("name"), 100) in cast_ids
    ]
    if not selected_characters:
        selected_characters = all_characters[:4]
    for key, item in selected_characters:
        name = _clean(item.get("name"), 100)
        character_records.append(
            " ".join(
                part
                for part in [
                    name,
                    f"role={_clean(item.get('role'), 180)}" if item.get("role") else "",
                    (
                        f"identity={_clean(item.get('identityContract') or item.get('description'), 420)}"
                        if item.get("identityContract") or item.get("description")
                        else ""
                    ),
                    (
                        f"wardrobe={_clean(item.get('wardrobeState'), 260)}"
                        if item.get("wardrobeState")
                        else ""
                    ),
                    (
                        f"state={_clean(item.get('currentState'), 260)}"
                        if item.get("currentState")
                        else ""
                    ),
                ]
                if part
            )
        )
    location = (world.get("locations") or {}).get(chronology.get("locationId"), {})
    story_rules = "; ".join(
        f"{key}: {_clean(value, 280)}"
        for key, value in list((world.get("storyRules") or {}).items())[:5]
        if _clean(value, 280)
    )
    locked = "; ".join(
        f"{key}: {_clean(value, 260)}"
        for key, value in list((memory.get("lockedAssets") or {}).items())[:8]
        if _clean(value, 260)
    )
    shot_objective = _clean(
        ((shot or {}).get("shotBlueprint") or {}).get("description")
        or ((shot or {}).get("sourceScene") or {}).get("summary"),
        340,
    )
    pieces = [
        f"PROJECT MEMORY {memory.get('projectId')}.",
        f"Characters: {'; '.join(character_records)}." if character_records else "",
        (
            "Location continuity: "
            + " ".join(
                part
                for part in [
                    _clean(location.get("slugline"), 180),
                    _clean(location.get("description"), 260),
                    (
                        f"time={_clean(location.get('timeOfDay'), 80)}"
                        if location.get("timeOfDay")
                        else ""
                    ),
                    (
                        f"weather={_clean(location.get('weather'), 100)}"
                        if location.get("weather")
                        else ""
                    ),
                ]
                if part
            )
            + "."
            if location
            else ""
        ),
        f"Scene state: {_clean(chronology.get('summary'), 320)}." if chronology else "",
        f"Story rules: {story_rules}." if story_rules else "",
        f"Locked assets: {locked}." if locked else "",
        f"Shot memory objective: {shot_objective}." if shot_objective else "",
        "Do not contradict this approved production memory.",
    ]
    return " ".join(item for item in pieces if item)[:max_chars]


def _decision_from_shot(shot: dict[str, Any]) -> dict[str, Any] | None:
    if shot.get("status") != "verified":
        return None
    visual = shot.get("visualQuality") or {}
    semantic = shot.get("semanticQuality") or {}
    return {
        "type": "shot_acceptance",
        "shotId": _clean(shot.get("id"), 100),
        "order": int(shot.get("order", 0) or 0),
        "scene": int(((shot.get("sourceScene") or {}).get("number", 0)) or 0),
        "visualScore": visual.get("score"),
        "semanticScore": semantic.get("score"),
        "providerModel": _clean(shot.get("providerModel"), 160),
        "retakes": len(shot.get("visualQualityRetakeHistory") or []),
        "recordedAt": _clean(shot.get("completedAt") or utc_now(), 80),
    }


def update_memory_from_queue(
    memory: dict[str, Any],
    queue: dict[str, Any] | None,
) -> dict[str, Any]:
    result = copy.deepcopy(memory)
    if not isinstance(queue, dict):
        result["updatedAt"] = utc_now()
        return result
    decisions = list(result.get("decisions") or [])
    scars = list(result.get("scars") or [])
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        decision = _decision_from_shot(shot)
        if decision:
            decisions.append(decision)
        for report_name in ("visualQuality", "semanticQuality"):
            report = shot.get(report_name) or {}
            for finding in report.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                scars.append(
                    {
                        "type": report_name,
                        "shotId": _clean(shot.get("id"), 100),
                        "code": _clean(finding.get("code"), 120),
                        "severity": _clean(finding.get("severity"), 40),
                        "repair": _clean(finding.get("repair"), 800),
                        "recordedAt": utc_now(),
                    }
                )
    result["decisions"] = _dedupe(list(reversed(decisions)), limit=MAX_DECISIONS)
    result["decisions"].reverse()
    result["scars"] = _dedupe(list(reversed(scars)), limit=MAX_SCARS)
    result["scars"].reverse()
    result["updatedAt"] = utc_now()
    result["memoryVersion"] = int(result.get("memoryVersion", 1) or 1) + 1
    result["promptCoreHash"] = memory_fingerprint(result)
    return result


def memory_summary(memory: dict[str, Any]) -> dict[str, Any]:
    world = memory.get("world") or {}
    return {
        "projectId": memory.get("projectId"),
        "memoryVersion": int(memory.get("memoryVersion", 1) or 1),
        "characters": len(world.get("characters") or {}),
        "locations": len(world.get("locations") or {}),
        "props": len(world.get("props") or {}),
        "vehicles": len(world.get("vehicles") or {}),
        "scenes": len(world.get("chronology") or []),
        "shotContracts": len(world.get("shotContracts") or []),
        "decisions": len(memory.get("decisions") or []),
        "scars": len(memory.get("scars") or []),
        "lockedAssets": len(memory.get("lockedAssets") or {}),
        "promptCoreHash": memory.get("promptCoreHash"),
    }


def _projects_root(output_root: str | None = "runs") -> Path:
    root = resolve_runs_root(output_root)
    path = (root / PROJECTS_DIR).resolve()
    if root not in path.parents:
        raise ProductionMemoryError("Project memory path escaped the runs root")
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_memory_path(
    project_id: str,
    *,
    output_root: str | None = "runs",
) -> Path:
    selected = normalize_project_id(project_id)
    root = _projects_root(output_root)
    path = (root / selected / MEMORY_FILE).resolve()
    if root not in path.parents:
        raise ProductionMemoryError("Project memory path escaped the project root")
    return path


def save_project_memory(
    memory: dict[str, Any],
    *,
    output_root: str | None = "runs",
    run_id: str | None = None,
) -> Path:
    project_id = normalize_project_id(memory.get("projectId"))
    path = project_memory_path(project_id, output_root=output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = copy.deepcopy(memory)
    payload["projectId"] = project_id
    payload["updatedAt"] = utc_now()
    payload["promptCoreHash"] = memory_fingerprint(payload)
    atomic_write_json(path, payload)
    version_dir = path.parent / "versions"
    version_dir.mkdir(parents=True, exist_ok=True)
    version_name = (
        f"{int(payload.get('memoryVersion', 1) or 1):06d}"
        + (f"_{_clean(run_id, 100)}" if run_id else "")
        + ".json"
    )
    atomic_write_json(version_dir / version_name, payload)
    versions = sorted(version_dir.glob("*.json"), reverse=True)
    for obsolete in versions[40:]:
        obsolete.unlink(missing_ok=True)
    return path


def load_project_memory(
    project_id: str,
    *,
    output_root: str | None = "runs",
) -> dict[str, Any]:
    path = project_memory_path(project_id, output_root=output_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProductionMemoryError(f"Project memory {project_id!r} was not found") from exc
    except json.JSONDecodeError as exc:
        raise ProductionMemoryError("Project memory JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ProductionMemoryError("Project memory must be a JSON object")
    return payload


def list_project_memories(
    *,
    output_root: str | None = "runs",
    limit: int = 100,
) -> list[dict[str, Any]]:
    root = _projects_root(output_root)
    records: list[dict[str, Any]] = []
    for path in root.glob(f"*/{MEMORY_FILE}"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            records.append(
                {
                    **memory_summary(payload),
                    "title": payload.get("title"),
                    "updatedAt": payload.get("updatedAt"),
                    "path": str(path),
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    records.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    return records[: max(0, int(limit))]


def persist_run_memory(
    run_id: str,
    *,
    output_root: str | None = "runs",
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    state = result.get("state") or {}
    if not isinstance(state, dict) or not state:
        raise ProductionMemoryError("The run has no persisted film state")
    memory = state.get("productionMemory")
    if not isinstance(memory, dict):
        memory = build_production_memory(state)
    queue = load_video_queue(workspace.media_dir)
    memory = update_memory_from_queue(memory, queue)
    state["productionMemory"] = memory
    result["state"] = state
    result["productionMemory"] = memory_summary(memory)
    memory_dir = workspace.path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_path = memory_dir / MEMORY_FILE
    world_path = memory_dir / "world_graph.json"
    atomic_write_json(memory_path, memory)
    atomic_write_json(world_path, memory.get("world") or {})
    save_project_memory(memory, output_root=output_root, run_id=run_id)
    if queue is not None:
        queue["productionMemory"] = memory_summary(memory)
        save_video_queue(workspace.media_dir, queue)
    workspace.register_artifact("productionMemory", memory_path)
    workspace.register_artifact("productionWorldGraph", world_path)
    workspace.write_json("result.json", result)
    return {
        "memory": memory,
        "summary": memory_summary(memory),
        "memoryPath": str(memory_path),
        "worldGraphPath": str(world_path),
        "projectPath": str(
            project_memory_path(memory["projectId"], output_root=output_root)
        ),
    }


__all__ = [
    "ProductionMemoryError",
    "build_production_memory",
    "list_project_memories",
    "load_project_memory",
    "memory_fingerprint",
    "memory_prompt_context",
    "memory_summary",
    "normalize_memory_seed",
    "normalize_project_id",
    "persist_run_memory",
    "project_id_for_state",
    "save_project_memory",
    "update_memory_from_queue",
]
