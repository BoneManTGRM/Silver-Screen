"""Persistent world-memory manager for projects, episodes, and sequels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from silver_screen.science import SCIENCE

st.set_page_config(
    page_title="Silver-Screen | Production Memory",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _module():
    from silver_screen import production_memory

    return production_memory


def _list_memories() -> list[dict[str, Any]]:
    module = _module()
    for name in ("list_project_memories", "list_project_memory", "list_memories"):
        function = getattr(module, name, None)
        if callable(function):
            try:
                value = function(output_root="runs")
            except TypeError:
                value = function("runs")
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    root = Path("runs") / "_projects"
    results = []
    for path in sorted(root.glob("*/production_memory.json")) if root.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                results.append(payload)
        except Exception:
            continue
    return results


def _load(project_id: str) -> dict[str, Any]:
    module = _module()
    for name in ("load_project_memory", "load_memory"):
        function = getattr(module, name, None)
        if callable(function):
            try:
                value = function(project_id, output_root="runs")
            except TypeError:
                value = function(project_id, "runs")
            if isinstance(value, dict):
                return value
    path = Path("runs") / "_projects" / project_id / "production_memory.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _save(project_id: str, memory: dict[str, Any]) -> dict[str, Any]:
    module = _module()
    for name in ("save_project_memory", "update_project_memory", "save_memory"):
        function = getattr(module, name, None)
        if callable(function):
            for kwargs in (
                {"output_root": "runs"},
                {},
            ):
                try:
                    value = function(project_id, memory, **kwargs)
                    return value if isinstance(value, dict) else memory
                except TypeError:
                    continue
    raise RuntimeError("The production-memory module does not expose a save function")


st.title("🧠 Production Memory")
st.caption(SCIENCE["credit"])
st.write(
    "Manage the persistent world graph that Silver-Screen carries across shots, "
    "episodes, sequels, retakes, and provider changes. Memory stores production facts "
    "and operator-approved continuity rules. It does not identify people or create "
    "biometric face embeddings."
)

memories = _list_memories()
labels = {
    f"{item.get('projectId') or item.get('id') or 'unknown'} | v{item.get('memoryVersion', 1)}": item
    for item in memories
}
with st.sidebar:
    selected = st.selectbox(
        "Project memory",
        list(labels) or ["No persistent projects"],
        disabled=not labels,
    )
    project_id = st.text_input(
        "New or existing project ID",
        value=(
            str((labels.get(selected) or {}).get("projectId") or "")
            if labels
            else ""
        ),
        placeholder="my-film-universe",
    )
    load_clicked = st.button(
        "Load memory",
        type="primary",
        use_container_width=True,
        disabled=not project_id.strip(),
    )

if load_clicked:
    try:
        st.session_state["project-memory"] = _load(project_id.strip())
    except Exception as exc:
        st.error(str(exc))

if "project-memory" not in st.session_state:
    st.session_state["project-memory"] = {
        "schemaVersion": 1,
        "projectId": project_id.strip(),
        "memoryVersion": 1,
        "world": {
            "characters": {},
            "locations": {},
            "props": {},
            "vehicles": {},
            "wardrobe": {},
            "relationships": [],
            "chronology": [],
            "storyRules": [],
            "visualStyle": {},
        },
        "decisions": [],
        "scarHistory": [],
        "operatorNotes": "",
    }

memory = st.session_state["project-memory"]
summary = st.columns(6)
world = memory.get("world") or {}
summary[0].metric("Version", memory.get("memoryVersion", 1))
summary[1].metric("Characters", len(world.get("characters") or {}))
summary[2].metric("Locations", len(world.get("locations") or {}))
summary[3].metric("Props", len(world.get("props") or {}))
summary[4].metric("Chronology", len(world.get("chronology") or []))
summary[5].metric("Scars", len(memory.get("scarHistory") or []))

left, right = st.columns([1.35, 1])
with left:
    raw = st.text_area(
        "Editable production-memory JSON",
        json.dumps(memory, ensure_ascii=False, indent=2),
        height=700,
        help="Locked facts should remain concrete and testable: who owns a prop, which wardrobe is active, where a scene occurs, and what changed chronologically.",
    )
with right:
    st.subheader("Memory design")
    st.markdown(
        """
**Characters**: identity, role, movement, wardrobe, condition, relationships.  
**Locations**: geography, entrances, lighting, time, weather, persistent damage.  
**Props and vehicles**: owner, position, state, visual description, continuity lock.  
**Chronology**: ordered state changes and causal events.  
**Story rules**: facts a later shot must not contradict.  
**Visual style**: lens, palette, medium, texture, camera grammar.  
**Decisions**: operator approvals and candidate selections.  
**Scar history**: successful repairs that should be reused.
"""
    )
    st.warning(
        "Do not store private secrets or API keys in production memory. Project memory "
        "is included in run artifacts and may be downloaded with the production bundle."
    )

save_clicked = st.button(
    "Validate and save persistent memory",
    type="primary",
    use_container_width=True,
    disabled=not project_id.strip(),
)
if save_clicked:
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Production memory must be a JSON object")
        parsed["projectId"] = project_id.strip()
        saved = _save(project_id.strip(), parsed)
        st.session_state["project-memory"] = saved
        st.success("Persistent production memory was validated and saved.")
    except Exception as exc:
        st.error(str(exc))

st.download_button(
    "Download production memory",
    json.dumps(st.session_state["project-memory"], ensure_ascii=False, indent=2).encode("utf-8"),
    file_name=f"{project_id.strip() or 'production'}-memory.json",
    mime="application/json",
    use_container_width=True,
)
