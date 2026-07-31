"""Provider-free storyboard and timing animatic generation."""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .runtime import atomic_write_json, atomic_write_text, utc_now


class PrevisualizationError(RuntimeError):
    """Raised when a local animatic cannot be rendered."""


def _clean(value: Any, limit: int = 900) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def build_animatic_manifest(preview: dict[str, Any]) -> dict[str, Any]:
    ledger = preview.get("promptLedger") or {}
    entries = [
        item for item in ledger.get("entries") or [] if isinstance(item, dict)
    ]
    shots: list[dict[str, Any]] = []
    cursor = 0.0
    for entry in entries:
        blueprint = entry.get("blueprint") or {}
        duration = max(0.5, float(blueprint.get("durationSeconds", 8) or 8))
        shots.append(
            {
                "shotId": entry.get("shotId"),
                "order": int(entry.get("order", len(shots) + 1) or len(shots) + 1),
                "scene": entry.get("scene"),
                "chapter": entry.get("chapter"),
                "startSeconds": round(cursor, 3),
                "endSeconds": round(cursor + duration, 3),
                "durationSeconds": round(duration, 3),
                "shotType": _clean(blueprint.get("type"), 100),
                "objective": _clean(blueprint.get("description"), 1200),
                "dialogue": _clean(blueprint.get("dialogue"), 900),
                "override": _clean(blueprint.get("override"), 900),
                "audioStrategy": (
                    (preview.get("shotDirection") or {}).get("audioStrategy")
                ),
                "promptHash": entry.get("promptHashWithoutContinuity"),
                "negativePromptHash": entry.get("negativePromptHash"),
            }
        )
        cursor += duration
    return {
        "schemaVersion": 1,
        "createdAt": utc_now(),
        "title": ((preview.get("state") or {}).get("title") or "Untitled"),
        "logline": ((preview.get("state") or {}).get("logline") or ""),
        "runtimeSeconds": round(cursor, 3),
        "shots": shots,
        "providerCallsMade": 0,
        "bindingContract": {
            "previewFingerprint": preview.get("fingerprint"),
            "ledgerHash": ledger.get("ledgerHash"),
            "plannedShots": len(shots),
        },
    }


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _font(size: int):
    try:
        from PIL import ImageFont

        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ):
            if Path(path).exists():
                return ImageFont.truetype(path, size=size)
        return ImageFont.load_default()
    except Exception:
        return None


def _wrap(text: str, width: int) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if len(candidate) > width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _card(path: Path, title: str, shot: dict[str, Any]) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1280, 720), (18, 20, 26))
    draw = ImageDraw.Draw(image)
    title_font = _font(42)
    heading_font = _font(30)
    body_font = _font(24)
    small_font = _font(19)
    draw.text((54, 38), _clean(title, 80), fill=(238, 238, 242), font=title_font)
    heading = (
        f"SHOT {shot.get('order')}  |  SCENE {shot.get('scene')}  |  "
        f"{str(shot.get('shotType') or 'SHOT').upper()}"
    )
    draw.text((54, 106), heading, fill=(214, 175, 82), font=heading_font)
    y = 170
    for line in _wrap(str(shot.get("objective") or "No shot objective"), 76)[:8]:
        draw.text((54, y), line, fill=(230, 232, 236), font=body_font)
        y += 34
    dialogue = str(shot.get("dialogue") or "").strip()
    if dialogue:
        y += 14
        draw.text((54, y), "DIALOGUE / AUDIO CUE", fill=(150, 191, 235), font=small_font)
        y += 30
        for line in _wrap(dialogue, 82)[:5]:
            draw.text((54, y), line, fill=(204, 216, 230), font=small_font)
            y += 28
    footer = (
        f"{float(shot.get('startSeconds', 0) or 0):.1f}s–"
        f"{float(shot.get('endSeconds', 0) or 0):.1f}s  |  "
        f"{float(shot.get('durationSeconds', 0) or 0):.1f}s  |  "
        f"{shot.get('shotId')}"
    )
    draw.rectangle((0, 668, 1280, 720), fill=(10, 11, 15))
    draw.text((54, 682), footer, fill=(172, 176, 185), font=small_font)
    image.save(path, format="PNG", optimize=True)


def write_storyboard_html(
    manifest: dict[str, Any], destination: str | os.PathLike[str]
) -> str:
    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    cards = []
    for shot in manifest.get("shots") or []:
        cards.append(
            "<article><h2>Shot {order} · Scene {scene}</h2>"
            "<p class='meta'>{start:.1f}s–{end:.1f}s · {type}</p>"
            "<p>{objective}</p>{dialogue}</article>".format(
                order=html.escape(str(shot.get("order"))),
                scene=html.escape(str(shot.get("scene"))),
                start=float(shot.get("startSeconds", 0) or 0),
                end=float(shot.get("endSeconds", 0) or 0),
                type=html.escape(str(shot.get("shotType") or "shot")),
                objective=html.escape(str(shot.get("objective") or "")),
                dialogue=(
                    "<blockquote>" + html.escape(str(shot.get("dialogue"))) + "</blockquote>"
                    if shot.get("dialogue")
                    else ""
                ),
            )
        )
    document = """<!doctype html><html><head><meta charset='utf-8'>
<title>{title} · Silver-Screen Animatic</title><style>
body{{background:#101219;color:#eef0f4;font:16px system-ui;margin:0;padding:36px}}
header{{max-width:1100px;margin:auto auto 24px}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px;max-width:1100px;margin:auto}}
article{{background:#1b1e28;border:1px solid #303545;border-radius:14px;padding:20px;min-height:190px}}h1,h2{{margin-top:0}}h2{{font-size:20px;color:#d8b46b}}.meta{{color:#96bce9}}blockquote{{border-left:3px solid #6f9ed0;margin:18px 0 0;padding-left:14px;color:#cdd9e8}}</style></head>
<body><header><h1>{title}</h1><p>{logline}</p><p>{runtime:.1f}s · {count} planned shots · provider-free previsualization</p></header><main>{cards}</main></body></html>""".format(
        title=html.escape(str(manifest.get("title") or "Untitled")),
        logline=html.escape(str(manifest.get("logline") or "")),
        runtime=float(manifest.get("runtimeSeconds", 0) or 0),
        count=len(manifest.get("shots") or []),
        cards="".join(cards),
    )
    atomic_write_text(path, document)
    return str(path)


def render_animatic(
    manifest: dict[str, Any],
    output_dir: str | os.PathLike[str],
    *,
    seconds_per_card_cap: float = 3.0,
) -> dict[str, Any]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "animatic_manifest.json"
    atomic_write_json(manifest_path, manifest)
    html_path = Path(write_storyboard_html(manifest, root / "storyboard.html"))
    cards_dir = root / "storyboard_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    concat_lines: list[str] = []
    for shot in manifest.get("shots") or []:
        path = cards_dir / f"shot_{int(shot.get('order', 0) or 0):04d}.png"
        _card(path, str(manifest.get("title") or "Untitled"), shot)
        duration = max(
            0.75,
            min(seconds_per_card_cap, float(shot.get("durationSeconds", 1) or 1) / 3),
        )
        concat_lines.append(f"file '{path.as_posix()}'")
        concat_lines.append(f"duration {duration:.3f}")
    video_path: Path | None = None
    ffmpeg = _ffmpeg()
    if ffmpeg and concat_lines:
        concat = root / "animatic_concat.txt"
        atomic_write_text(concat, "\n".join(concat_lines + [concat_lines[-2]]) + "\n")
        target = root / "director_animatic.mp4"
        command = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-vf",
            "fps=24,format=yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(target),
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=300
        )
        if completed.returncode == 0 and target.exists():
            video_path = target
    return {
        "manifestPath": str(manifest_path),
        "storyboardPath": str(html_path),
        "videoPath": str(video_path) if video_path else None,
        "providerCallsMade": 0,
    }


__all__ = [
    "PrevisualizationError",
    "build_animatic_manifest",
    "render_animatic",
    "write_storyboard_html",
]
