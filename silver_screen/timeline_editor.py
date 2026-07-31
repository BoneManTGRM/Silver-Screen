"""Local non-linear timeline planning and FFmpeg rendering."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .runtime import RunWorkspace, atomic_write_json, utc_now
from .transition_engine import assemble, settings
from .video_runtime import load_video_queue, save_video_queue


class TimelineEditorError(RuntimeError):
    """Raised when a timeline edit is invalid or cannot be rendered."""


TIMELINE_FILE = "editor_timeline.json"
SAFE_TRANSITIONS = {"fade", "fadeblack"}


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise TimelineEditorError("Timeline artifact escaped the production workspace")
    return resolved.relative_to(root).as_posix()


def _source(root: Path, value: Any) -> Path:
    path = Path(str(value or ""))
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise TimelineEditorError("Timeline source escaped the production workspace")
    if not resolved.exists():
        raise TimelineEditorError(f"Timeline source is missing: {resolved}")
    return resolved


def build_timeline(run_id: str, *, output_root: str = "runs") -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    queue = load_video_queue(workspace.media_dir)
    if queue is None:
        raise TimelineEditorError("The selected run has no durable video queue")
    items = []
    for shot in sorted(
        [
            item
            for item in queue.get("shots") or []
            if isinstance(item, dict) and item.get("status") == "verified"
        ],
        key=lambda item: int(item.get("order", 0) or 0),
    ):
        duration = float(
            shot.get("verifiedDurationSeconds")
            or shot.get("plannedDurationSeconds")
            or 8
        )
        items.append(
            {
                "timelineOrder": len(items) + 1,
                "shotId": shot.get("id"),
                "sourceOrder": shot.get("order"),
                "scene": (shot.get("sourceScene") or {}).get("number"),
                "chapter": (shot.get("sourceScene") or {}).get("chapter"),
                "sourcePath": shot.get("path"),
                "inSeconds": 0.0,
                "outSeconds": round(duration, 3),
                "durationSeconds": round(duration, 3),
                "transitionStyle": "fade",
                "transitionSeconds": 0.26,
                "locked": bool(shot.get("timelineLocked", False)),
                "label": (
                    (shot.get("shotBlueprint") or {}).get("type")
                    or f"Shot {shot.get('order')}"
                ),
                "notes": "",
            }
        )
    timeline = {
        "schemaVersion": 1,
        "runId": run_id,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "items": items,
        "render": {
            "status": "not_rendered",
            "outputPath": None,
        },
    }
    path = workspace.media_dir / TIMELINE_FILE
    atomic_write_json(path, timeline)
    workspace.register_artifact("editorTimeline", path)
    return timeline


def load_timeline(run_id: str, *, output_root: str = "runs") -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    path = workspace.media_dir / TIMELINE_FILE
    if not path.exists():
        return build_timeline(run_id, output_root=output_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TimelineEditorError("The saved editor timeline is unreadable") from exc
    if not isinstance(value, dict):
        raise TimelineEditorError("The saved editor timeline is invalid")
    return value


def normalize_timeline(
    timeline: dict[str, Any],
    *,
    workspace: RunWorkspace,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for raw in timeline.get("items") or []:
        if not isinstance(raw, dict):
            continue
        source = _source(workspace.media_dir, raw.get("sourcePath"))
        start = max(0.0, float(raw.get("inSeconds", 0) or 0))
        end = max(start + 0.2, float(raw.get("outSeconds", 0) or 0))
        style = str(raw.get("transitionStyle") or "fade").casefold()
        if style not in SAFE_TRANSITIONS:
            style = "fade"
        transition = max(0.0, min(1.2, float(raw.get("transitionSeconds", 0.26) or 0.26)))
        duration = end - start
        transition = min(transition, max(0.0, duration * 0.45))
        items.append(
            {
                "timelineOrder": int(raw.get("timelineOrder", len(items) + 1) or len(items) + 1),
                "shotId": str(raw.get("shotId") or source.stem),
                "sourceOrder": raw.get("sourceOrder"),
                "scene": raw.get("scene"),
                "chapter": raw.get("chapter"),
                "sourcePath": _relative(workspace.media_dir, source),
                "inSeconds": round(start, 3),
                "outSeconds": round(end, 3),
                "durationSeconds": round(duration, 3),
                "transitionStyle": style,
                "transitionSeconds": round(transition, 3),
                "locked": bool(raw.get("locked")),
                "label": str(raw.get("label") or source.stem)[:180],
                "notes": str(raw.get("notes") or "")[:1200],
            }
        )
    if not items:
        raise TimelineEditorError("The timeline contains no valid clips")
    items.sort(key=lambda item: (item["timelineOrder"], str(item["shotId"])))
    for index, item in enumerate(items, start=1):
        item["timelineOrder"] = index
        if index == len(items):
            item["transitionSeconds"] = 0.0
    return {
        "schemaVersion": 1,
        "runId": timeline.get("runId"),
        "createdAt": timeline.get("createdAt") or utc_now(),
        "updatedAt": utc_now(),
        "items": items,
        "render": dict(timeline.get("render") or {}),
    }


def save_timeline(
    run_id: str,
    timeline: dict[str, Any],
    *,
    output_root: str = "runs",
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    normalized = normalize_timeline(timeline, workspace=workspace)
    queue = load_video_queue(workspace.media_dir)
    if queue:
        locks = {
            str(item.get("shotId") or ""): bool(item.get("locked"))
            for item in normalized["items"]
        }
        for shot in queue.get("shots") or []:
            if isinstance(shot, dict):
                shot["timelineLocked"] = locks.get(str(shot.get("id") or ""), False)
        save_video_queue(workspace.media_dir, queue)
    path = workspace.media_dir / TIMELINE_FILE
    atomic_write_json(path, normalized)
    workspace.register_artifact("editorTimeline", path)
    return normalized


def _trim(
    ffmpeg: str,
    source: Path,
    destination: Path,
    start: float,
    end: float,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=600
    )
    if completed.returncode != 0 or not destination.exists():
        raise TimelineEditorError(
            "Timeline trim failed: " + completed.stderr[-1200:]
        )


def _write_edl(root: Path, timeline: dict[str, Any]) -> dict[str, str]:
    json_path = root / "editor_timeline_edl.json"
    csv_path = root / "editor_timeline_edl.csv"
    atomic_write_json(json_path, timeline)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timelineOrder",
                "shotId",
                "sourcePath",
                "inSeconds",
                "outSeconds",
                "durationSeconds",
                "transitionStyle",
                "transitionSeconds",
                "locked",
                "label",
                "notes",
            ],
        )
        writer.writeheader()
        for item in timeline.get("items") or []:
            writer.writerow({key: item.get(key) for key in writer.fieldnames})
    return {"json": str(json_path), "csv": str(csv_path)}


def render_timeline(
    run_id: str,
    timeline: dict[str, Any] | None = None,
    *,
    output_root: str = "runs",
    output_name: str = "editor_cut.mp4",
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    current = timeline or load_timeline(run_id, output_root=output_root)
    normalized = save_timeline(run_id, current, output_root=output_root)
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise TimelineEditorError("FFmpeg is unavailable")
    render_root = workspace.media_dir / "editor_render"
    render_root.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    for item in normalized["items"]:
        source = _source(workspace.media_dir, item["sourcePath"])
        destination = render_root / f"clip_{item['timelineOrder']:04d}.mp4"
        _trim(
            ffmpeg,
            source,
            destination,
            float(item["inSeconds"]),
            float(item["outSeconds"]),
        )
        clips.append(destination)
    transitions: list[dict[str, Any]] = []
    for previous, current in zip(normalized["items"], normalized["items"][1:]):
        transitions.append(
            {
                "fromShot": previous["shotId"],
                "toShot": current["shotId"],
                "fromOrder": previous["timelineOrder"],
                "toOrder": current["timelineOrder"],
                "relation": "editor",
                "style": previous["transitionStyle"],
                "durationSeconds": previous["transitionSeconds"],
                "promptDirective": "Manual editor timeline transition",
            }
        )
    target = workspace.media_dir / Path(output_name).name
    report = assemble(clips, target, transitions, settings("auto", analyze_frames=False))
    normalized["render"] = {
        "status": "complete",
        "renderedAt": utc_now(),
        "outputPath": str(target),
        "report": report,
    }
    save_timeline(run_id, normalized, output_root=output_root)
    edl = _write_edl(workspace.media_dir, normalized)
    workspace.register_artifact("editorCut", target)
    workspace.register_artifact("editorEdlJson", edl["json"])
    workspace.register_artifact("editorEdlCsv", edl["csv"])
    return {
        "runId": run_id,
        "outputPath": str(target),
        "timeline": normalized,
        "report": report,
        "edl": edl,
        "providerCallsMade": 0,
    }


__all__ = [
    "TimelineEditorError",
    "build_timeline",
    "load_timeline",
    "normalize_timeline",
    "render_timeline",
    "save_timeline",
]
