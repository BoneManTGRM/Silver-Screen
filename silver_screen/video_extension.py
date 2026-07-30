"""Extend an existing verified production without discarding accepted clips."""

from __future__ import annotations

import time
from typing import Any, Callable

from .media import process_media
from .pipeline import (
    PipelineError,
    _persist_and_finalize,
    _pipeline_status,
    _video_progress,
)
from .runtime import RunWorkspace, load_run

ProgressCallback = Callable[[str, int, str], None]


def extend_video_run(
    run_id: str,
    *,
    target_runtime_seconds: int,
    max_shots: int,
    output_root: str | None = "runs",
    batch_size: int = 1,
    continuous: bool = False,
    max_retries: int | None = None,
    max_provider_calls: int | None = None,
    use_continuity: bool | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Extend a saved video queue to a larger target and preserve every valid clip."""

    target = max(4, min(5400, int(target_runtime_seconds)))
    shot_limit = max(1, min(900, int(max_shots)))
    checkpoint_size = max(1, min(128, int(batch_size)))

    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    state = result.get("state") or {}
    if not state:
        raise PipelineError(
            "The saved run has no persisted film state", run_id=run_id
        )

    options = dict(result.get("options") or {})
    options.update(
        {
            "targetRuntimeSeconds": target,
            "videoMaxShots": shot_limit,
            "videoBatchSize": checkpoint_size,
            "videoContinuous": bool(continuous),
        }
    )
    if max_retries is not None:
        options["videoMaxRetries"] = int(max_retries)
    if max_provider_calls is not None:
        options["videoMaxProviderCalls"] = int(max_provider_calls)
    if use_continuity is not None:
        options["videoUseContinuity"] = bool(use_continuity)

    workspace.update(
        status="running",
        stage="video_extension",
        progress=max(72, int(workspace.manifest.get("progress", 72) or 72)),
        error=None,
        extra={"options": options},
    )
    started = time.perf_counter()

    try:
        media = process_media(
            state,
            out_dir=workspace.media_dir,
            video_mode="ai-video",
            target_runtime_seconds=target,
            video_max_shots=shot_limit,
            video_batch_size=checkpoint_size,
            video_max_retries=options.get("videoMaxRetries"),
            video_max_provider_calls=options.get("videoMaxProviderCalls"),
            video_max_spend_usd=options.get("videoMaxSpendUsd"),
            video_cost_per_second_usd=options.get("videoCostPerSecondUsd"),
            video_continuous=bool(continuous),
            video_resume=True,
            video_use_continuity=options.get("videoUseContinuity"),
            video_progress=_video_progress(progress, workspace),
        )
        status = _pipeline_status("ai-video", media)
        if status == "failed":
            raise PipelineError(
                str(media.get("error") or "Video extension failed"),
                run_id=run_id,
            )

        result["status"] = status
        result["options"] = options
        result["media"] = media
        result["videoMetrics"] = media.get("metrics") or {}
        result["videoMsil"] = media.get("msil") or {}
        result.setdefault("timings", {})["lastExtensionSeconds"] = round(
            time.perf_counter() - started, 4
        )
        result["timings"]["extensionCount"] = int(
            result["timings"].get("extensionCount", 0) or 0
        ) + 1
        result["warnings"] = list(
            dict.fromkeys(
                [
                    *(result.get("warnings") or []),
                    *(media.get("warnings") or []),
                ]
            )
        )
        _persist_and_finalize(workspace, result)
        return result
    except PipelineError:
        raise
    except Exception as exc:
        workspace.fail(str(exc))
        raise PipelineError(
            f"Video extension failed: {exc}", run_id=run_id
        ) from exc
