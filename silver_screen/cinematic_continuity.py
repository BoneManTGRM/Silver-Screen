"""Install cinematic prompt and assembly upgrades without changing public APIs."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .runtime import RunWorkspace, load_run, utc_now
from .transition_engine import (
    TransitionError,
    assemble,
    build_filter,
    build_plan,
    duration,
    load_plan,
    paths,
    planned_transition,
    prompt_directive,
    relation,
    relative,
    rows,
    settings,
    shot_path,
    verified_shots,
)
from .video_runtime import load_video_queue, save_video_queue


def _transition_map(plan: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(item.get("fromShot") or ""), str(item.get("toShot") or "")): item
        for item in plan.get("transitions") or []
        if isinstance(item, dict)
    }


def _assemble_verified(
    queue: dict[str, Any],
    root: Path,
    *,
    complete: bool,
    ai_video: Any,
    mode: str = "auto",
) -> dict[str, Any]:
    cfg = settings(mode)
    shots = verified_shots(queue)
    if not shots:
        return {}
    plan = build_plan(queue, root, cfg)
    mapping = _transition_map(plan)
    groups: list[tuple[int, list[dict[str, Any]]]] = []
    for shot in shots:
        chapter = int((shot.get("sourceScene") or {}).get("chapter", 1) or 1)
        if not groups or groups[-1][0] != chapter:
            groups.append((chapter, []))
        groups[-1][1].append(shot)

    chapter_dir = paths(root)["chapters"]
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter_paths: list[Path] = []
    chapter_reports: list[dict[str, Any]] = []
    fallbacks = 0

    for chapter, chapter_shots in groups:
        clips = [path for shot in chapter_shots if (path := shot_path(root, shot))]
        if not clips:
            continue
        transitions = [
            mapping.get(
                (str(a.get("id") or ""), str(b.get("id") or "")),
                planned_transition(a, b, cfg),
            )
            for a, b in zip(chapter_shots, chapter_shots[1:])
        ]
        target = chapter_dir / f"chapter_{chapter:03d}_cinematic.mp4"
        try:
            report = assemble(clips, target, transitions, cfg)
            report["mode"] = "cinematic"
        except Exception as exc:
            fallbacks += 1
            ai_video.assemble_clips(clips, target)
            report = {
                "path": str(target),
                "mode": "safe_concat_fallback",
                "error": str(exc),
            }
        chapter_paths.append(target)
        chapter_reports.append({"chapter": chapter, "clips": len(clips), **report})

    chapter_transitions = []
    for (_, previous), (_, current) in zip(groups, groups[1:]):
        a, b = previous[-1], current[0]
        chapter_transitions.append(
            mapping.get(
                (str(a.get("id") or ""), str(b.get("id") or "")),
                planned_transition(a, b, cfg),
            )
        )

    final = root / (
        "final_cinematic_film.mp4" if complete else "partial_cinematic_film.mp4"
    )
    try:
        final_report = assemble(chapter_paths, final, chapter_transitions, cfg)
        final_report["mode"] = "cinematic"
    except Exception as exc:
        fallbacks += 1
        ai_video.assemble_clips(chapter_paths, final)
        final_report = {
            "path": str(final),
            "mode": "safe_concat_fallback",
            "error": str(exc),
        }

    transition_paths = paths(root)
    key = "finalFilm" if complete else "partialFilm"
    artifacts = {
        "chapterReels": [relative(root, path) for path in chapter_paths],
        key: relative(root, final),
        "transitionPlan": relative(root, transition_paths["plan"]),
        "transitionRuntime": relative(root, transition_paths["runtime"]),
    }
    plan["assembly"] = {
        "mode": "cinematic" if fallbacks == 0 else "cinematic_with_safe_fallback",
        "fallbackCount": fallbacks,
        "chapterReports": chapter_reports,
        "finalReport": final_report,
        "completedAt": utc_now(),
    }
    plan["artifacts"] = {**dict(plan.get("artifacts") or {}), **artifacts}
    plan["metrics"]["assemblyFallbacks"] = fallbacks
    queue["transitionPlan"] = plan
    queue["transitionMetrics"] = plan["metrics"]
    if fallbacks:
        record = {
            "event": "transition_safe_fallback",
            "fallbackCount": fallbacks,
            "at": utc_now(),
        }
        events = queue.setdefault("events", [])
        if record not in events:
            events.append(record)
    queue.setdefault("artifacts", {}).update(artifacts)
    from .transition_engine import save_plan

    save_plan(queue, root)
    save_video_queue(root, queue)
    return artifacts


def _enrich(result: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    root = Path(out_dir).resolve()
    queue = result.get("queue") or {}
    plan = queue.get("transitionPlan") or load_plan(root) or {}
    result["transitionPlan"] = plan
    result["transitionMetrics"] = (
        queue.get("transitionMetrics") or plan.get("metrics") or {}
    )
    result["transition_plan_path"] = (
        str(paths(root)["plan"]) if paths(root)["plan"].exists() else None
    )
    result["transition_runtime_path"] = (
        str(paths(root)["runtime"]) if paths(root)["runtime"].exists() else None
    )
    return result


def _cinematic_timing_plan(
    script: str,
    video_result: dict[str, Any],
    config: Any,
    script_sync: Any,
) -> dict[str, Any]:
    cfg = config.normalized()
    source_lines = script_sync.parse_script(script)
    shots = script_sync._verified_shots(video_result)
    if not shots:
        raise script_sync.VoiceStudioError(
            "Script Sync requires at least one verified video clip"
        )
    windows = []
    cursor = 0.0
    for shot in shots:
        seconds = duration(shot)
        try:
            start = float(shot.get("timelineStartSeconds"))
            end = float(shot.get("timelineEndSeconds"))
        except (TypeError, ValueError):
            start, end = cursor, cursor + seconds
        if end <= start:
            end = start + seconds
        windows.append(
            {"shot": shot, "start": start, "end": end, "duration": seconds}
        )
        cursor = max(cursor, end)
    total = max(window["end"] for window in windows)
    planned = []
    auto_cursor = cfg.head_padding_seconds
    for item in source_lines:
        estimate = script_sync.estimate_speech_seconds(
            item["text"], wpm=cfg.words_per_minute, speed=cfg.speed
        )
        start = (
            auto_cursor
            if item.get("explicitStart") is None
            else float(item["explicitStart"])
        )
        end = (
            start + estimate
            if item.get("explicitEnd") is None
            else float(item["explicitEnd"])
        )
        start, end = max(0.0, start), min(total, end)
        if end <= start:
            raise script_sync.VoiceStudioError(
                f"Script line {item['order']} starts after the cinematic runtime"
            )
        candidates = [
            index
            for index, window in enumerate(windows)
            if window["start"] <= start < window["end"]
        ]
        index = (
            candidates[-1]
            if candidates
            else min(
                len(windows) - 1,
                next(
                    (
                        i
                        for i, window in enumerate(windows)
                        if start < window["end"]
                    ),
                    len(windows) - 1,
                ),
            )
        )
        window = windows[index]
        local = max(0.0, start - window["start"])
        available = max(
            0.25,
            window["duration"] - local - cfg.tail_padding_seconds,
        )
        target = min(end - start, available)
        record = dict(item)
        record.update(
            {
                "shotId": str(window["shot"].get("id")),
                "shotOrder": int(
                    window["shot"].get("order", index + 1) or index + 1
                ),
                "globalStartSeconds": round(start, 3),
                "globalEndSeconds": round(start + target, 3),
                "localStartSeconds": round(local, 3),
                "targetDurationSeconds": round(target, 3),
                "estimatedSpeechSeconds": round(estimate, 3),
                "overflowSeconds": round(max(0.0, estimate - target), 3),
                "cinematicWindowStartSeconds": round(window["start"], 3),
                "cinematicWindowEndSeconds": round(window["end"], 3),
                "status": "planned",
            }
        )
        planned.append(record)
        auto_cursor = start + target + cfg.line_gap_seconds
    overflow = round(sum(float(item["overflowSeconds"]) for item in planned), 3)
    source_runtime = sum(duration(shot) for shot in shots)
    return {
        "schemaVersion": 2,
        "createdAt": utc_now(),
        "videoDurationSeconds": round(total, 3),
        "config": asdict(cfg),
        "lines": planned,
        "metrics": {
            "lineCount": len(planned),
            "wordCount": sum(
                len(script_sync.WORD_RE.findall(item["text"])) for item in planned
            ),
            "estimatedSpeechSeconds": round(
                sum(float(item["estimatedSpeechSeconds"]) for item in planned),
                3,
            ),
            "overflowSeconds": overflow,
            "fitStatus": (
                "fits" if overflow <= 0.15 else "needs_timing_repair"
            ),
            "cinematicTimeline": True,
            "transitionOverlapSeconds": round(
                max(0.0, source_runtime - total),
                3,
            ),
        },
    }


def rebuild_run_transitions(
    run_id: str,
    *,
    output_root: str = "runs",
    mode: str = "auto",
) -> dict[str, Any]:
    """Rebuild a saved run locally without a new video-provider request."""

    from . import ai_video

    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    root = workspace.media_dir.resolve()
    queue = load_video_queue(root)
    if queue is None or not verified_shots(queue):
        raise TransitionError(
            "The selected run has no verified durable video queue"
        )
    complete = str(queue.get("status") or "") == "complete"
    artifacts = _assemble_verified(
        queue,
        root,
        complete=complete,
        ai_video=ai_video,
        mode=mode,
    )
    media = result.get("media") or {}
    media.update(
        {
            "queue": queue,
            "transitionPlan": queue.get("transitionPlan") or {},
            "transitionMetrics": queue.get("transitionMetrics") or {},
            "transition_plan_path": str(paths(root)["plan"]),
            "transition_runtime_path": str(paths(root)["runtime"]),
        }
    )
    final_relative = artifacts.get("finalFilm") or artifacts.get("partialFilm")
    final = str((root / final_relative).resolve()) if final_relative else None
    media["hero_path"] = final
    media["final_video_path"] = final if complete else None
    media["partial_video_path"] = final if not complete else None
    media["chapter_paths"] = [
        str((root / item).resolve())
        for item in artifacts.get("chapterReels") or []
    ]
    result["media"] = media
    result["transitionPlan"] = media["transitionPlan"]
    result["transitionMetrics"] = media["transitionMetrics"]
    result.setdefault("artifacts", {})["transitionPlan"] = str(paths(root)["plan"])
    result["artifacts"]["transitionRuntime"] = str(paths(root)["runtime"])
    if final:
        result["artifacts"][
            "finalCinematicFilm" if complete else "partialCinematicFilm"
        ] = final
    workspace.register_optional_artifact("transitionPlan", paths(root)["plan"])
    workspace.register_optional_artifact("transitionRuntime", paths(root)["runtime"])
    workspace.register_optional_artifact(
        "finalCinematicFilm" if complete else "partialCinematicFilm",
        final,
    )
    workspace.write_json("result.json", result)
    bundle_title = str((result.get("state") or {}).get("title") or run_id)
    result["artifacts"]["bundle"] = str(workspace.build_bundle(bundle_title))
    workspace.write_json("result.json", result)
    workspace.build_bundle(bundle_title)
    return result


def install_cinematic_continuity() -> None:
    """Patch core extension points once after package imports finish."""

    from . import ai_video

    if getattr(ai_video, "_cinematic_continuity_installed", False):
        return
    original_prompt = ai_video.scene_prompt
    original_assemble = ai_video.assemble_verified_production
    original_generate = ai_video.generate_ai_video

    def enhanced_prompt(
        state: dict[str, Any],
        scene: dict[str, Any],
        shot: dict[str, Any] | None = None,
        repair: dict[str, Any] | None = None,
    ) -> str:
        base = original_prompt(state, scene, shot, repair)
        extra = prompt_directive(shot)
        return (
            base
            if not extra
            else base[: max(0, 3500 - len(extra) - 1)] + " " + extra
        )

    def enhanced_assemble(
        queue: dict[str, Any],
        root: Path,
        *,
        complete: bool,
    ) -> dict[str, Any]:
        try:
            return _assemble_verified(
                queue,
                root,
                complete=complete,
                ai_video=ai_video,
            )
        except Exception:
            return original_assemble(queue, root, complete=complete)

    def enhanced_generate(
        state: dict[str, Any],
        out_dir: str | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return _enrich(original_generate(state, out_dir, **kwargs), out_dir)

    ai_video.scene_prompt = enhanced_prompt
    ai_video.assemble_verified_production = enhanced_assemble
    ai_video.generate_ai_video = enhanced_generate
    ai_video._cinematic_continuity_installed = True

    try:
        from . import media

        media.generate_ai_video = enhanced_generate
    except Exception:
        pass

    try:
        from . import script_sync

        original_script_assemble = script_sync.assemble_clips

        def script_assemble(clips: list[Path], destination: Path) -> Path:
            plan = load_plan(destination.parent.parent / "media") or {}
            transitions = [
                item
                for item in plan.get("transitions") or []
                if isinstance(item, dict)
            ]
            if len(clips) > 1 and len(transitions) == len(clips) - 1:
                try:
                    assemble(
                        clips,
                        destination,
                        transitions,
                        settings(
                            str(
                                (plan.get("settings") or {}).get(
                                    "mode",
                                    "auto",
                                )
                            )
                        ),
                    )
                    return destination
                except Exception:
                    pass
            return original_script_assemble(clips, destination)

        script_sync.build_timing_plan = (
            lambda script, video_result, config: _cinematic_timing_plan(
                script,
                video_result,
                config,
                script_sync,
            )
        )
        script_sync.assemble_clips = script_assemble
    except Exception:
        pass

    try:
        from . import pipeline

        original_register = pipeline._register_media_artifacts

        def register(
            workspace: RunWorkspace,
            media_result: dict[str, Any],
        ) -> None:
            original_register(workspace, media_result)
            workspace.register_optional_artifact(
                "transitionPlan",
                media_result.get("transition_plan_path"),
            )
            workspace.register_optional_artifact(
                "transitionRuntime",
                media_result.get("transition_runtime_path"),
            )

        pipeline._register_media_artifacts = register
    except Exception:
        pass


transition_rows = rows
load_transition_plan = load_plan
transition_settings = settings
transition_relation = relation
plan_transition = planned_transition
build_transition_plan = build_plan
build_xfade_filter = build_filter
assemble_cinematic_clips = assemble
CinematicTransitionError = TransitionError
