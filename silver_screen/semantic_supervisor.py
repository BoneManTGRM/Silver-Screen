"""Evidence-based semantic shot verification with optional OpenAI vision.

Local visual metrics remain the first gate. When the operator explicitly
authorizes it and an OpenAI API key is configured, sampled generated frames are
compared with the approved shot contract and production memory. The supervisor
records confidence and evidence; it does not identify real people or infer
private traits.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

from .production_memory import (
    load_project_memory, memory_context, project_id_for, record_memory_event,
    record_scar, record_shot_outcome, save_project_memory,
)
from .runtime import RunWorkspace, load_run, utc_now
from .video_runtime import load_video_queue, save_video_queue
from .visual_quality import analyze_clip


class SemanticSupervisorError(RuntimeError):
    pass


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def semantic_settings(value: Any = None) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    mode = str(raw.get("mode") or os.getenv("SILVER_SCREEN_SEMANTIC_QA_MODE", "auto")).casefold()
    if mode not in {"auto", "openai", "local", "off"}:
        mode = "auto"
    authorized = _bool(raw.get("authorized"), os.getenv("SILVER_SCREEN_SEMANTIC_QA_AUTHORIZED", "0") not in {"0", "false", "off"})
    enabled = mode != "off" and _bool(raw.get("enabled"), os.getenv("SILVER_SCREEN_SEMANTIC_QA", "1") not in {"0", "false", "off"})
    key_available = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return {
        "schemaVersion": 1,
        "enabled": enabled,
        "mode": mode,
        "authorized": authorized,
        "openaiAvailable": key_available,
        "useOpenAI": enabled and mode in {"auto", "openai"} and authorized and key_available,
        "model": str(raw.get("model") or os.getenv("SILVER_SCREEN_SEMANTIC_MODEL", "gpt-5.6-luna")).strip(),
        "qualityTarget": _float(raw.get("qualityTarget", os.getenv("SILVER_SCREEN_SEMANTIC_TARGET", "0.84")), 0.84, 0.45, 0.98),
        "hardRejectTarget": _float(raw.get("hardRejectTarget", os.getenv("SILVER_SCREEN_SEMANTIC_HARD_REJECT", "0.58")), 0.58, 0.25, 0.90),
        "minimumConfidence": _float(raw.get("minimumConfidence", os.getenv("SILVER_SCREEN_SEMANTIC_MIN_CONFIDENCE", "0.62")), 0.62, 0.25, 0.98),
        "sampleFrames": _int(raw.get("sampleFrames", os.getenv("SILVER_SCREEN_SEMANTIC_SAMPLE_FRAMES", "4")), 4, 2, 8),
        "maxImageBytes": _int(raw.get("maxImageBytes", os.getenv("SILVER_SCREEN_SEMANTIC_MAX_IMAGE_BYTES", "350000")), 350000, 80000, 1500000),
        "detail": "high" if str(raw.get("detail") or "low").casefold() == "high" else "low",
        "automaticReject": _bool(raw.get("automaticReject"), False),
    }


def _ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def sample_semantic_frames(clip: Path, destination: Path, *, count: int = 4) -> list[Path]:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise SemanticSupervisorError("FFmpeg is required for semantic frame sampling")
    destination.mkdir(parents=True, exist_ok=True)
    for old in destination.glob("semantic_*.jpg"):
        old.unlink(missing_ok=True)
    command = [ffmpeg, "-y", "-i", str(clip), "-vf", f"fps={max(2, count)}/8,scale=640:-2", "-frames:v", str(max(2, count)), "-q:v", "3", str(destination / "semantic_%03d.jpg")]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    frames = sorted(destination.glob("semantic_*.jpg"))
    if completed.returncode != 0 or len(frames) < 2:
        raise SemanticSupervisorError(f"Could not sample semantic frames: {completed.stderr[-900:]}")
    return frames


def _image_data_url(path: Path, max_bytes: int) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.width > 768:
            image.thumbnail((768, 768))
        selected = b""
        for quality in (82, 72, 62, 52, 42):
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=quality, optimize=True)
            selected = buffer.getvalue()
            if len(selected) <= max_bytes:
                break
    return "data:image/jpeg;base64," + base64.b64encode(selected).decode("ascii")


def _scene_for(state: dict[str, Any], number: int) -> dict[str, Any]:
    for scene in state.get("scenes") or []:
        if isinstance(scene, dict) and int(scene.get("number", 0) or 0) == number:
            return scene
    return {}


def build_shot_contract(state: dict[str, Any], shot: dict[str, Any], memory: dict[str, Any] | None = None) -> dict[str, Any]:
    source = shot.get("sourceScene") or {}
    number = int(source.get("number", 0) or 0)
    scene = _scene_for(state, number)
    characters = []
    character_map = {str(item.get("id")): item for item in state.get("characters") or [] if isinstance(item, dict)}
    for value in scene.get("characters") or []:
        item = character_map.get(str(value)) or {}
        characters.append({"name": item.get("name"), "role": item.get("role"), "description": item.get("description")})
    blueprint = shot.get("blueprint") or shot.get("shotBlueprint") or {}
    contract = {
        "shotId": str(shot.get("id") or ""),
        "order": int(shot.get("order", 0) or 0),
        "scene": number,
        "chapter": source.get("chapter"),
        "setting": scene.get("slugline"),
        "storyBeat": scene.get("summary") or scene.get("action"),
        "conflict": scene.get("conflict"),
        "turn": scene.get("turn"),
        "characters": characters,
        "shotType": blueprint.get("type"),
        "shotAction": blueprint.get("description") or shot.get("prompt"),
        "dialogue": blueprint.get("dialogue"),
        "continuityUsed": bool(shot.get("continuityUsed")),
        "approvedPrompt": shot.get("prompt"),
        "memoryContext": memory_context(memory or {}, scene_number=number, shot=shot, max_chars=1800),
    }
    contract["contractHash"] = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    return contract


def _extract_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    parts = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts)


def _json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.casefold().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(cleaned[start:end + 1])
        if isinstance(value, dict):
            return value
    raise SemanticSupervisorError("Semantic reviewer did not return a JSON object")


class OpenAIVisionSupervisor:
    def __init__(self, api_key: str | None = None, *, model: str | None = None, timeout_seconds: int = 180) -> None:
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
        self.model = (model or os.getenv("SILVER_SCREEN_SEMANTIC_MODEL") or "gpt-5.6-luna").strip()
        self.timeout_seconds = max(30, int(timeout_seconds))
        if not self.api_key:
            raise SemanticSupervisorError("OPENAI_API_KEY is not configured")

    def evaluate(self, contract: dict[str, Any], frames: list[Path], *, detail: str = "low", max_image_bytes: int = 350000) -> dict[str, Any]:
        instructions = (
            "You are Silver-Screen's evidence-based shot supervisor. Compare the sampled frames with the approved contract. "
            "Do not identify real people or infer private traits. Judge only visible production consistency. Return JSON with: "
            "scores (storyBeat, action, cast, identityAndWardrobe, propsAndWorld, framingAndCamera, performance, continuity, inventedContradictions; each 0..1 where higher is better), "
            "confidence 0..1, criticalFailures array, findings array of {code,severity,message,repair}, repairDirective string, and summary string."
        )
        content: list[dict[str, Any]] = [{"type": "input_text", "text": instructions + "\nCONTRACT:\n" + json.dumps(contract, ensure_ascii=False, default=str)}]
        for frame in frames[:8]:
            content.append({"type": "input_image", "image_url": _image_data_url(frame, max_image_bytes), "detail": detail})
        payload = {"model": self.model, "input": [{"role": "user", "content": content}], "max_output_tokens": 1800}
        body = json.dumps(payload).encode("utf-8")
        last: Exception | None = None
        for attempt in range(3):
            request = urllib.request.Request("https://api.openai.com/v1/responses", data=body, method="POST", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
                parsed = _json_object(_extract_text(result))
                parsed["provider"] = "openai"
                parsed["model"] = self.model
                parsed["usage"] = result.get("usage") or {}
                return parsed
            except urllib.error.HTTPError as exc:
                detail_text = exc.read().decode("utf-8", errors="replace")
                last = SemanticSupervisorError(f"OpenAI returned HTTP {exc.code}: {detail_text[:900]}")
                if exc.code != 429 or attempt >= 2:
                    raise last
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                last = exc
                break
        raise SemanticSupervisorError(f"Semantic review failed: {last}")


def _score(value: Any, default: float = 0.5) -> float:
    return _float(value, default, 0.0, 1.0)


def _normalize_payload(value: dict[str, Any]) -> dict[str, Any]:
    names = ("storyBeat", "action", "cast", "identityAndWardrobe", "propsAndWorld", "framingAndCamera", "performance", "continuity", "inventedContradictions")
    scores = {name: _score((value.get("scores") or {}).get(name)) for name in names}
    findings = []
    for item in value.get("findings") or []:
        if isinstance(item, dict):
            findings.append({"code": str(item.get("code") or "semantic_finding")[:100], "severity": str(item.get("severity") or "medium")[:20], "message": str(item.get("message") or "")[:900], "repair": str(item.get("repair") or "")[:1200]})
    return {
        "scores": scores,
        "confidence": _score(value.get("confidence"), 0.0),
        "criticalFailures": [str(item)[:600] for item in (value.get("criticalFailures") or [])[:20]],
        "findings": findings[:40],
        "repairDirective": str(value.get("repairDirective") or "")[:2400],
        "summary": str(value.get("summary") or "")[:1200],
        "provider": value.get("provider"), "model": value.get("model"), "usage": value.get("usage") or {},
    }


def _semantic_score(scores: dict[str, Any]) -> float:
    weights = {"storyBeat": .15, "action": .15, "cast": .10, "identityAndWardrobe": .13, "propsAndWorld": .10, "framingAndCamera": .10, "performance": .10, "continuity": .12, "inventedContradictions": .05}
    return sum(_score(scores.get(name)) * weight for name, weight in weights.items())


def evaluate_clip(clip: str | os.PathLike[str], *, state: dict[str, Any], shot: dict[str, Any], memory: dict[str, Any] | None = None, work_dir: str | os.PathLike[str] | None = None, visual_report: dict[str, Any] | None = None, config: dict[str, Any] | None = None, client: OpenAIVisionSupervisor | None = None) -> dict[str, Any]:
    cfg = semantic_settings(config)
    path = Path(clip).resolve()
    visual = visual_report if isinstance(visual_report, dict) else None
    if visual is None:
        try:
            visual = analyze_clip(path, work_dir=(Path(work_dir) / "visual" if work_dir else None))
        except Exception as exc:
            visual = {"available": False, "score": 0.5, "accepted": True, "hardFailure": False, "findings": [], "error": str(exc)}
    contract = build_shot_contract(state, shot, memory)
    semantic_available = False
    semantic_error = None
    frame_hashes: list[str] = []
    if cfg["useOpenAI"]:
        try:
            frames = sample_semantic_frames(path, Path(work_dir or path.parent / ".semantic") / "frames", count=int(cfg["sampleFrames"]))
            raw = (client or OpenAIVisionSupervisor(model=cfg["model"])).evaluate(contract, frames, detail=cfg["detail"], max_image_bytes=int(cfg["maxImageBytes"]))
            semantic = _normalize_payload(raw)
            semantic_available = True
            frame_hashes = [hashlib.sha256(frame.read_bytes()).hexdigest() for frame in frames]
        except Exception as exc:
            semantic_error = str(exc)
            semantic = _normalize_payload({"summary": "Semantic provider review was unavailable."})
    else:
        semantic = _normalize_payload({"summary": "Local technical QA only; semantic review was not an acceptance gate."})
    semantic_value = _semantic_score(semantic["scores"])
    visual_value = _score(visual.get("score"), 0.5)
    composite = semantic_value * .74 + visual_value * .26 if semantic_available else visual_value
    confidence = float(semantic.get("confidence", 0) or 0)
    semantic_hard = semantic_available and confidence >= cfg["minimumConfidence"] and (bool(semantic["criticalFailures"]) or semantic_value < cfg["hardRejectTarget"] or any(item.get("severity") == "critical" for item in semantic["findings"]))
    hard_failure = bool(visual.get("hardFailure")) or semantic_hard
    accepted = not hard_failure and (composite >= cfg["qualityTarget"] if semantic_available else bool(visual.get("accepted", True)))
    repair = " ".join(part for part in (str(visual.get("repairDirective") or ""), semantic["repairDirective"]) if part.strip())[:3000]
    return {
        "schemaVersion": 1, "analyzedAt": utc_now(), "clip": str(path), "shotId": str(shot.get("id") or ""),
        "contractHash": contract["contractHash"], "contract": contract,
        "semanticAvailable": semantic_available, "semanticError": semantic_error,
        "semanticProvider": semantic.get("provider"), "semanticModel": semantic.get("model"),
        "semanticScore": round(semantic_value, 6), "visualScore": round(visual_value, 6), "compositeScore": round(composite, 6), "scorePercent": round(composite * 100, 1),
        "confidence": round(confidence, 6), "qualityTarget": cfg["qualityTarget"],
        "rating": "accepted" if accepted else "reject" if hard_failure else "review",
        "accepted": accepted, "hardFailure": hard_failure,
        "criticalFailures": semantic["criticalFailures"], "scores": semantic["scores"],
        "findings": [*(visual.get("findings") or []), *semantic["findings"]][:60],
        "repairDirective": repair, "summary": semantic["summary"], "visualReport": visual,
        "usage": semantic.get("usage") or {}, "evidenceFrameHashes": frame_hashes,
        "decisionBasis": "semantic_and_visual" if semantic_available else "visual_only_semantic_unavailable",
    }


def inspect_run_semantics(run_id: str, *, project_id: str | None = None, output_root: str = "runs", config: dict[str, Any] | None = None, client: OpenAIVisionSupervisor | None = None) -> dict[str, Any]:
    workspace = RunWorkspace.open_existing(output_root, run_id)
    result = load_run(run_id, output_root)
    queue = load_video_queue(workspace.media_dir)
    if queue is None:
        raise SemanticSupervisorError("The selected production has no durable video queue")
    brief = result.get("brief") or workspace.manifest.get("brief") or {}
    selected = project_id_for(brief, explicit=project_id)
    memory = load_project_memory(selected, output_root)
    state = result.get("state") or {}
    reports = []
    for shot in queue.get("shots") or []:
        if not isinstance(shot, dict) or shot.get("status") != "verified" or not shot.get("path"):
            continue
        clip = Path(str(shot["path"]))
        if not clip.is_absolute():
            clip = (workspace.media_dir / clip).resolve()
        if not clip.exists():
            continue
        report = evaluate_clip(clip, state=state, shot=shot, memory=memory, work_dir=workspace.media_dir / "semantic_quality" / str(shot.get("id") or "shot"), visual_report=shot.get("visualQuality"), config=config, client=client)
        shot["semanticQuality"] = report
        reports.append(report)
        record_shot_outcome(memory, shot=shot, report=report, route=shot.get("modelRoute") or {}, accepted=report["accepted"], repair=report["repairDirective"])
        if report["hardFailure"]:
            record_scar(memory, domain="semantic_shot", failure="; ".join(report["criticalFailures"]) or report["summary"], repair=report["repairDirective"], shot_id=str(shot.get("id") or ""))
    reports.sort(key=lambda item: int((item.get("contract") or {}).get("order", 0) or 0))
    scores = [float(item.get("compositeScore", 0) or 0) for item in reports]
    summary = {
        "schemaVersion": 1, "runId": run_id, "projectId": selected, "analyzedAt": utc_now(), "clips": len(reports),
        "accepted": sum(bool(item.get("accepted")) for item in reports),
        "review": sum(item.get("rating") == "review" for item in reports),
        "rejected": sum(item.get("rating") == "reject" for item in reports),
        "semanticReviewed": sum(bool(item.get("semanticAvailable")) for item in reports),
        "averageScore": round(sum(scores) / len(scores), 6) if scores else 0.0,
        "minimumScore": round(min(scores), 6) if scores else 0.0, "reports": reports,
    }
    report_path = workspace.write_json("media/semantic_quality_report.json", summary)
    workspace.register_artifact("semanticQualityReport", report_path)
    queue["semanticQualityReport"] = summary
    save_video_queue(workspace.media_dir, queue)
    record_memory_event(memory, "semantic_run_inspection", detail=f"Inspected {len(reports)} verified clips", data={"accepted": summary["accepted"], "rejected": summary["rejected"], "averageScore": summary["averageScore"]})
    save_project_memory(memory, output_root)
    return {"result": result, "queue": queue, "report": summary, "memory": memory, "reportPath": str(report_path)}


def select_retake_candidate(report: dict[str, Any], *, target: float | None = None) -> dict[str, Any] | None:
    threshold = float(target if target is not None else semantic_settings()["qualityTarget"])
    candidates = [item for item in report.get("reports") or [] if isinstance(item, dict) and (item.get("hardFailure") or float(item.get("compositeScore", 0) or 0) < threshold)]
    candidates.sort(key=lambda item: (0 if item.get("hardFailure") else 1, float(item.get("compositeScore", 0) or 0), int((item.get("contract") or {}).get("order", 0) or 0)))
    return candidates[0] if candidates else None


def compare_candidate_reports(previous: dict[str, Any], current: dict[str, Any], *, minimum_gain: float = .015) -> dict[str, Any]:
    before, after = float(previous.get("compositeScore", 0) or 0), float(current.get("compositeScore", 0) or 0)
    gain = after - before
    return {"previousScore": before, "currentScore": after, "gain": round(gain, 6), "selected": "current" if gain >= minimum_gain else "previous", "minimumGain": minimum_gain}


__all__ = ["OpenAIVisionSupervisor", "SemanticSupervisorError", "build_shot_contract", "compare_candidate_reports", "evaluate_clip", "inspect_run_semantics", "sample_semantic_frames", "select_retake_candidate", "semantic_settings"]
