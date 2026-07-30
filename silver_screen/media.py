"""Media helpers — portraits / generated cards → chapter + hero reels (moviepy + Pillow).

Graceful fallback to still PNGs if video write fails.
Compatible with moviepy 1.x (moviepy.editor) and 2.x.
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
        # moviepy 1.x
        from moviepy.editor import ImageClip, concatenate_videoclips, ColorClip
    except ImportError:
        # moviepy 2.x
        from moviepy import ImageClip, concatenate_videoclips, ColorClip
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
    }.get(g, (30, 30, 50))

def _make_card(text: str, size=(1280, 720), bg=(30, 30, 50), accent=(210, 200, 180)):
    if Image is None:
        raise RuntimeError("Pillow not available")
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
    except Exception:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
        except Exception:
            font = ImageFont.load_default()
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

def process_media(
    state: Optional[Dict[str, Any]] = None,
    images: Optional[List[Any]] = None,
    voices: Optional[List[Any]] = None,
    out_dir: Optional[str] = None,
) -> Dict[str, Any]:
    state = state or {}
    result = {
        "ok": False,
        "note": "",
        "chapter_paths": [],
        "hero_path": None,
        "portraits_used": 0,
        "out_dir": None,
        "error": None,
        "images": len(images or []),
        "voices": len(voices or []),
        "status": "pending",
    }
    if not HAS_MEDIA:
        result["note"] = "moviepy/Pillow unavailable — pip install -r requirements.txt"
        result["status"] = "libs missing"
        return result

    out_dir = out_dir or tempfile.mkdtemp(prefix="silverscreen_")
    os.makedirs(out_dir, exist_ok=True)
    result["out_dir"] = out_dir

    genre = state.get("genre", "drama")
    bg = _genre_color(genre)
    title = state.get("title", "Silver-Screen")
    premise = (state.get("premise") or "")[:90]
    scenes = state.get("scenes") or []
    chapters_meta = state.get("chapters") or [{"number": 1, "title": "Chapter 1"}]

    portrait_imgs = []
    if images:
        for up in images:
            try:
                if hasattr(up, "read"):
                    data = up.read()
                    if hasattr(up, "seek"):
                        up.seek(0)
                    im = Image.open(io.BytesIO(data)).convert("RGB")
                else:
                    im = Image.open(up).convert("RGB")
                portrait_imgs.append(im.resize((1280, 720), Image.LANCZOS))
            except Exception:
                pass
    result["portraits_used"] = len(portrait_imgs)

    chapter_clips = []
    for ci, ch in enumerate(chapters_meta[:5]):
        cards = []
        ch_title = ch.get("title", f"Chapter {ci+1}") if isinstance(ch, dict) else f"Chapter {ci+1}"
        cards.append(_make_card(f"{title}\n{ch_title}\n{premise}", bg=bg))
        ch_num = ch.get("number") if isinstance(ch, dict) else (ci + 1)
        relevant = [s for s in scenes if isinstance(s, dict) and s.get("chapter") == ch_num] or scenes[ci * 2 : (ci + 1) * 2]
        for s in relevant[:2]:
            if isinstance(s, dict):
                cards.append(_make_card(str(s.get("summary", "The repair continues."))[:140], bg=bg))
        if portrait_imgs:
            cards.append(portrait_imgs[ci % len(portrait_imgs)])

        clips = []
        for card in cards:
            try:
                clips.append(ImageClip(np.array(card)).with_duration(2.8) if hasattr(ImageClip(np.array(card)), "with_duration") else ImageClip(np.array(card)).set_duration(2.8))
            except Exception:
                try:
                    clips.append(ImageClip(np.array(card)).set_duration(2.8))
                except Exception:
                    pass
        if not clips:
            try:
                clips = [ColorClip(size=(1280, 720), color=bg).set_duration(3)]
            except Exception:
                try:
                    clips = [ColorClip(size=(1280, 720), color=bg).with_duration(3)]
                except Exception:
                    clips = []

        path = os.path.join(out_dir, f"chapter_{ci+1}.webm")
        try:
            if not clips:
                raise RuntimeError("no clips")
            chapter = concatenate_videoclips(clips, method="compose")
            chapter.write_videofile(path, fps=12, codec="libvpx", audio=False, verbose=False, logger=None)
            result["chapter_paths"].append(path)
            chapter_clips.append(chapter)
        except Exception as e:
            result["error"] = str(e)
            still = os.path.join(out_dir, f"chapter_{ci+1}.png")
            try:
                cards[0].save(still)
                result["chapter_paths"].append(still)
            except Exception:
                pass

    if chapter_clips:
        try:
            hero = concatenate_videoclips(chapter_clips[:3], method="compose")
            hero_path = os.path.join(out_dir, "hero_reel.webm")
            hero.write_videofile(hero_path, fps=12, codec="libvpx", audio=False, verbose=False, logger=None)
            result["hero_path"] = hero_path
            for c in chapter_clips:
                try:
                    c.close()
                except Exception:
                    pass
            try:
                hero.close()
            except Exception:
                pass
        except Exception as e:
            result["error"] = (result.get("error") or "") + " | hero: " + str(e)

    result["ok"] = bool(result["chapter_paths"])
    result["status"] = "ok" if result["ok"] else "no clips"
    result["note"] = "Chapter + hero reels generated" if result["ok"] else "No clips produced"
    return result

def process_uploads(images=None, voices=None, film=None, out_dir=None):
    """Backward-compatible alias."""
    return process_media(film or {}, images=images, voices=voices, out_dir=out_dir)
