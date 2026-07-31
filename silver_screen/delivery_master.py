"""Create normalized delivery masters from accepted Silver-Screen footage."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .media_probe import probe_media
from .runtime import RunWorkspace, load_run, utc_now


class DeliveryMasterError(RuntimeError):
    """Raised when a delivery master cannot be produced."""


def _ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _source(result: dict[str, Any]) -> str | None:
    media = result.get("media") or {}
    voice = media.get("voice") or {}
    return (
        voice.get("captionedVideoPath")
        or voice.get("outputVideoPath")
        or media.get("final_cinematic_video_path")
        or media.get("final_video_path")
        or media.get("partial_cinematic_video_path")
        or media.get("partial_video_path")
        or media.get("hero_path")
    )


def create_delivery_master(
    run_id: str,
    *,
    output_root: str = "runs",
    profile: str = "1080p",
) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    value = _source(result)
    if not value:
        raise DeliveryMasterError("No assembled source video is available")
    source = Path(str(value))
    if not source.is_absolute():
        candidate = (workspace.path / source).resolve()
        source = candidate if candidate.exists() else (workspace.media_dir / source).resolve()
    source = source.resolve()
    if not source.exists():
        raise DeliveryMasterError(f"Delivery source is missing: {source}")
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise DeliveryMasterError("FFmpeg is unavailable")
    selected = str(profile or "1080p").strip().casefold()
    if selected not in {"1080p", "4k", "source"}:
        selected = "1080p"
    dimensions = {
        "1080p": (1920, 1080),
        "4k": (3840, 2160),
        "source": (None, None),
    }[selected]
    target = workspace.media_dir / f"final_delivery_master_{selected}.mp4"
    probe = probe_media(source)
    video_filter = ["fps=24"]
    if dimensions[0]:
        width, height = dimensions
        video_filter.extend(
            [
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
            ]
        )
    video_filter.append("format=yuv420p")
    command = [ffmpeg, "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?"]
    command += [
        "-vf",
        ",".join(video_filter),
        "-c:v",
        "libx264",
        "-preset",
        os.getenv("SILVER_SCREEN_MASTER_PRESET", "medium"),
        "-crf",
        os.getenv("SILVER_SCREEN_MASTER_CRF", "17"),
        "-profile:v",
        "high",
        "-level",
        "4.2" if selected != "4k" else "5.1",
        "-pix_fmt",
        "yuv420p",
    ]
    if bool(probe.get("audio")):
        command += [
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-ar",
            "48000",
        ]
    command += ["-movflags", "+faststart", str(target)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if completed.returncode != 0 or not target.exists():
        raise DeliveryMasterError(
            "FFmpeg delivery mastering failed: " + completed.stderr[-1600:]
        )
    output_probe = probe_media(target)
    workspace.register_artifact("deliveryMaster", target)
    return {
        "runId": run_id,
        "createdAt": utc_now(),
        "profile": selected,
        "sourcePath": str(source),
        "outputPath": str(target),
        "input": probe,
        "output": output_probe,
        "audioNormalized": bool(probe.get("audio")),
        "providerCallsMade": 0,
    }


__all__ = ["DeliveryMasterError", "create_delivery_master"]
