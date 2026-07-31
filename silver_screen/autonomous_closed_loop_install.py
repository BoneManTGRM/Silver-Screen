"""Install bounded closed-loop semantic repair for Autonomous Studio.

The loop spends only within the operator-approved provider-call ceiling. It
preserves every accepted source clip, retakes only the lowest-scoring semantic
unit, compares the replacement with the preserved original, and restores the
original automatically when the replacement does not produce a verified gain.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .runtime import RunWorkspace, load_run, utc_now
from .video_runtime import (
    load_video_queue,
    record_video_event,
    save_video_queue,
    update_video_metrics,
)

_PLAN_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "silver_screen_autonomous_plan_context", default=None
)


class AutonomousRepairError(RuntimeError):
    pass


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().casefold() not in {"", "0", "false", "no", "off"}


def _relative(root: Path, path: Path) -> str:
    resolved, base = path.resolve(), root.resolve()
    if resolved != base and base not in resolved.parents:
        raise AutonomousRepairError("Autonomous repair artifact escaped the production workspace")
    return resolved.relative_to(base).as_posix()


def _path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = str(shot.get("path") or "")
    if not value:
        return None
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise AutonomousRepairError("Shot artifact escaped the production workspace")
    return resolved


def _shot(queue: dict[str, Any], shot_id: str) -> dict[str, Any]:
    for item in queue.get("shots") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == shot_id:
            return item
    raise AutonomousRepairError(f"Shot {shot_id!r} was not found")


def _clip_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _summarize(reports: list[dict[str, Any]], *, run_id: str, project_id: str) -> dict[str, Any]:
    ordered = sorted(
        (item for item in reports if isinstance(item, dict)),
        key=lambda item: int((item.get("contract") or {}).get("order", item.get("order", 0)) or 0),
    )
    scores = [float(item.get("compositeScore", 0) or 0) for item in ordered]
    return {
        "schemaVersion": 2,
        "runId": run_id,
        "projectId": project_id,
        "analyzedAt": utc_now(),
        "clips": len(ordered),
        "accepted": sum(bool(item.get("accepted")) for item in ordered),
        "review": sum(item.get("rating") == "review" for item in ordered),
        "rejected": sum(item.get("rating") == "reject" for item in ordered),
        "semanticReviewed": sum(bool(item.get("semanticAvailable")) for item in ordered),
        "averageScore": round(sum(scores) / len(scores), 6) if scores else 0.0,
        "minimumScore": round(min(scores), 6) if scores else 0.0,
        "reports": ordered,
    }


def _save_semantic_summary(
    workspace: RunWorkspace,
    queue: dict[str, Any],
    reports: list[dict[str, Any]],
    *,
    project_id: str,
) -> dict[str, Any]:
    summary = _summarize(reports, run_id=workspace.run_id, project_id=project_id)
    path = workspace.write_json("media/semantic_quality_report.json", summary)
    workspace.register_artifact("semanticQualityReport", path)
    queue["semanticQualityReport"] = summary
    save_video_queue(workspace.media_dir, queue)
    return {"report": summary, "queue": queue, "reportPath": str(path)}


def _persist_plan_and_routes(
    run_id: str,
    plan: dict[str, Any] | None,
    *,
    output_root: str,
    config: dict[str, Any],
    project_id: str,
) -> None:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    if plan:
        path = workspace.write_json("autonomous_plan.json", plan)
        workspace.register_artifact("autonomousPlan", path)
    else:
        plan_path = workspace.path / "autonomous_plan.json"
        if plan_path.exists():
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                plan = None
    queue = load_video_queue(workspace.media_dir)
    if queue is not None and plan:
        routes = ((plan.get("modelRoutes") or {}).get("routes") or [])
        by_order = {
            int(item.get("order", 0) or 0): item
            for item in routes
            if isinstance(item, dict)
        }
        by_id = {
            str(item.get("shotId") or ""): item
            for item in routes
            if isinstance(item, dict) and item.get("shotId")
        }
        for shot in queue.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            route = by_id.get(str(shot.get("id") or "")) or by_order.get(
                int(shot.get("order", 0) or 0)
            )
            if route:
                shot["modelRoute"] = deepcopy(route)
        save_video_queue(workspace.media_dir, queue)
    job_path = workspace.path / "autonomous_job.json"
    if not job_path.exists():
        job = {
            "schemaVersion": 2,
            "runId": run_id,
            "projectId": project_id,
            "updatedAt": utc_now(),
            "status": "running",
            "config": config,
            "quality": {},
            "audioFinished": False,
            "errors": [],
            "repairHistory": [],
            "artifacts": {},
        }
        path = workspace.write_json("autonomous_job.json", job)
        workspace.register_artifact("autonomousJob", path)


def _candidate(report: dict[str, Any], *, target: float) -> dict[str, Any] | None:
    candidates = [
        item
        for item in report.get("reports") or []
        if isinstance(item, dict)
        and item.get("semanticAvailable")
        and (
            item.get("hardFailure")
            or float(item.get("compositeScore", 0) or 0) < target
        )
    ]
    candidates.sort(
        key=lambda item: (
            0 if item.get("hardFailure") else 1,
            float(item.get("compositeScore", 0) or 0),
            int((item.get("contract") or {}).get("order", 0) or 0),
        )
    )
    return candidates[0] if candidates else None


def _schedule_semantic_retake(
    run_id: str,
    candidate: dict[str, Any],
    *,
    output_root: str,
    approved_calls: int,
    max_retries: int,
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    root = workspace.media_dir.resolve()
    queue = load_video_queue(root)
    if queue is None:
        raise AutonomousRepairError("The autonomous run has no video queue")
    shot_id = str(candidate.get("shotId") or (candidate.get("contract") or {}).get("shotId") or "")
    shot = _shot(queue, shot_id)
    if shot.get("status") != "verified":
        raise AutonomousRepairError("The semantic retake target is not verified")
    metrics = queue.get("metrics") or {}
    calls = int(metrics.get("providerCalls", 0) or 0)
    if approved_calls <= 0 or calls >= approved_calls:
        raise AutonomousRepairError("No approved provider call remains for semantic repair")
    attempts = int(shot.get("attempts", 0) or 0)
    if attempts >= max_retries + 1:
        raise AutonomousRepairError("The approved per-shot attempt ceiling has been reached")
    source = _path(root, shot)
    if source is None or not source.exists():
        raise AutonomousRepairError("The accepted semantic-retake source clip is missing")
    history = shot.setdefault("semanticRetakeHistory", [])
    maximum = _int_env("SILVER_SCREEN_AUTONOMOUS_MAX_SEMANTIC_RETAKES_PER_SHOT", 2, 1, 5)
    if len(history) >= maximum:
        raise AutonomousRepairError("The semantic retake safety limit has been reached for this clip")
    number = len(history) + 1
    archive = root / "semantic_quality" / "retakes" / shot_id / f"accepted_before_retake_{number:02d}.mp4"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, archive)
    record = {
        "retakeNumber": number,
        "sourcePath": _relative(root, source),
        "candidatePath": _relative(root, archive),
        "sourceHash": _clip_hash(source),
        "previousSemanticReport": deepcopy(shot.get("semanticQuality") or candidate),
        "previousVisualReport": deepcopy(shot.get("visualQuality") or {}),
        "verification": deepcopy(shot.get("verification") or {}),
        "verifiedDurationSeconds": float(
            shot.get("verifiedDurationSeconds")
            or shot.get("plannedDurationSeconds")
            or 0
        ),
        "completedAt": shot.get("completedAt"),
        "scheduledAt": utc_now(),
        "status": "preserved",
    }
    history.append(record)
    directive = " ".join(
        part
        for part in [
            "TARGETED SEMANTIC DIRECTOR RETAKE: preserve every accepted property and change only the listed failed dimensions.",
            str(candidate.get("repairDirective") or "").strip(),
            "Match the approved story beat, visible action, cast, identity, wardrobe, props, setting, screen direction, lens language, and duration. Do not add unapproved events or characters.",
        ]
        if part
    )[:3000]
    shot["semanticRetake"] = {
        "status": "scheduled",
        "retakeNumber": number,
        "previousScore": float(candidate.get("compositeScore", 0) or 0),
        "directive": directive,
        "scheduledAt": utc_now(),
    }
    # The existing provider-prompt extension appends this directive after the
    # approved prompt ledger, keeping the change explicit and bounded.
    shot["visualQualityRetake"] = {
        "status": "scheduled",
        "retakeNumber": number,
        "directive": directive,
        "previousScore": float(candidate.get("compositeScore", 0) or 0),
        "scheduledAt": utc_now(),
    }
    shot["status"] = "pending"
    shot["path"] = None
    shot["verifiedDurationSeconds"] = 0.0
    shot["verification"] = {}
    shot["providerPredictionId"] = None
    shot["providerPredictionUrl"] = None
    shot["providerStatus"] = None
    shot["completedAt"] = None
    shot["startedAt"] = None
    shot["lastError"] = "Autonomous semantic retake requested"
    queue["status"] = "partial"
    queue["stopReason"] = "autonomous_semantic_retake_scheduled"
    queue["completedAt"] = None
    update_video_metrics(queue)
    record_video_event(
        queue,
        "autonomous_semantic_retake_scheduled",
        shot_id=shot_id,
        data={
            "previousScore": candidate.get("compositeScore"),
            "retakeNumber": number,
            "remainingApprovedCalls": max(0, approved_calls - calls),
        },
    )
    save_video_queue(root, queue)
    return {
        "shotId": shot_id,
        "record": record,
        "queue": queue,
        "directive": directive,
    }


def _restore_preserved(
    workspace: RunWorkspace,
    queue: dict[str, Any],
    shot: dict[str, Any],
    record: dict[str, Any],
    *,
    reason: str,
) -> None:
    root = workspace.media_dir.resolve()
    archive = (root / str(record.get("candidatePath") or "")).resolve()
    target = (root / str(record.get("sourcePath") or "")).resolve()
    if root not in archive.parents or root not in target.parents:
        raise AutonomousRepairError("Preserved retake path escaped the workspace")
    if not archive.exists():
        raise AutonomousRepairError("The preserved pre-retake clip is missing")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, target)
    shot["path"] = _relative(root, target)
    shot["status"] = "verified"
    shot["verification"] = deepcopy(record.get("verification") or {})
    shot["verifiedDurationSeconds"] = float(record.get("verifiedDurationSeconds", 0) or 0)
    shot["completedAt"] = record.get("completedAt") or utc_now()
    shot["lastError"] = None
    shot["semanticQuality"] = deepcopy(record.get("previousSemanticReport") or {})
    shot["visualQuality"] = deepcopy(record.get("previousVisualReport") or {})
    shot.pop("semanticRetake", None)
    shot.pop("visualQualityRetake", None)
    record["status"] = "restored_original"
    record["decisionAt"] = utc_now()
    record["decisionReason"] = reason[:1200]
    update_video_metrics(queue)
    record_video_event(
        queue,
        "autonomous_semantic_original_restored",
        shot_id=str(shot.get("id") or ""),
        detail=reason,
    )
    save_video_queue(root, queue)


def _select_candidate(
    workspace: RunWorkspace,
    queue: dict[str, Any],
    shot: dict[str, Any],
    record: dict[str, Any],
    current: dict[str, Any],
    *,
    minimum_gain: float,
) -> dict[str, Any]:
    previous = record.get("previousSemanticReport") or {}
    old_score = float(previous.get("compositeScore", 0) or 0)
    new_score = float(current.get("compositeScore", 0) or 0)
    gain = new_score - old_score
    old_hard, new_hard = bool(previous.get("hardFailure")), bool(current.get("hardFailure"))
    keep_new = (old_hard and not new_hard) or (
        not new_hard and gain >= minimum_gain
    )
    if keep_new:
        shot["semanticQuality"] = current
        shot.pop("semanticRetake", None)
        shot.pop("visualQualityRetake", None)
        record["status"] = "selected_new"
        record["decisionAt"] = utc_now()
        record["currentScore"] = new_score
        record["gain"] = round(gain, 6)
        record_video_event(
            queue,
            "autonomous_semantic_retake_selected",
            shot_id=str(shot.get("id") or ""),
            data={"previousScore": old_score, "currentScore": new_score, "gain": gain},
        )
        save_video_queue(workspace.media_dir, queue)
        return {
            "selected": "new",
            "previousScore": old_score,
            "currentScore": new_score,
            "gain": round(gain, 6),
            "report": current,
        }
    reason = (
        "The replacement introduced a hard semantic failure."
        if new_hard
        else f"The measured gain {gain:.4f} was below the required {minimum_gain:.4f}."
    )
    _restore_preserved(workspace, queue, shot, record, reason=reason)
    return {
        "selected": "preserved_original",
        "previousScore": old_score,
        "currentScore": new_score,
        "gain": round(gain, 6),
        "report": deepcopy(previous),
        "reason": reason,
    }


def _incremental_report(
    run_id: str,
    shot_id: str,
    *,
    project_id: str,
    output_root: str,
    semantic_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from .production_memory import load_project_memory
    from .semantic_supervisor import evaluate_clip

    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    queue = load_video_queue(workspace.media_dir)
    if queue is None:
        raise AutonomousRepairError("The retake run has no video queue")
    shot = _shot(queue, shot_id)
    path = _path(workspace.media_dir.resolve(), shot)
    if path is None or not path.exists() or shot.get("status") != "verified":
        raise AutonomousRepairError("The replacement clip was not verified")
    memory = load_project_memory(project_id, output_root)
    report = evaluate_clip(
        path,
        state=result.get("state") or {},
        shot=shot,
        memory=memory,
        work_dir=workspace.media_dir / "semantic_quality" / shot_id,
        visual_report=shot.get("visualQuality"),
        config=semantic_config,
    )
    report["clipHash"] = _clip_hash(path)
    shot["semanticQuality"] = report
    save_video_queue(workspace.media_dir, queue)
    return report, shot, queue


def install_autonomous_closed_loop() -> None:
    from . import autonomous_studio

    if getattr(autonomous_studio, "_closed_loop_installed", False):
        return

    original_finish = autonomous_studio.finish_autonomous_run
    original_start = autonomous_studio.start_autonomous_production

    def start_autonomous_production(
        plan: dict[str, Any],
        *,
        images: list[Any] | None = None,
        voices: list[Any] | None = None,
        output_root: str = "runs",
        progress=None,
    ) -> dict[str, Any]:
        token = _PLAN_CONTEXT.set(deepcopy(plan))
        try:
            return original_start(
                plan,
                images=images,
                voices=voices,
                output_root=output_root,
                progress=progress,
            )
        finally:
            _PLAN_CONTEXT.reset(token)

    def finish_autonomous_run(
        run_id: str,
        *,
        project_id: str,
        config: dict[str, Any],
        output_root: str = "runs",
    ) -> dict[str, Any]:
        from .pipeline import resume_video_run
        from .semantic_supervisor import inspect_run_semantics

        cfg = autonomous_studio.normalize_autonomous_config(config)
        plan = _PLAN_CONTEXT.get()
        _persist_plan_and_routes(
            run_id,
            plan,
            output_root=output_root,
            config=cfg,
            project_id=project_id,
        )

        # Run local finishing first but defer paid speech until picture lock.
        first_cfg = {**cfg, "finishAudio": False, "semanticQa": False}
        finished = original_finish(
            run_id,
            project_id=project_id,
            config=first_cfg,
            output_root=output_root,
        )
        repair_history: list[dict[str, Any]] = []
        semantic: dict[str, Any] | None = None
        use_semantic = bool(cfg.get("semanticQa")) and bool(
            (cfg.get("semantic") or {}).get("useOpenAI")
        )
        result = load_run(run_id, output_root)
        if use_semantic:
            try:
                semantic = inspect_run_semantics(
                    run_id,
                    project_id=project_id,
                    output_root=output_root,
                    config=cfg.get("semantic") or {},
                )
            except Exception as exc:
                finished.setdefault("errors", []).append(
                    f"semantic inspection: {exc}"
                )

        auto_repair = _bool_env("SILVER_SCREEN_AUTONOMOUS_AUTO_SEMANTIC_REPAIR", True)
        profile = str(cfg.get("qualityProfile") or "cinematic")
        default_repairs = 2 if profile == "blockbuster_target" else 1
        max_repairs = _int_env(
            "SILVER_SCREEN_AUTONOMOUS_MAX_SEMANTIC_RETAKES_PER_RUN",
            default_repairs,
            0,
            8,
        )
        minimum_gain = _float_env(
            "SILVER_SCREEN_AUTONOMOUS_RETAKE_MIN_GAIN", 0.015, 0.0, 0.25
        )
        approved_calls = int(cfg.get("maxProviderCalls", 0) or 0)
        max_retries = int(cfg.get("retriesPerShot", 0) or 0)

        if (
            auto_repair
            and semantic
            and str(result.get("status") or "") == "complete"
            and max_repairs > 0
        ):
            for _ in range(max_repairs):
                report = semantic.get("report") or {}
                candidate = _candidate(
                    report,
                    target=float((cfg.get("semantic") or {}).get("qualityTarget", 0.84)),
                )
                if candidate is None:
                    break
                workspace = RunWorkspace.open_existing(output_root, run_id)
                queue = load_video_queue(workspace.media_dir) or {}
                calls = int((queue.get("metrics") or {}).get("providerCalls", 0) or 0)
                if approved_calls <= 0 or calls >= approved_calls:
                    repair_history.append(
                        {
                            "status": "not_run",
                            "shotId": candidate.get("shotId"),
                            "reason": "approved_provider_call_ceiling_reached",
                        }
                    )
                    break
                try:
                    scheduled = _schedule_semantic_retake(
                        run_id,
                        candidate,
                        output_root=output_root,
                        approved_calls=approved_calls,
                        max_retries=max_retries,
                    )
                    resumed = resume_video_run(
                        run_id,
                        output_root=output_root,
                        batch_size=1,
                        continuous=False,
                        max_retries=max_retries,
                        max_provider_calls=approved_calls,
                        max_spend_usd=(
                            float(cfg.get("maxSpendUsd", 0) or 0) or None
                        ),
                        cost_per_second_usd=(
                            float(cfg.get("costPerSecondUsd", 0) or 0) or None
                        ),
                        use_continuity=True,
                    )
                    if str(resumed.get("status") or "") not in {"complete", "partial"}:
                        raise AutonomousRepairError(
                            f"Retake resume ended as {resumed.get('status')}"
                        )
                    current, shot, queue = _incremental_report(
                        run_id,
                        str(scheduled["shotId"]),
                        project_id=project_id,
                        output_root=output_root,
                        semantic_config=cfg.get("semantic") or {},
                    )
                    workspace = RunWorkspace.open_existing(output_root, run_id)
                    decision = _select_candidate(
                        workspace,
                        queue,
                        shot,
                        scheduled["record"],
                        current,
                        minimum_gain=minimum_gain,
                    )
                    if decision["selected"] == "preserved_original":
                        # Reconcile assembly and persisted pipeline status locally.
                        resume_video_run(
                            run_id,
                            output_root=output_root,
                            batch_size=1,
                            continuous=False,
                            max_retries=max_retries,
                            max_provider_calls=approved_calls,
                            max_spend_usd=(
                                float(cfg.get("maxSpendUsd", 0) or 0) or None
                            ),
                            cost_per_second_usd=(
                                float(cfg.get("costPerSecondUsd", 0) or 0) or None
                            ),
                            use_continuity=True,
                        )
                    reports = [
                        deepcopy(item)
                        for item in (report.get("reports") or [])
                        if isinstance(item, dict)
                        and str(item.get("shotId") or (item.get("contract") or {}).get("shotId") or "")
                        != str(scheduled["shotId"])
                    ]
                    reports.append(deepcopy(decision["report"]))
                    queue = load_video_queue(workspace.media_dir) or queue
                    semantic = _save_semantic_summary(
                        workspace,
                        queue,
                        reports,
                        project_id=project_id,
                    )
                    repair_history.append(
                        {
                            "status": "completed",
                            "shotId": scheduled["shotId"],
                            **{key: value for key, value in decision.items() if key != "report"},
                        }
                    )
                except Exception as exc:
                    try:
                        workspace = RunWorkspace.open_existing(output_root, run_id)
                        queue = load_video_queue(workspace.media_dir) or {}
                        shot = _shot(queue, str(candidate.get("shotId") or (candidate.get("contract") or {}).get("shotId") or ""))
                        history = shot.get("semanticRetakeHistory") or []
                        if history:
                            _restore_preserved(
                                workspace,
                                queue,
                                shot,
                                history[-1],
                                reason=f"Retake execution failed: {exc}",
                            )
                            resume_video_run(
                                run_id,
                                output_root=output_root,
                                batch_size=1,
                                continuous=False,
                                max_retries=max_retries,
                                max_provider_calls=approved_calls,
                                use_continuity=True,
                            )
                    except Exception as restore_exc:
                        finished.setdefault("errors", []).append(
                            f"semantic retake restore: {restore_exc}"
                        )
                    repair_history.append(
                        {
                            "status": "failed_and_restored_if_possible",
                            "shotId": candidate.get("shotId"),
                            "error": str(exc),
                        }
                    )
                    break

        # Re-run local finishing once after picture lock and generate speech once.
        final_cfg = {**cfg, "semanticQa": False}
        final = original_finish(
            run_id,
            project_id=project_id,
            config=final_cfg,
            output_root=output_root,
        )
        if semantic:
            final["semantic"] = semantic
            quality = autonomous_studio._quality(
                final.get("result") or {},
                final.get("visual"),
                semantic,
            )
            workspace = RunWorkspace.open_existing(output_root, run_id)
            quality_path = workspace.write_json(
                "quality/project_quality_report.json", quality
            )
            workspace.register_artifact("projectQualityReport", quality_path)
            final["quality"] = quality
            job = final.get("job") or {}
            job["quality"] = quality
            job["repairHistory"] = repair_history
            job["updatedAt"] = utc_now()
            job_path = workspace.write_json("autonomous_job.json", job)
            workspace.register_artifact("autonomousJob", job_path)
            final["job"] = job
        else:
            final.setdefault("job", {})["repairHistory"] = repair_history
        final["repairHistory"] = repair_history
        final["closedLoop"] = {
            "enabled": auto_repair,
            "semanticAuthorized": use_semantic,
            "maximumRetakes": max_repairs,
            "retakesAttempted": len(
                [item for item in repair_history if item.get("status") == "completed"]
            ),
            "providerCallCeiling": approved_calls,
            "minimumVerifiedGain": minimum_gain,
        }
        final.setdefault("errors", []).extend(
            item for item in finished.get("errors") or [] if item not in (final.get("errors") or [])
        )
        return final

    autonomous_studio.start_autonomous_production = start_autonomous_production
    autonomous_studio.finish_autonomous_run = finish_autonomous_run
    autonomous_studio._closed_loop_installed = True


__all__ = ["AutonomousRepairError", "install_autonomous_closed_loop"]
