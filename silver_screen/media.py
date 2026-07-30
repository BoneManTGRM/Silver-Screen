"""media.py — chapter cards + optional reels for Streamlit.

Compatible with moviepy 1.x and 2.x. Always produces PNG cards; video when possible.
Never raises into Streamlit.
"""

from __future__ import annotations

import io
import os
import tempfile
from typing import Any, Dict, List, Optional

HAS_MEDIA = False
ImageClip = concatenate_videoclips = ColorClip = None
np = None
Image = ImageDraw = ImageFont = None

try:
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    try:
        from moviepy import ImageClip, concatenate_videoclips, ColorClip  # v2
    except Exception:
        from moviepy.editor import ImageClip, concatenate_videoclips, ColorClip  # v1
    HAS_MEDIA = True
except Exception:
    HAS_MEDIA = False


def _genre_color(genre: str):
    g = (genre or "drama").lower().replace("-", "")
    return {
        "scifi": (20, 40, 90),
        "noir": (25, 25, 30),
        "drama": (55, 40, 50),
        "thriller": (50, 20, 25),
        "fantasy": (40, 30, 70),
        "horror": (25, 10, 15),
        "romance": (48, 32, 48),
    }.get(g, (30, 30, 50))


def _font(size: int = 40):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _make_card(text: str, size=(1280, 720), bg=(30, 30, 50), accent=(210, 200, 180)):
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    font = _font(40)
    words = (text or "").replace("\n", " ").split()
    lines, line = [], ""
    for w in words:
        test = (line + " " + w).strip()
        if len(test) > 42:
            if line:
                lines.append(line)
            line = w
        else:
            line = test
    if line:
        lines.append(line)
    y = size[1] // 2 - max(1, len(lines)) * 28
    for ln in lines[:10]:
        try:
            bbox = draw.textbbox((0, 0), ln, font=font)
            x = (size[0] - (bbox[2] - bbox[0])) // 2
        except Exception:
            x = 40
        draw.text((x, y), ln, fill=accent, font=font)
        y += 52
    return img


def _load_upload(upload) -> Optional[Any]:
    try:
        if hasattr(upload, "read"):
            data = upload.read()
            if hasattr(upload, "seek"):
                upload.seek(0)
            return Image.open(io.BytesIO(data)).convert("RGB")
        return Image.open(upload).convert("RGB")
    except Exception:
        return None


def _clip_with_duration(clip, seconds: float):
    if hasattr(clip, "with_duration"):
        return clip.with_duration(seconds)
    return clip.set_duration(seconds)


def _write_video(clip, path: str, fps: int = 12) -> None:
    """Write video without moviepy-version-specific kwargs that break v2."""
    try:
        clip.write_videofile(path, fps=fps, codec="libx264", audio=False, logger=None)
        return
    except TypeError:
        pass
    try:
        clip.write_videofile(path, fps=fps, codec="libx264", audio=False)
        return
    except Exception:
        pass
    alt = path.rsplit(".", 1)[0] + ".webm"
    try:
        clip.write_videofile(alt, fps=fps, codec="libvpx", audio=False, logger=None)
        if alt != path and os.path.exists(alt):
            os.replace(alt, path)
    except TypeError:
        clip.write_videofile(alt, fps=fps, codec="libvpx", audio=False)
        if alt != path and os.path.exists(alt):
            os.replace(alt, path)


def process_media(
    state: Optional[Dict[str, Any]] = None,
    images: Optional[List[Any]] = None,
    voices: Optional[List[Any]] = None,
    out_dir: Optional[str] = None,
    max_chapters: int = 4,
) -> Dict[str, Any]:
    state = state or {}
    images = images or []
    result: Dict[str, Any] = {
        "ok": False,
        "status": "init",
        "chapter_paths": [],
        "hero_path": None,
        "out_dir": None,
        "portraits_used": 0,
        "voices_count": len(voices or []),
        "note": "",
        "error": None,
    }

    if not HAS_MEDIA or Image is None:
        result["status"] = "unavailable"
        result["note"] = "moviepy/Pillow unavailable — pip install -r requirements.txt"
        return result

    try:
        out_dir = out_dir or tempfile.mkdtemp(prefix="silverscreen_")
        os.makedirs(out_dir, exist_ok=True)
        result["out_dir"] = out_dir

        bg = _genre_color(state.get("genre") or "drama")
        title = state.get("title") or "Silver-Screen"
        premise = (state.get("premise") or "")[:100]
        scenes = state.get("scenes") or []
        chapters = state.get("chapters") or [
            {"number": i + 1, "title": f"Chapter {i + 1}"} for i in range(2)
        ]

        portraits = []
        for up in images:
            im = _load_upload(up)
            if im is not None:
                try:
                    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
                    portraits.append(im.resize((1280, 720), resample))
                except Exception:
                    portraits.append(im)
        result["portraits_used"] = len(portraits)

        chapter_paths: List[str] = []
        chapter_clips = []

        for ci, ch in enumerate(chapters[:max_chapters]):
            ch_title = ch.get("title", f"Chapter {ci + 1}") if isinstance(ch, dict) else str(ch)
            lines = f"{title}\n{ch_title}\n{premise}"
            card = _make_card(lines, bg=bg)
            if portraits:
                try:
                    p = portraits[ci % len(portraits)].copy().resize((640, 360))
                    card.paste(p, (320, 360))
                except Exception:
                    pass

            png_path = os.path.join(out_dir, f"chapter_{ci + 1}.png")
            card.save(png_path)

            video_path = os.path.join(out_dir, f"chapter_{ci + 1}.mp4")
            wrote_video = False
            if ImageClip is not None and np is not None:
                try:
                    clip = _clip_with_duration(ImageClip(np.array(card)), 2.5)
                    _write_video(clip, video_path, fps=10)
                    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                        chapter_paths.append(video_path)
                        chapter_clips.append(clip)
                        wrote_video = True
                except Exception as e:
                    result["error"] = str(e)

            if not wrote_video:
                chapter_paths.append(png_path)

        result["chapter_paths"] = chapter_paths

        if chapter_clips and concatenate_videoclips is not None:
            try:
                hero = concatenate_videoclips(chapter_clips[: min(3, len(chapter_clips))], method="compose")
                hero_path = os.path.join(out_dir, "hero_reel.mp4")
                _write_video(hero, hero_path, fps=10)
                if os.path.exists(hero_path) and os.path.getsize(hero_path) > 0:
                    result["hero_path"] = hero_path
            except Exception as e:
                result["error"] = (result.get("error") or "") + " | hero: " + str(e)

        result["ok"] = bool(chapter_paths)
        result["status"] = "ok" if result["ok"] else "no clips"
        has_vid = any(str(p).endswith((".mp4", ".webm")) for p in chapter_paths) or bool(result.get("hero_path"))
        result["note"] = (
            f"Generated {len(chapter_paths)} chapter(s)"
            + (f" with {result['portraits_used']} portrait(s)" if result["portraits_used"] else "")
            + (". Video ready." if has_vid else ". PNG cards (video write failed or unavailable).")
        )
    except Exception as e:
        result["error"] = str(e)
        result["status"] = "error"
        result["note"] = f"Media failed safely: {e}"

    return result


def process_uploads(images=None, voices=None, film=None, out_dir=None):
    return process_media(film or {}, images=images, voices=voices, out_dir=out_dir)
