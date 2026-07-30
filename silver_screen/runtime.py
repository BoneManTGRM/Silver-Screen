"""Durable run workspaces, manifests, and artifact bundles."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RUNS_DIR = "runs"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(value: str, fallback: str = "silver-screen") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return (cleaned[:80] or fallback).strip("-")


def create_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"ss_{stamp}_{uuid.uuid4().hex[:8]}"


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "name") and not isinstance(value, (str, bytes, bytearray)):
        return str(getattr(value, "name", type(value).__name__))
    return str(value)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent, prefix=f".{path.name}."
    ) as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
    )


class RunWorkspace:
    """A single durable pipeline run with an append-safe manifest."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        run_id: str | None = None,
        *,
        brief: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        configured_root = root or os.getenv("SILVER_SCREEN_RUNS_DIR") or DEFAULT_RUNS_DIR
        self.root = Path(configured_root).expanduser().resolve()
        self.run_id = run_id or create_run_id()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{6,120}", self.run_id):
            raise ValueError("run_id contains unsupported characters")
        self.path = (self.root / self.run_id).resolve()
        if self.root not in self.path.parents:
            raise ValueError("run workspace escaped the configured root")
        self.media_dir = self.path / "media"
        self.path.mkdir(parents=True, exist_ok=False)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.manifest: dict[str, Any] = {
            "schemaVersion": 1,
            "runId": self.run_id,
            "status": "running",
            "stage": "created",
            "progress": 0,
            "startedAt": utc_now(),
            "updatedAt": utc_now(),
            "completedAt": None,
            "brief": brief or {},
            "options": options or {},
            "warnings": [],
            "error": None,
            "artifacts": {},
        }
        self._write_manifest()

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    def _write_manifest(self) -> None:
        self.manifest["updatedAt"] = utc_now()
        atomic_write_json(self.manifest_path, self.manifest)

    def update(
        self,
        *,
        stage: str | None = None,
        progress: int | None = None,
        status: str | None = None,
        warning: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if stage is not None:
            self.manifest["stage"] = stage
        if progress is not None:
            self.manifest["progress"] = max(0, min(100, int(progress)))
        if status is not None:
            self.manifest["status"] = status
        if warning:
            warnings = self.manifest.setdefault("warnings", [])
            if warning not in warnings:
                warnings.append(warning)
        if error is not None:
            self.manifest["error"] = error
        if extra:
            self.manifest.update(extra)
        self._write_manifest()

    def write_text(self, relative_path: str, content: str) -> Path:
        path = self._safe_path(relative_path)
        atomic_write_text(path, content)
        return path

    def write_json(self, relative_path: str, payload: Any) -> Path:
        path = self._safe_path(relative_path)
        atomic_write_json(path, payload)
        return path

    def _safe_path(self, relative_path: str) -> Path:
        path = (self.path / relative_path).resolve()
        if path != self.path and self.path not in path.parents:
            raise ValueError("artifact path escaped the run workspace")
        return path

    def relative(self, path: str | os.PathLike[str]) -> str:
        resolved = Path(path).resolve()
        if resolved != self.path and self.path not in resolved.parents:
            raise ValueError("artifact is outside the run workspace")
        return resolved.relative_to(self.path).as_posix()

    def register_artifact(self, name: str, path: str | os.PathLike[str]) -> None:
        self.manifest.setdefault("artifacts", {})[name] = self.relative(path)
        self._write_manifest()

    def persist_result(self, result: dict[str, Any]) -> dict[str, str]:
        state = result.get("state") or {}
        tgrm_payload = {
            "metrics": result.get("metrics") or {},
            "msil": result.get("msil") or {},
            "log": result.get("log") or [],
            "scars": result.get("scars") or [],
            "remainingFractures": result.get("remainingFractures") or [],
        }
        outline = {
            "title": state.get("title"),
            "logline": state.get("logline"),
            "storyBible": state.get("storyBible"),
            "characters": state.get("characters") or [],
            "acts": state.get("acts") or [],
            "chapters": state.get("chapters") or [],
            "scenes": [
                {
                    "number": scene.get("number"),
                    "act": scene.get("act"),
                    "chapter": scene.get("chapter"),
                    "slugline": scene.get("slugline"),
                    "summary": scene.get("summary"),
                    "conflict": scene.get("conflict"),
                    "turn": scene.get("turn"),
                }
                for scene in state.get("scenes") or []
                if isinstance(scene, dict)
            ],
        }
        paths = {
            "brief": self.write_json("brief.json", result.get("brief") or {}),
            "film": self.write_json("film.json", state),
            "outline": self.write_json("outline.json", outline),
            "screenplay": self.write_text("screenplay.txt", str(state.get("script") or "")),
            "tgrm": self.write_json("tgrm.json", tgrm_payload),
            "result": self.write_json("result.json", result),
        }
        for name, path in paths.items():
            self.manifest.setdefault("artifacts", {})[name] = self.relative(path)
        self._write_manifest()
        return {name: str(path) for name, path in paths.items()}

    def build_bundle(self, title: str | None = None) -> Path:
        filename = f"{slugify(title or self.run_id)}-{self.run_id}.zip"
        bundle_path = self.path / filename
        self.manifest.setdefault("artifacts", {})["bundle"] = filename
        self._write_manifest()
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.path.rglob("*")):
                if not path.is_file() or path == bundle_path:
                    continue
                archive.write(path, path.relative_to(self.path).as_posix())
        return bundle_path

    def complete(self, extra: dict[str, Any] | None = None) -> None:
        payload = {
            "status": "complete",
            "stage": "complete",
            "progress": 100,
            "completedAt": utc_now(),
        }
        if extra:
            payload.update(extra)
        self.manifest.update(payload)
        self._write_manifest()

    def fail(self, error: str) -> None:
        self.manifest.update(
            {
                "status": "failed",
                "stage": "failed",
                "completedAt": utc_now(),
                "error": error,
            }
        )
        self._write_manifest()


def list_runs(
    root: str | os.PathLike[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    configured_root = Path(
        root or os.getenv("SILVER_SCREEN_RUNS_DIR") or DEFAULT_RUNS_DIR
    ).expanduser()
    if not configured_root.exists():
        return []
    records: list[dict[str, Any]] = []
    for manifest_path in configured_root.glob("*/manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["workspace"] = str(manifest_path.parent.resolve())
            records.append(payload)
        except (OSError, json.JSONDecodeError):
            continue
    records.sort(
        key=lambda item: str(item.get("startedAt") or item.get("updatedAt") or ""),
        reverse=True,
    )
    return records[: max(0, limit)]


def load_run(
    run_id: str,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    configured_root = Path(
        root or os.getenv("SILVER_SCREEN_RUNS_DIR") or DEFAULT_RUNS_DIR
    ).expanduser()
    result_path = configured_root / run_id / "result.json"
    return json.loads(result_path.read_text(encoding="utf-8"))
