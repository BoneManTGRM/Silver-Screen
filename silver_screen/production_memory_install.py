"""Install durable project memory into validation, planning, prompts, and checkpoints."""

from __future__ import annotations

import contextvars
import copy
from typing import Any

from .production_memory import (
    build_production_memory,
    load_project_memory,
    memory_prompt_context,
    memory_summary,
    normalize_memory_seed,
    persist_run_memory,
)

_MEMORY_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "silver_screen_production_memory_context",
    default={},
)


def _seed_from_brief(brief: dict[str, Any]) -> dict[str, Any]:
    raw = brief.get("productionMemory") or brief.get("production_memory") or {}
    seed = normalize_memory_seed(raw)
    project_id = str(
        brief.get("projectId")
        or brief.get("project_id")
        or seed.get("projectId")
        or ""
    ).strip()
    if project_id:
        seed["projectId"] = project_id
    load_existing = bool(
        brief.get("loadProjectMemory")
        or brief.get("load_project_memory")
    )
    existing: dict[str, Any] = {}
    if load_existing and project_id:
        try:
            existing = load_project_memory(project_id, output_root="runs")
        except Exception:
            existing = {}
    return {
        "seed": seed,
        "existing": existing,
        "projectId": project_id,
    }


def _insert_memory(prompt: str, context: str, limit: int = 3500) -> str:
    context = str(context or "").strip()
    if not context:
        return str(prompt or "")[:limit]
    marker = "Finish on a concrete action"
    value = str(prompt or "")
    insertion = "PRODUCTION WORLD MEMORY: " + context
    if marker in value:
        before, after = value.split(marker, 1)
        reserve = len(insertion) + len(marker) + len(after) + 3
        value = before[: max(0, limit - reserve)].rstrip()
        return f"{value} {insertion} {marker}{after}"[:limit]
    reserve = len(insertion) + 1
    return f"{value[: max(0, limit - reserve)].rstrip()} {insertion}"[:limit]


def install_production_memory() -> None:
    from . import pipeline, script_engine, shot_director, shot_director_install, tgrm

    if getattr(pipeline, "_production_memory_installed", False):
        return

    original_validate = pipeline.validate_brief
    original_build = pipeline.build_film_from_brief
    original_tgrm = pipeline.run_tgrm
    original_run = pipeline.run_pipeline
    original_resume = pipeline.resume_video_run
    original_directed_prompt = shot_director.render_directed_prompt

    def validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
        normalized = original_validate(brief)
        context = _seed_from_brief(brief)
        _MEMORY_CONTEXT.set(context)
        normalized["productionMemory"] = copy.deepcopy(context["seed"])
        normalized["projectId"] = context.get("projectId") or None
        normalized["loadProjectMemory"] = bool(context.get("existing"))
        return normalized

    def build_film_from_brief(*args: Any, **kwargs: Any) -> dict[str, Any]:
        state = original_build(*args, **kwargs)
        context = _MEMORY_CONTEXT.get() or {}
        memory = build_production_memory(
            state,
            seed=context.get("seed") or {},
            existing=context.get("existing") or {},
            project_id=context.get("projectId") or None,
        )
        state["productionMemory"] = memory
        state["projectId"] = memory["projectId"]
        return state

    def run_tgrm(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_tgrm(state, *args, **kwargs)
        current = result.get("state") or state
        context = _MEMORY_CONTEXT.get() or {}
        existing = current.get("productionMemory") or context.get("existing") or {}
        memory = build_production_memory(
            current,
            seed=context.get("seed") or {},
            existing=existing,
            project_id=(
                current.get("projectId")
                or context.get("projectId")
                or None
            ),
        )
        current["productionMemory"] = memory
        current["projectId"] = memory["projectId"]
        result["state"] = current
        return result

    def render_directed_prompt(
        state: dict[str, Any],
        scene: dict[str, Any],
        shot: dict[str, Any] | None = None,
        repair: dict[str, Any] | None = None,
    ) -> str:
        prompt = original_directed_prompt(state, scene, shot, repair)
        context = memory_prompt_context(state, scene, shot, max_chars=1150)
        if isinstance(shot, dict):
            memory = state.get("productionMemory") or {}
            shot["productionMemory"] = {
                "projectId": memory.get("projectId"),
                "promptCoreHash": memory.get("promptCoreHash"),
                "memoryVersion": memory.get("memoryVersion"),
            }
        return _insert_memory(prompt, context)

    def run_pipeline(brief: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        _MEMORY_CONTEXT.set(_seed_from_brief(brief))
        result = original_run(brief, *args, **kwargs)
        run_id = str((result.get("run") or {}).get("id") or "")
        persisted = bool((result.get("run") or {}).get("persisted"))
        if run_id and persisted:
            try:
                memory_result = persist_run_memory(
                    run_id,
                    output_root=str(kwargs.get("output_root") or "runs"),
                )
                result["productionMemory"] = memory_result["summary"]
                result.setdefault("artifacts", {})["productionMemory"] = memory_result[
                    "memoryPath"
                ]
                result["artifacts"]["productionWorldGraph"] = memory_result[
                    "worldGraphPath"
                ]
            except Exception as exc:
                result.setdefault("warnings", []).append(
                    f"Production memory checkpoint could not be refreshed: {exc}"
                )
        elif isinstance(result.get("state"), dict):
            memory = (result.get("state") or {}).get("productionMemory") or {}
            if memory:
                result["productionMemory"] = memory_summary(memory)
        return result

    def resume_video_run(run_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_resume(run_id, *args, **kwargs)
        try:
            memory_result = persist_run_memory(
                run_id,
                output_root=str(kwargs.get("output_root") or "runs"),
            )
            result["productionMemory"] = memory_result["summary"]
            result.setdefault("artifacts", {})["productionMemory"] = memory_result[
                "memoryPath"
            ]
            result["artifacts"]["productionWorldGraph"] = memory_result[
                "worldGraphPath"
            ]
        except Exception as exc:
            result.setdefault("warnings", []).append(
                f"Production memory checkpoint could not be refreshed: {exc}"
            )
        return result

    pipeline.validate_brief = validate_brief
    pipeline.build_film_from_brief = build_film_from_brief
    pipeline.run_tgrm = run_tgrm
    pipeline.run_pipeline = run_pipeline
    pipeline.resume_video_run = resume_video_run
    pipeline._production_memory_installed = True

    script_engine.build_film_from_brief = build_film_from_brief
    tgrm.run_tgrm = run_tgrm
    shot_director.render_directed_prompt = render_directed_prompt
    shot_director_install.render_directed_prompt = render_directed_prompt


__all__ = ["install_production_memory"]
