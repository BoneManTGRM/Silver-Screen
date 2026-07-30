"""Safe chapter-card and preview-reel rendering.

Media is optional. A missing codec or library degrades to PNG cards and returns
structured warnings instead of failing the story pipeline.
"""

from __future__ import annotations

import io
import os
import tempfile
import textwrap
from pathlib import Path
from typing import Any

HAS_PIL = False
HAS_VIDEO = False
Image = ImageDraw = ImageFont = ImageOps = None
np = None
ImageClip = concatenate_videoclips = None

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    import numpy as np

    try:
        from moviepy import ImageClip, concatenate_videoclips
    except Exception:
        from moviepy.editor import ImageClip, concatenate_videoclips
    HAS_VIDEO = True
except Exception:
    HAS_VIDEO = False

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
VIDEO_MODES = {"cards", "chapters", "hero"}


def media_capabilities() -> dict[str, Any]:
    return {
        "pillow": HAS_PIL,
        "video": HAS_VIDEO,
        "modes": sorted(VIDEO_MODES),
    }


def _genre_color(genre: str) -> tuple[int, int, int]:
    normalized = (genre or "drama").lower().replace("-", "")
    return {
        "scifi": (18, 36, 72),
        "noir": (24, 24, 28),
        "drama": (58, 40, 48),
        "thriller": (56, 20, 26),
        "fantasy": (42, 30, 72),
        "horror": (28, 12, 18),
        "romance": (66, 38, 58),
        "western": (76, 52, 30),
    }.get(normalized, (30, 30, 50))


def _font(size: int = 40, bold: bool = False):
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _read_upload(upload: Any) -> bytes:
    if isinstance(upload, (str, os.PathLike)):
        path = Path(upload)
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError(f"Image exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        return path.read_bytes()
    size = getattr(upload, "size", None)
    if isinstance(size, int) and size > MAX_UPLOAD_BYTES:
        raise ValueError(f"Image exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not hasattr(upload, "read"):
        raise TypeError("Unsupported image input")
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if hasattr(upload, "seek"):
        upload.seek(0)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Image exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    return data


def _load_image(upload: Any):
    data = _read_upload(upload)
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGB")


def _fit_background(image, size: tuple[int, int]):
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    return ImageOps.fit(image, size, method=resampling, centering=(0.5, 0.45))


def _draw_wrapped(
    draw,
    text: str,
    xy: tuple[int, int],
    *,
    font,
    fill: tuple[int, int, int],
    width: int,
    line_spacing: int,
    max_lines: int,
) -> int:
    words = (text or "").replace("\n", " ").split()
    if not words:
        return xy[1]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        try:
            bbox = draw.textbbox((0, 0), candidate, font=font)
            candidate_width = bbox[2] - bbox[0]
        except Exception:
            candidate_width = len(candidate) * 10
        if current and candidate_width > width:
            lines.append(current)
            current = word
        else:
            current = candidate
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and words:
        lines[-1] = textwrap.shorten(lines[-1], width=max(10, len(lines[-1]) - 1), placeholder="...")
    y = xy[1]
    for line in lines:
        draw.text((xy[0], y), line, font=font, fill=fill)
        y += line_spacing
    return y


def _make_card(
    title: str,
    chapter_title: str,
    summary: str,
    *,
    genre: str,
    portrait=None,
    size: tuple[int, int] = (1280, 720),
):
    background = _genre_color(genre)
    if portrait is not None:
        card = _fit_background(portrait, size)
        overlay = Image.new("RGBA", size, (0, 0, 0, 145))
        card = Image.alpha_composite(card.convert("RGBA"), overlay).convert("RGB")
    else:
        card = Image.new("RGB", size, background)

    draw = ImageDraw.Draw(card)
    accent = (226, 216, 190)
    muted = (224, 224, 224)
    title_font = _font(56, bold=True)
    chapter_font = _font(38, bold=True)
    body_font = _font(28)
    draw.rectangle((0, 0, 18, size[1]), fill=accent)
    draw.text((70, 68), title[:80], font=title_font, fill=accent)
    draw.text((72, 160), chapter_title[:100], font=chapter_font, fill=(255, 255, 255))
    _draw_wrapped(
        draw,
        summary,
        (72, 245),
        font=body_font,
        fill=muted,
        width=size[0] - 150,
        line_spacing=40,
        max_lines=8,
    )
    draw.text(
        (72, size[1] - 70),
        "SILVER-SCREEN | REPARODYNAMICS | TGRM",
        font=_font(20, bold=True),
        fill=accent,
    )
    return card


def _clip_with_duration(clip, seconds: float):
    if hasattr(clip, "with_duration"):
        return clip.with_duration(seconds)
    return clip.set_duration(seconds)


def _write_video(clip, path: Path, fps: int = 12) -> Path | None:
    attempts = [
        {"fps": fps, "codec": "libx264", "audio": False, "logger": None},
        {"fps": fps, "codec": "libx264", "audio": False},
    ]
    for kwargs in attempts:
        try:
            clip.write_videofile(str(path), **kwargs)
            if path.exists() and path.stat().st_size > 0:
                return path
        except Exception:
            continue
    return None


def _chapter_summary(state: dict[str, Any], chapter_number: int) -> str:
    scenes = [
        scene
        for scene in state.get("scenes") or []
        if isinstance(scene, dict) and int(scene.get("chapter", 1)) == chapter_number
    ]
    summaries = [str(scene.get("summary") or "") for scene in scenes[:3]]
    return " ".join(summary for summary in summaries if summary) or str(
        state.get("premise") or ""
    )


def process_media(
    state: dict[str, Any] | None = None,
    images: list[Any] | None = None,
    voices: list[Any] | None = None,
    out_dir: str | os.PathLike[str] | None = None,
    max_chapters: int = 4,
    video_mode: str = "cards",
) -> dict[str, Any]:
    """Render chapter cards and, when requested, short preview videos."""

    state = state or {}
    images = list(images or [])
    voices = list(voices or [])
    mode = video_mode if video_mode in VIDEO_MODES else "cards"
    warnings: list[str] = []
    result: dict[str, Any] = {
        "ok": False,
        "status": "initializing",
        "mode": mode,
        "chapter_paths": [],
        "card_paths": [],
        "video_paths": [],
        "hero_path": None,
        "out_dir": None,
        "portraits_used": 0,
        "voices_count": len(voices),
        "warnings": warnings,
        "note": "",
        "error": None,
        "capabilities": media_capabilities(),
    }

    if voices:
        warnings.append(
            "Voice files were inventoried but not synthesized. Silver-Screen does not impersonate or clone voices in this release."
        )
    if not HAS_PIL or Image is None:
        result.update(
            {
                "status": "unavailable",
                "note": "Pillow is unavailable, so media rendering was skipped.",
                "error": "missing_pillow",
            }
        )
        return result

    try:
        output = Path(out_dir or tempfile.mkdtemp(prefix="silverscreen_media_"))
        output.mkdir(parents=True, exist_ok=True)
        result["out_dir"] = str(output.resolve())

        portraits = []
        for index, upload in enumerate(images):
            try:
                portraits.append(_load_image(upload))
            except Exception as exc:
                warnings.append(f"Portrait {index + 1} was skipped: {exc}")
        result["portraits_used"] = len(portraits)

        title = str(state.get("title") or "Silver-Screen")
        genre = str(state.get("genre") or "drama")
        chapters = [chapter for chapter in state.get("chapters") or [] if isinstance(chapter, dict)]
        if not chapters:
            chapters = [{"number": 1, "title": "Chapter 1"}]
        chapter_limit = max(1, min(int(max_chapters), len(chapters), 12))
        clips = []

        for index, chapter in enumerate(chapters[:chapter_limit]):
            chapter_number = int(chapter.get("number", index + 1) or index + 1)
            chapter_title = str(chapter.get("title") or f"Chapter {chapter_number}")
            summary = _chapter_summary(state, chapter_number)
            portrait = portraits[index % len(portraits)] if portraits else None
            card = _make_card(
                title,
                chapter_title,
                summary,
                genre=genre,
                portrait=portrait,
            )
            card_path = output / f"chapter_{chapter_number:02d}.png"
            card.save(card_path, format="PNG", optimize=True)
            result["card_paths"].append(str(card_path.resolve()))
            result["chapter_paths"].append(str(card_path.resolve()))

            if mode in {"chapters", "hero"}:
                if not HAS_VIDEO or ImageClip is None or np is None:
                    if "MoviePy or NumPy is unavailable; PNG cards were retained." not in warnings:
                        warnings.append("MoviePy or NumPy is unavailable; PNG cards were retained.")
                    continue
                try:
                    clip = _clip_with_duration(ImageClip(np.array(card)), 2.8)
                    video_path = output / f"chapter_{chapter_number:02d}.mp4"
                    written = _write_video(clip, video_path, fps=12)
                    if written is not None:
                        result["video_paths"].append(str(written.resolve()))
                        result["chapter_paths"][-1] = str(written.resolve())
                        clips.append(clip)
                    else:
                        clip.close()
                        warnings.append(
                            f"Chapter {chapter_number} video encoding failed; the PNG card remains available."
                        )
                except Exception as exc:
                    warnings.append(
                        f"Chapter {chapter_number} video encoding failed; the PNG card remains available: {exc}"
                    )

        if mode == "hero" and clips and concatenate_videoclips is not None:
            hero = None
            try:
                hero = concatenate_videoclips(clips, method="compose")
                hero_path = output / "hero_reel.mp4"
                written = _write_video(hero, hero_path, fps=12)
                if written is not None:
                    result["hero_path"] = str(written.resolve())
                else:
                    warnings.append("Hero reel encoding failed; chapter artifacts remain available.")
            except Exception as exc:
                warnings.append(f"Hero reel encoding failed: {exc}")
            finally:
                if hero is not None:
                    try:
                        hero.close()
                    except Exception:
                        pass

        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass

        result["ok"] = bool(result["card_paths"])
        result["status"] = "complete" if result["ok"] else "empty"
        result["note"] = (
            f"Generated {len(result['card_paths'])} chapter card(s), "
            f"{len(result['video_paths'])} chapter video(s), and "
            f"{'one hero reel' if result['hero_path'] else 'no hero reel'}."
        )
    except Exception as exc:
        result.update(
            {
                "status": "error",
                "error": str(exc),
                "note": f"Media failed safely: {exc}",
            }
        )
    return result


def process_uploads(images=None, voices=None, film=None, out_dir=None):
    """Compatibility wrapper retained for earlier integrations."""

    return process_media(
        film or {},
        images=images,
        voices=voices,
        out_dir=out_dir,
        video_mode="cards",
    )
