"""Local transition planning, scoring, and FFmpeg cinematic assembly."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .runtime import atomic_write_json, utc_now

PLAN_FILE = "transition_plan.json"
RUNTIME_FILE = "transition_runtime.json"
SAFE_STYLES = {"fade", "fadeblack"}


class TransitionError(RuntimeError):
    """Raised when local transition work cannot complete."""


@dataclass(frozen=True)
class TransitionSettings:
    enabled: bool = True
    mode: str = "auto"
    same_scene: float = 0.18
    scene_change: float = 0.32
    chapter_change: float = 0.50
    fps: int = 24
    max_width: int = 1280
    crf: int = 18
    analyze_frames: bool = True


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.lower() not in {"0", "false", "no", "off"}


def _float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def settings(mode: str | None = None, *, analyze_frames: bool | None = None) -> TransitionSettings:
    selected = str(mode or os.getenv("SILVER_SCREEN_TRANSITION_MODE", "auto")).lower()
    if selected not in {"auto", "subtle", "strong", "off"}:
        selected = "auto"
    cfg = TransitionSettings(
        enabled=_bool_env("SILVER_SCREEN_CINEMATIC_TRANSITIONS", True) and selected != "off",
        mode=selected,
        same_scene=_float_env("SILVER_SCREEN_TRANSITION_SAME_SCENE_SECONDS", 0.18, 0.05, 0.8),
        scene_change=_float_env("SILVER_SCREEN_TRANSITION_SCENE_SECONDS", 0.32, 0.08, 1.0),
        chapter_change=_float_env("SILVER_SCREEN_TRANSITION_CHAPTER_SECONDS", 0.50, 0.12, 1.2),
        fps=max(12, min(60, int(os.getenv("SILVER_SCREEN_TRANSITION_FPS", "24") or 24))),
        max_width=max(640, min(3840, int(os.getenv("SILVER_SCREEN_TRANSITION_MAX_WIDTH", "1280") or 1280))),
        crf=max(14, min(30, int(os.getenv("SILVER_SCREEN_TRANSITION_CRF", "18") or 18))),
        analyze_frames=_bool_env("SILVER_SCREEN_TRANSITION_ANALYZE_FRAMES", True)
        if analyze_frames is None
        else bool(analyze_frames),
    )
    if selected == "subtle":
        return replace(
            cfg,
            same_scene=max(0.05, cfg.same_scene * 0.7),
            scene_change=max(0.08, cfg.scene_change * 0.7),
            chapter_change=max(0.12, cfg.chapter_change * 0.75),
        )
    if selected == "strong":
        return replace(
            cfg,
            same_scene=min(0.8, cfg.same_scene * 1.45),
            scene_change=min(1.0, cfg.scene_change * 1.4),
            chapter_change=min(1.2, cfg.chapter_change * 1.3),
        )
    return cfg


def paths(root: str | os.PathLike[str]) -> dict[str, Path]:
    base = Path(root).resolve()
    return {
        "plan": base / PLAN_FILE,
        "runtime": base / RUNTIME_FILE,
        "frames": base / "transition_frames",
        "chapters": base / "chapters",
    }


def _scene(shot: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int((shot.get("sourceScene") or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return default


def duration(shot: dict[str, Any]) -> float:
    try:
        return max(
            0.2,
            float(
                shot.get("verifiedDurationSeconds")
                or shot.get("plannedDurationSeconds")
                or 8
            ),
        )
    except (TypeError, ValueError):
        return 8.0


def verified_shots(queue: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            shot
            for shot in queue.get("shots") or []
            if isinstance(shot, dict) and shot.get("status") == "verified"
        ],
        key=lambda shot: int(shot.get("order", 0) or 0),
    )


def shot_path(root: Path, shot: dict[str, Any]) -> Path | None:
    value = str(shot.get("path") or "")
    if not value:
        return None
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise TransitionError("Video artifact escaped the production workspace")
    return resolved


def relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise TransitionError("Transition artifact escaped the production workspace")
    return resolved.relative_to(root).as_posix()


def relation(previous: dict[str, Any], current: dict[str, Any]) -> str:
    if _scene(previous, "number") == _scene(current, "number"):
        return "continuation"
    if _scene(previous, "chapter", 1) == _scene(current, "chapter", 1):
        return "scene_change"
    return "chapter_change"


def planned_transition(
    previous: dict[str, Any],
    current: dict[str, Any],
    cfg: TransitionSettings | None = None,
) -> dict[str, Any]:
    cfg = cfg or settings()
    kind = relation(previous, current)
    if kind == "continuation":
        style, seconds = "fade", cfg.same_scene
        prompt = (
            "Continue the exact pose, action, screen position, camera axis, lens feel, "
            "lighting direction, and camera velocity from the supplied final frame. "
            "The opening second must look like the next moment of the same take."
        )
    elif kind == "scene_change":
        style, seconds = "fade", cfg.scene_change
        prompt = (
            "Open with a professional match-on-action. Preserve screen direction, "
            "subject scale, color temperature, and visual momentum before revealing "
            "the new scene; avoid an abrupt pose or camera reset."
        )
    else:
        style, seconds = "fadeblack", cfg.chapter_change
        prompt = (
            "Open as a deliberate chapter transition. Preserve screen direction and "
            "color logic, then reveal the new setting cleanly without an accidental jump cut."
        )
    return {
        "fromShot": str(previous.get("id") or ""),
        "toShot": str(current.get("id") or ""),
        "fromOrder": int(previous.get("order", 0) or 0),
        "toOrder": int(current.get("order", 0) or 0),
        "relation": kind,
        "style": style,
        "durationSeconds": round(seconds if cfg.enabled else 0.0, 3),
        "promptDirective": prompt,
        "continuityFrameUsed": bool(current.get("continuityUsed")),
        "fromScene": _scene(previous, "number"),
        "toScene": _scene(current, "number"),
        "fromChapter": _scene(previous, "chapter", 1),
        "toChapter": _scene(current, "chapter", 1),
        "transitionId": (
            f"transition_{int(previous.get('order', 0) or 0):04d}_"
            f"{int(current.get('order', 0) or 0):04d}"
        ),
    }


def prompt_directive(shot: dict[str, Any] | None) -> str:
    if not isinstance(shot, dict):
        return ""
    if shot.get("continuityUsed") and int(shot.get("segment", 1) or 1) > 1:
        return (
            "CINEMATIC CONTINUITY: the supplied image is the literal final frame of "
            "the preceding clip. Preserve the same actor pose, motion, eye line, "
            "screen position, wardrobe, lighting, and camera velocity; do not reset the take."
        )
    if shot.get("continuityUsed"):
        return (
            "CINEMATIC TRANSITION: use the supplied final frame as a visual match. "
            "Preserve screen direction, subject scale, lighting, and momentum for the opening beat."
        )
    return (
        "CINEMATIC OPENING: begin with a stable composition and one clear camera "
        "movement that the following clip can continue."
    )


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ffprobe() -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = _ffmpeg()
    sibling = Path(ffmpeg).with_name("ffprobe") if ffmpeg else None
    return str(sibling) if sibling and sibling.exists() else None


def probe(path: Path) -> dict[str, Any]:
    result = {"duration": 0.0, "width": None, "height": None, "audio": False}
    ffprobe = _ffprobe()
    if not ffprobe or not path.exists():
        return result
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if completed.returncode != 0:
        return result
    try:
        payload = json.loads(completed.stdout)
        streams = payload.get("streams") or []
        video = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            {},
        )
        result.update(
            {
                "duration": float((payload.get("format") or {}).get("duration") or 0),
                "width": video.get("width"),
                "height": video.get("height"),
                "audio": any(stream.get("codec_type") == "audio" for stream in streams),
            }
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return result


def _frame(clip: Path, destination: Path, *, first: bool) -> Path | None:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y"]
    command += ["-ss", "0.05", "-i", str(clip)] if first else [
        "-sseof",
        "-0.12",
        "-i",
        str(clip),
    ]
    command += ["-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "3", str(destination)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if completed.returncode != 0 or not destination.exists():
        destination.unlink(missing_ok=True)
        return None
    return destination


def _similarity(first: Path, second: Path) -> dict[str, float] | None:
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

        with Image.open(first) as a, Image.open(second) as b:
            a = ImageOps.fit(a.convert("RGB"), (192, 108))
            b = ImageOps.fit(b.convert("RGB"), (192, 108))
            rms = ImageStat.Stat(ImageChops.difference(a, b)).rms
            visual = 1 - sum(rms) / (len(rms) * 255)
            luma = 1 - abs(
                ImageStat.Stat(a.convert("L")).mean[0]
                - ImageStat.Stat(b.convert("L")).mean[0]
            ) / 255
            edge = 1 - ImageStat.Stat(
                ImageChops.difference(
                    a.convert("L").filter(ImageFilter.FIND_EDGES),
                    b.convert("L").filter(ImageFilter.FIND_EDGES),
                )
            ).rms[0] / 255
        return {
            "visual": max(0.0, min(1.0, visual)),
            "luminance": max(0.0, min(1.0, luma)),
            "edge": max(0.0, min(1.0, edge)),
        }
    except Exception:
        return None


def analyze(
    previous: dict[str, Any],
    current: dict[str, Any],
    root: Path,
    cfg: TransitionSettings,
) -> dict[str, Any]:
    item = planned_transition(previous, current, cfg)
    score_data = None
    a, b = shot_path(root, previous), shot_path(root, current)
    if cfg.analyze_frames and a and b and a.exists() and b.exists():
        frame_root = paths(root)["frames"]
        key = f"{item['fromShot']}__{item['toShot']}"
        out_frame = _frame(a, frame_root / f"{key}-out.jpg", first=False)
        in_frame = _frame(b, frame_root / f"{key}-in.jpg", first=True)
        if out_frame and in_frame:
            score_data = _similarity(out_frame, in_frame)
    continuity = 1.0 if current.get("continuityUsed") else 0.45
    kind = item["relation"]
    if score_data:
        v, l, e = score_data["visual"], score_data["luminance"], score_data["edge"]
        raw = (
            v * 0.55 + l * 0.15 + e * 0.15 + continuity * 0.15
            if kind == "continuation"
            else v * 0.38 + l * 0.17 + e * 0.10 + continuity * 0.10 + 0.25
            if kind == "scene_change"
            else v * 0.25 + l * 0.15 + e * 0.10 + 0.50
        )
    else:
        raw = 0.64 + continuity * 0.18 if kind == "continuation" else 0.68 if kind == "scene_change" else 0.72
    repair = "none"
    if kind == "continuation" and raw < 0.58:
        item.update(style="fade", durationSeconds=max(item["durationSeconds"], 0.34))
        repair = "extended_dissolve"
    elif kind == "scene_change" and raw < 0.50:
        item.update(style="fadeblack", durationSeconds=max(item["durationSeconds"], 0.46))
        repair = "dip_to_black"
    elif kind == "scene_change" and raw < 0.64:
        item.update(style="fade", durationSeconds=max(item["durationSeconds"], 0.38))
        repair = "longer_dissolve"
    bonus = {"fade": 0.035, "fadeblack": 0.12}.get(item["style"], 0)
    effective = min(1.0, raw + bonus + min(0.12, item["durationSeconds"] * 0.18))
    item.update(
        analyzed=bool(score_data),
        rawMatchScore=round(raw, 6),
        effectiveScore=round(effective, 6),
        rating="seamless" if effective >= 0.82 else "smooth" if effective >= 0.64 else "masked" if effective >= 0.48 else "attention",
        tgrmRepair=repair,
        analysis=score_data or {},
        updatedAt=utc_now(),
    )
    return item


def save_plan(queue: dict[str, Any], root: Path) -> None:
    plan = queue.get("transitionPlan") or {}
    target = paths(root)
    atomic_write_json(target["plan"], plan)
    atomic_write_json(
        target["runtime"],
        {
            "status": plan.get("status"),
            "updatedAt": plan.get("updatedAt"),
            "metrics": plan.get("metrics") or {},
            "assembly": plan.get("assembly") or {},
            "artifacts": plan.get("artifacts") or {},
        },
    )


def load_plan(root: str | os.PathLike[str]) -> dict[str, Any] | None:
    path = paths(root)["plan"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def build_plan(
    queue: dict[str, Any],
    root: str | os.PathLike[str],
    cfg: TransitionSettings | None = None,
) -> dict[str, Any]:
    cfg = cfg or settings()
    base = Path(root).resolve()
    shots = verified_shots(queue)
    transitions = []
    source_runtime = sum(duration(shot) for shot in shots)
    cursor = 0.0
    if shots:
        shots[0]["timelineStartSeconds"] = 0.0
        shots[0]["timelineEndSeconds"] = round(duration(shots[0]), 3)
        cursor = shots[0]["timelineEndSeconds"]
        for previous, current in zip(shots, shots[1:]):
            item = analyze(previous, current, base, cfg)
            current["transitionIn"] = item
            transitions.append(item)
            start = max(0.0, cursor - (item["durationSeconds"] if cfg.enabled else 0))
            current["timelineStartSeconds"] = round(start, 3)
            current["timelineEndSeconds"] = round(start + duration(current), 3)
            cursor = current["timelineEndSeconds"]
    scores = [float(item["effectiveScore"]) for item in transitions]
    metrics = {
        "enabled": cfg.enabled,
        "boundaries": len(transitions),
        "averageScore": round(sum(scores) / len(scores), 6) if scores else 1.0,
        "minimumScore": round(min(scores), 6) if scores else 1.0,
        "smoothOrBetterBoundaries": sum(item["rating"] in {"seamless", "smooth"} for item in transitions),
        "attentionBoundaries": sum(item["rating"] == "attention" for item in transitions),
        "hardCutsAvoided": len(transitions) if cfg.enabled else 0,
        "transitionSeconds": round(sum(item["durationSeconds"] for item in transitions), 3),
        "sourceRuntimeSeconds": round(source_runtime, 3),
        "assembledRuntimeSeconds": round(cursor, 3),
    }
    previous_plan = queue.get("transitionPlan") or {}
    plan = {
        "status": "attention" if metrics["attentionBoundaries"] else "ready" if transitions else "single_clip",
        "settings": asdict(cfg),
        "transitions": transitions,
        "metrics": metrics,
        "assembly": dict(previous_plan.get("assembly") or {}),
        "artifacts": dict(previous_plan.get("artifacts") or {}),
        "updatedAt": utc_now(),
    }
    queue["transitionPlan"] = plan
    queue["transitionMetrics"] = metrics
    save_plan(queue, base)
    return plan


def rows(plan_or_queue: dict[str, Any]) -> list[dict[str, Any]]:
    plan = plan_or_queue.get("transitionPlan", plan_or_queue)
    return [
        {
            "From": item.get("fromOrder"),
            "To": item.get("toOrder"),
            "Relationship": str(item.get("relation", "")).replace("_", " ").title(),
            "Edit": (
                "Cross Dissolve"
                if str(item.get("style", "")).lower() == "fade"
                else "Dip to Black"
                if str(item.get("style", "")).lower() == "fadeblack"
                else str(item.get("style", "")).replace("_", " ").title()
            ),
            "Blend": float(item.get("durationSeconds", 0) or 0),
            "Score": round(float(item.get("effectiveScore", 0) or 0) * 100, 1),
            "State": str(item.get("rating", "")).title(),
            "TGRM repair": str(item.get("tgrmRepair", "none")).replace("_", " ").title(),
        }
        for item in plan.get("transitions") or []
        if isinstance(item, dict)
    ]


def build_filter(
    durations: list[float],
    transitions: list[dict[str, Any]],
) -> tuple[str, str, str, float, list[dict[str, Any]]]:
    if len(durations) < 2 or len(transitions) != len(durations) - 1:
        raise TransitionError("Transition count must equal clip count minus one")
    graph, video, audio, total, effective = [], "0:v", "0:a", float(durations[0]), []
    for index, item in enumerate(transitions, start=1):
        next_duration = float(durations[index])
        seconds = min(
            max(0.05, float(item.get("durationSeconds", 0.05) or 0.05)),
            1.2,
            max(0.05, total - 0.05),
            max(0.05, next_duration - 0.05),
        )
        style = str(item.get("style", "fade")).lower()
        style = style if style in SAFE_STYLES else "fade"
        offset = max(0.001, total - seconds)
        vout, aout = f"v{index}", f"a{index}"
        graph.append(f"[{video}][{index}:v]xfade=transition={style}:duration={seconds:.3f}:offset={offset:.3f}[{vout}]")
        graph.append(f"[{audio}][{index}:a]acrossfade=d={seconds:.3f}:c1=tri:c2=tri[{aout}]")
        effective.append({**item, "style": style, "durationSeconds": round(seconds, 3), "offsetSeconds": round(offset, 3)})
        video, audio, total = vout, aout, total + next_duration - seconds
    return ";".join(graph), video, audio, round(total, 3), effective


def _dimensions(meta: list[dict[str, Any]], cfg: TransitionSettings) -> tuple[int, int]:
    width = next((int(item["width"]) for item in meta if item.get("width")), 1280)
    height = next((int(item["height"]) for item in meta if item.get("height")), 720)
    if width > cfg.max_width:
        height = round(height * cfg.max_width / width)
        width = cfg.max_width
    return max(2, width - width % 2), max(2, height - height % 2)


def _normalize(
    source: Path,
    destination: Path,
    *,
    seconds: float,
    width: int,
    height: int,
    cfg: TransitionSettings,
    has_audio: bool,
) -> None:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise TransitionError("FFmpeg is required for cinematic assembly")
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={cfg.fps},format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS"
    )
    command = [ffmpeg, "-y", "-i", str(source)]
    if has_audio:
        filters = (
            f"[0:v]{vf}[v];[0:a]aresample=48000,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"apad,atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS[a]"
        )
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        filters = f"[0:v]{vf}[v];[1:a]atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS[a]"
    command += [
        "-filter_complex",
        filters,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{seconds:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(max(16, cfg.crf)),
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
    completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if completed.returncode != 0 or not destination.exists():
        raise TransitionError("FFmpeg normalization failed: " + completed.stderr[-1200:])


def _render(
    clips: list[Path],
    destination: Path,
    durations: list[float],
    transitions: list[dict[str, Any]],
    cfg: TransitionSettings,
    *,
    fade_only: bool,
) -> dict[str, Any]:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise TransitionError("FFmpeg is required for cinematic transition assembly")
    selected = [{**item, "style": "fade" if fade_only else item.get("style", "fade")} for item in transitions]
    graph, video, audio, total, effective = build_filter(durations, selected)
    command = [ffmpeg, "-y"]
    for clip in clips:
        command += ["-i", str(clip)]
    command += [
        "-filter_complex",
        graph,
        "-map",
        f"[{video}]",
        "-map",
        f"[{audio}]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(cfg.crf),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-t",
        f"{total:.3f}",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    info = probe(destination)
    if completed.returncode != 0 or not destination.exists() or info["duration"] <= 0.05:
        destination.unlink(missing_ok=True)
        raise TransitionError("FFmpeg transition assembly failed: " + completed.stderr[-1400:])
    return {
        "path": str(destination),
        "durationSeconds": round(info["duration"], 3),
        "effectiveTransitions": effective,
        "fallbackToFade": fade_only,
    }


def assemble(
    clips: list[Path],
    destination: Path,
    transitions: list[dict[str, Any]],
    cfg: TransitionSettings | None = None,
) -> dict[str, Any]:
    cfg = cfg or settings()
    if not clips:
        raise TransitionError("No clips were available for cinematic assembly")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        shutil.copy2(clips[0], destination)
        info = probe(destination)
        return {
            "path": str(destination),
            "durationSeconds": round(float(info.get("duration") or 0.0), 3),
            "effectiveTransitions": [],
            "fallbackToFade": False,
        }
    if not cfg.enabled or len(transitions) != len(clips) - 1:
        raise TransitionError("Cinematic transition plan is disabled or incomplete")
    meta = [probe(clip) for clip in clips]
    durations = [max(0.2, item["duration"] or float(os.getenv("SILVER_SCREEN_VIDEO_DURATION", "8") or 8)) for item in meta]
    width, height = _dimensions(meta, cfg)
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.stem}-", dir=destination.parent))
    normalized = []
    try:
        for index, (clip, item, seconds) in enumerate(zip(clips, meta, durations), start=1):
            target = temp / f"{index:04d}.mp4"
            _normalize(clip, target, seconds=seconds, width=width, height=height, cfg=cfg, has_audio=bool(item["audio"]))
            normalized.append(target)
        try:
            return _render(normalized, destination, durations, transitions, cfg, fade_only=False)
        except TransitionError:
            return _render(normalized, destination, durations, transitions, cfg, fade_only=True)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
