"""Media rendering for chapter cards, local previews, and real AI video."""

from __future__ import annotations

import io
import os
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from .ai_video import VideoGenerationError, generate_ai_video

HAS_PIL = False
HAS_VIDEO = False
Image = ImageDraw = ImageFont = ImageOps = None
np = None
ImageClip = concatenate_videoclips = None

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    HAS_PIL = True
except Exception:
    pass

try:
    import numpy as np

    try:
        from moviepy import ImageClip, concatenate_videoclips
    except Exception:
        from moviepy.editor import ImageClip, concatenate_videoclips
    HAS_VIDEO = True
except Exception:
    pass

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
VIDEO_MODES = {"cards", "preview", "preview-film", "ai-video"}


def media_capabilities() -> dict[str, Any]:
    return {
        "pillow": HAS_PIL,
        "localPreview": HAS_VIDEO,
        "aiVideo": bool(os.getenv("REPLICATE_API_TOKEN")),
        "aiProvider": "replicate",
        "aiModel": os.getenv("SILVER_SCREEN_VIDEO_MODEL", "google/veo-3-fast"),
        "modes": sorted(VIDEO_MODES),
    }


def _genre_color(genre: str) -> tuple[int, int, int]:
    return {
        "scifi": (18, 36, 72),
        "noir": (24, 24, 28),
        "drama": (58, 40, 48),
        "thriller": (56, 20, 26),
        "fantasy": (42, 30, 72),
        "horror": (28, 12, 18),
        "romance": (66, 38, 58),
        "western": (76, 52, 30),
    }.get((genre or "drama").lower().replace("-", ""), (30, 30, 50))


def _font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _read_upload(upload: Any) -> bytes:
    if isinstance(upload, (str, os.PathLike)):
        path = Path(upload)
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("Image exceeds 20 MB")
        return path.read_bytes()
    size = getattr(upload, "size", None)
    if isinstance(size, int) and size > MAX_UPLOAD_BYTES:
        raise ValueError("Image exceeds 20 MB")
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if hasattr(upload, "seek"):
        upload.seek(0)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Image exceeds 20 MB")
    return data


def _load_image(upload: Any):
    image = Image.open(io.BytesIO(_read_upload(upload)))
    image.load()
    return image.convert("RGB")


def _draw_wrapped(draw, text: str, xy: tuple[int, int], font, width: int, max_lines: int) -> None:
    words = (text or "").replace("\n", " ").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        try:
            measured = draw.textbbox((0, 0), candidate, font=font)[2]
        except Exception:
            measured = len(candidate) * 10
        if current and measured > width:
            lines.append(current)
            current = word
        else:
            current = candidate
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines:
        lines[-1] = textwrap.shorten(lines[-1], width=max(12, len(lines[-1]) - 1), placeholder="...")
    y = xy[1]
    for line in lines:
        draw.text((xy[0], y), line, font=font, fill=(230, 230, 230))
        y += 40


def _make_card(state: dict[str, Any], chapter: dict[str, Any], summary: str, portrait=None):
    size = (1280, 720)
    if portrait is not None:
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        card = ImageOps.fit(portrait, size, method=resampling, centering=(0.5, 0.45))
        card = Image.alpha_composite(card.convert("RGBA"), Image.new("RGBA", size, (0, 0, 0, 150))).convert("RGB")
    else:
        card = Image.new("RGB", size, _genre_color(str(state.get("genre") or "drama")))
    draw = ImageDraw.Draw(card)
    accent = (226, 216, 190)
    draw.rectangle((0, 0, 18, size[1]), fill=accent)
    draw.text((70, 68), str(state.get("title") or "Silver-Screen")[:80], font=_font(56, True), fill=accent)
    draw.text((72, 160), str(chapter.get("title") or "Chapter")[:100], font=_font(38, True), fill=(255, 255, 255))
    _draw_wrapped(draw, summary, (72, 245), _font(28), size[0] - 150, 8)
    draw.text((72, 650), "SILVER-SCREEN | REPARODYNAMICS | TGRM", font=_font(20, True), fill=accent)
    return card


def _chapter_summary(state: dict[str, Any], chapter_number: int) -> str:
    scenes = [
        scene
        for scene in state.get("scenes") or []
        if isinstance(scene, dict) and int(scene.get("chapter", 1)) == chapter_number
    ]
    return " ".join(str(scene.get("summary") or "") for scene in scenes[:3]) or str(state.get("premise") or "")


def _with_duration(clip, seconds: float):
    return clip.with_duration(seconds) if hasattr(clip, "with_duration") else clip.set_duration(seconds)


def _write_preview(clip, path: Path) -> Path | None:
    for kwargs in (
        {"fps": 12, "codec": "libx264", "audio": False, "logger": None},
        {"fps": 12, "codec": "libx264", "audio": False},
    ):
        try:
            clip.write_videofile(str(path), **kwargs)
            if path.exists() and path.stat().st_size > 0:
                return path
        except Exception:
            continue
    return None


def process_media(
    state: dict[str, Any] | None = None,
    images: list[Any] | None = None,
    voices: list[Any] | None = None,
    out_dir: str | os.PathLike[str] | None = None,
    max_chapters: int = 4,
    video_mode: str = "cards",
) -> dict[str, Any]:
    """Create requested media.

    ``preview`` and ``preview-film`` animate static chapter cards locally.
    ``ai-video`` calls a real generative model and fails explicitly when the
    provider is not configured; it never disguises a card animation as AI video.
    """

    state = state or {}
    mode = video_mode if video_mode in VIDEO_MODES else "cards"
    output = Path(out_dir or tempfile.mkdtemp(prefix="silverscreen_media_"))
    output.mkdir(parents=True, exist_ok=True)

    if mode == "ai-video":
        try:
            result = generate_ai_video(
                state,
                output,
                scene_limit=max_chapters,
                duration=int(os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8")),
            )
            result.update(
                {
                    "mode": mode,
                    "card_paths": [],
                    "out_dir": str(output.resolve()),
                    "portraits_used": 0,
                    "voices_count": len(voices or []),
                    "capabilities": media_capabilities(),
                }
            )
            return result
        except VideoGenerationError as exc:
            return {
                "ok": False,
                "status": "failed",
                "mode": mode,
                "chapter_paths": [],
                "card_paths": [],
                "video_paths": [],
                "hero_path": None,
                "final_video_path": None,
                "out_dir": str(output.resolve()),
                "portraits_used": 0,
                "voices_count": len(voices or []),
                "warnings": [],
                "error": str(exc),
                "note": "AI video was requested but no real video was produced.",
                "capabilities": media_capabilities(),
            }

    warnings: list[str] = []
    result: dict[str, Any] = {
        "ok": False,
        "status": "initializing",
        "mode": mode,
        "chapter_paths": [],
        "card_paths": [],
        "video_paths": [],
        "hero_path": None,
        "final_video_path": None,
        "out_dir": str(output.resolve()),
        "portraits_used": 0,
        "voices_count": len(voices or []),
        "warnings": warnings,
        "error": None,
        "capabilities": media_capabilities(),
    }
    if voices:
        warnings.append("Voice files were inventoried but not synthesized.")
    if not HAS_PIL:
        result.update(status="failed", error="Pillow is unavailable", note="No media was produced.")
        return result

    portraits = []
    for index, upload in enumerate(images or []):
        try:
            portraits.append(_load_image(upload))
        except Exception as exc:
            warnings.append(f"Portrait {index + 1} was skipped: {exc}")
    result["portraits_used"] = len(portraits)
    chapters = [item for item in state.get("chapters") or [] if isinstance(item, dict)] or [{"number": 1, "title": "Chapter 1"}]
    limit = max(1, min(int(max_chapters), len(chapters), 12))
    clips = []
    for index, chapter in enumerate(chapters[:limit]):
        number = int(chapter.get("number", index + 1) or index + 1)
        portrait = portraits[index % len(portraits)] if portraits else None
        card = _make_card(state, chapter, _chapter_summary(state, number), portrait)
        card_path = output / f"chapter_{number:02d}.png"
        card.save(card_path, "PNG", optimize=True)
        result["card_paths"].append(str(card_path.resolve()))
        result["chapter_paths"].append(str(card_path.resolve()))
        if mode in {"preview", "preview-film"}:
            if not HAS_VIDEO or ImageClip is None or np is None:
                warnings.append("MoviePy is unavailable; static cards were retained.")
                continue
            clip = _with_duration(ImageClip(np.array(card)), 2.8)
            video_path = output / f"preview_{number:02d}.mp4"
            written = _write_preview(clip, video_path)
            if written:
                result["video_paths"].append(str(written.resolve()))
                result["chapter_paths"][-1] = str(written.resolve())
                clips.append(clip)
            else:
                clip.close()
                warnings.append(f"Preview {number} could not be encoded.")

    if mode == "preview-film" and clips and concatenate_videoclips is not None:
        hero = None
        try:
            hero = concatenate_videoclips(clips, method="compose")
            written = _write_preview(hero, output / "preview_film.mp4")
            if written:
                result["hero_path"] = str(written.resolve())
                result["final_video_path"] = str(written.resolve())
        finally:
            if hero is not None:
                hero.close()
    for clip in clips:
        try:
            clip.close()
        except Exception:
            pass

    result["ok"] = bool(result["card_paths"])
    result["status"] = "complete" if result["ok"] else "failed"
    result["note"] = (
        f"Generated {len(result['card_paths'])} static card(s) and {len(result['video_paths'])} local preview clip(s). "
        "These previews are not AI-generated footage."
    )
    return result


def process_uploads(images=None, voices=None, film=None, out_dir=None):
    return process_media(film or {}, images=images, voices=voices, out_dir=out_dir, video_mode="cards")
