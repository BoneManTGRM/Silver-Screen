"""General-purpose project starters and consent-first star identity packs.

The module does not perform facial recognition or create biometric embeddings. It
stores operator-supplied descriptions and authorized reference images inside the
selected run workspace so a production can remain reproducible and auditable.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .runtime import RunWorkspace, atomic_write_json, atomic_write_text, slugify, utc_now

STAR_MODES = {
    "self",
    "authorized_person",
    "fictional_character",
    "authorized_character",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_REFERENCE_IMAGES = 6
MAX_IMAGE_BYTES = 20 * 1024 * 1024

PROJECT_STARTERS: dict[str, dict[str, Any]] = {
    "blank_project": {
        "label": "Blank original project",
        "title": "",
        "premise": (
            "A protagonist faces a difficult choice that changes the world around "
            "them and reveals who they are under pressure."
        ),
        "genre": "drama",
        "tone": "cinematic",
        "format": "trailer",
        "mode": "fictional_character",
        "lead_name": "",
        "lead_role": "Lead protagonist",
        "lead_appearance": "",
        "wardrobe": "",
        "identity_invariants": "",
        "performance_style": "Natural cinematic performance",
        "support_name": "Supporting Character",
        "support_role": "Trusted ally and dramatic counterpoint",
        "support_description": "A visually distinct supporting character with clear motivations.",
    },
    "star_as_myself": {
        "label": "Star as myself",
        "title": "",
        "premise": (
            "A seemingly ordinary person is pulled into a high-stakes conflict and "
            "must become the hero of their own story."
        ),
        "genre": "thriller",
        "tone": "cinematic",
        "format": "trailer",
        "mode": "self",
        "lead_name": "",
        "lead_role": "Lead protagonist and central star",
        "lead_appearance": "Describe your normal appearance without changing your identity.",
        "wardrobe": "Cinematic wardrobe appropriate to the story while preserving recognizable identity.",
        "identity_invariants": "Keep face, hair, age appearance, body proportions, and distinguishing features consistent.",
        "performance_style": "Confident, natural lead performance with grounded emotion",
        "support_name": "Supporting Character",
        "support_role": "Trusted ally and dramatic counterpoint",
        "support_description": "A visually distinct supporting character who never replaces the lead.",
    },
    "authorized_person": {
        "label": "Authorized real person",
        "title": "",
        "premise": (
            "A recognizable public figure enters an unexpected adventure that tests "
            "their courage, judgment, and loyalty."
        ),
        "genre": "drama",
        "tone": "cinematic",
        "format": "trailer",
        "mode": "authorized_person",
        "lead_name": "",
        "lead_role": "Lead protagonist",
        "lead_appearance": "Describe only the appearance you are authorized to reproduce.",
        "wardrobe": "Story-appropriate wardrobe approved for this production.",
        "identity_invariants": "Preserve the authorized subject's recognizable identity throughout.",
        "performance_style": "Natural cinematic performance",
        "support_name": "Supporting Character",
        "support_role": "Dramatic counterpoint",
        "support_description": "A distinct supporting character.",
    },
    "original_character": {
        "label": "Original fictional character",
        "title": "",
        "premise": (
            "An original hero discovers a hidden threat and must choose between the "
            "safe path and the action only they can take."
        ),
        "genre": "fantasy",
        "tone": "cinematic",
        "format": "trailer",
        "mode": "fictional_character",
        "lead_name": "",
        "lead_role": "Original lead character",
        "lead_appearance": "Describe the character's face, hair, build, age appearance, and signature details.",
        "wardrobe": "Describe the character's signature wardrobe and accessories.",
        "identity_invariants": "Keep the character design, colors, proportions, and signature details unchanged.",
        "performance_style": "Expressive cinematic character performance",
        "support_name": "Supporting Character",
        "support_role": "Ally, rival, or comic counterpoint",
        "support_description": "A clearly different supporting character.",
    },
    "moonie_moo_demo": {
        "label": "Moonie Moo example",
        "title": "Moonie Moo: Queen of the Spotlight",
        "premise": (
            "A glamorous celebrity cow and her stubborn loyal bulldog discover that "
            "protecting each other matters more than protecting a perfect public image."
        ),
        "genre": "comedy",
        "tone": "cinematic",
        "format": "trailer",
        "mode": "authorized_character",
        "lead_name": "Moonie Moo",
        "lead_role": "Glamorous celebrity cow",
        "lead_appearance": (
            "A fashionable black-and-white anthropomorphic cow with expressive eyes, "
            "red lipstick, elegant clothing, gold jewelry, and celebrity confidence."
        ),
        "wardrobe": "Elegant black dress, gold jewelry, bright lipstick, and designer accessories.",
        "identity_invariants": "Keep her cow markings, face design, proportions, makeup, and signature glamour consistent.",
        "performance_style": "Expressive animated comedy with confident celebrity timing",
        "support_name": "Bully",
        "support_role": "Loyal grumpy bulldog companion",
        "support_description": (
            "A compact blue-gray bulldog with a permanently unimpressed expression, "
            "comic timing, and fierce loyalty."
        ),
    },
}


def starter_payload(key: str | None) -> dict[str, Any]:
    """Return an isolated starter payload; blank is the product default."""

    selected = str(key or "blank_project")
    if selected not in PROJECT_STARTERS:
        selected = "blank_project"
    return deepcopy(PROJECT_STARTERS[selected])


def starter_labels() -> dict[str, str]:
    return {key: str(value["label"]) for key, value in PROJECT_STARTERS.items()}


def normalize_star_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    mode = str(source.get("mode") or "fictional_character").strip().lower()
    if mode not in STAR_MODES:
        mode = "fictional_character"
    return {
        "schemaVersion": 1,
        "mode": mode,
        "subjectName": str(source.get("subjectName") or source.get("name") or "").strip()[:100],
        "role": str(source.get("role") or "Lead protagonist").strip()[:160],
        "appearance": str(source.get("appearance") or "").strip()[:1200],
        "wardrobe": str(source.get("wardrobe") or "").strip()[:800],
        "identityInvariants": str(source.get("identityInvariants") or "").strip()[:1200],
        "performanceStyle": str(source.get("performanceStyle") or "Natural cinematic performance").strip()[:500],
        "authorizationConfirmed": bool(source.get("authorizationConfirmed", False)),
        "primaryReferenceIndex": max(0, int(source.get("primaryReferenceIndex", 0) or 0)),
        "createdAt": str(source.get("createdAt") or utc_now()),
    }


def validate_star_profile(profile: dict[str, Any], reference_count: int) -> None:
    mode = str(profile.get("mode") or "fictional_character")
    name = str(profile.get("subjectName") or "").strip()
    if not name:
        raise ValueError("The lead star requires a name")
    if reference_count < 0 or reference_count > MAX_REFERENCE_IMAGES:
        raise ValueError(f"Upload between 0 and {MAX_REFERENCE_IMAGES} reference images")
    if mode in {"self", "authorized_person"} and reference_count < 1:
        raise ValueError("A real-person star production requires at least one authorized reference image")
    if reference_count and not profile.get("authorizationConfirmed"):
        raise ValueError("Confirm that you are the subject or have explicit permission to use the likeness")
    if mode == "authorized_character" and not profile.get("authorizationConfirmed"):
        raise ValueError("Confirm that you are authorized to use this character or brand design")


def identity_locked_description(profile: dict[str, Any]) -> str:
    """Build a prompt-safe identity contract from operator-supplied information."""

    name = str(profile.get("subjectName") or "the lead")
    mode = str(profile.get("mode") or "fictional_character")
    if mode in {"self", "authorized_person"}:
        lock = (
            f"IDENTITY LOCK: {name} is the exact same real person shown in the authorized "
            "primary reference image. Preserve the same recognizable face, hair, age "
            "appearance, body proportions, and distinguishing features in every shot. "
            "Do not replace the lead with a different actor or redesign their identity."
        )
    else:
        lock = (
            f"CHARACTER LOCK: {name} must retain one consistent character design in every "
            "shot. Preserve the same face design, markings, colors, proportions, and "
            "signature features; do not substitute a different character."
        )
    details = [
        lock,
        f"Role: {profile.get('role')}." if profile.get("role") else "",
        f"Appearance: {profile.get('appearance')}." if profile.get("appearance") else "",
        f"Wardrobe: {profile.get('wardrobe')}." if profile.get("wardrobe") else "",
        (
            f"Non-negotiable continuity details: {profile.get('identityInvariants')}."
            if profile.get("identityInvariants")
            else ""
        ),
        (
            f"Performance direction: {profile.get('performanceStyle')}."
            if profile.get("performanceStyle")
            else ""
        ),
    ]
    return " ".join(item for item in details if item)[:4000]


def build_star_cast(
    profile: dict[str, Any],
    *,
    support_name: str,
    support_role: str,
    support_description: str,
) -> list[dict[str, str]]:
    support = str(support_name or "Supporting Character").strip() or "Supporting Character"
    return [
        {
            "name": str(profile.get("subjectName") or "Lead Star"),
            "role": str(profile.get("role") or "Lead protagonist"),
            "description": identity_locked_description(profile),
        },
        {
            "name": support[:100],
            "role": (str(support_role or "Supporting character").strip() or "Supporting character")[:160],
            "description": str(support_description or "A visually distinct supporting character.").strip()[:1200],
        },
    ]


def reorder_primary_reference(images: list[Any], primary_index: int) -> list[Any]:
    items = list(images or [])
    if not items:
        return []
    index = max(0, min(int(primary_index or 0), len(items) - 1))
    return [items[index], *items[:index], *items[index + 1 :]]


def _upload_name(upload: Any, index: int) -> str:
    raw = str(getattr(upload, "name", "") or f"reference-{index:02d}.jpg")
    suffix = Path(raw).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".jpg"
    return f"{index:02d}-{slugify(Path(raw).stem, fallback=f'reference-{index:02d}')}{suffix}"


def _upload_bytes(upload: Any) -> bytes:
    if isinstance(upload, (str, os.PathLike)):
        path = Path(upload)
        if not path.is_file():
            raise ValueError(f"Reference image does not exist: {path}")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError(f"Reference image exceeds 20 MB: {path.name}")
        return path.read_bytes()
    if not hasattr(upload, "read"):
        raise TypeError("Unsupported reference image")
    data = upload.read(MAX_IMAGE_BYTES + 1)
    if hasattr(upload, "seek"):
        upload.seek(0)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Reference image exceeds 20 MB")
    if len(data) < 256:
        raise ValueError("Reference image is empty or too small")
    return data


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.") as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def persist_star_profile(
    run_id: str,
    profile: dict[str, Any],
    images: list[Any] | None,
    *,
    output_root: str = "runs",
) -> dict[str, Any]:
    """Persist an authorized identity pack inside an existing run workspace."""

    ordered = list(images or [])
    validate_star_profile(profile, len(ordered))
    workspace = RunWorkspace.open_existing(output_root, run_id)
    identity_dir = workspace.path / "identity"
    identity_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for index, upload in enumerate(ordered, start=1):
        data = _upload_bytes(upload)
        filename = _upload_name(upload, index)
        destination = identity_dir / filename
        _atomic_write_bytes(destination, data)
        records.append(
            {
                "order": index,
                "primary": index == 1,
                "path": workspace.relative(destination),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        workspace.register_artifact(f"identityReference{index:02d}", destination)
    payload = {
        **normalize_star_profile(profile),
        "authorizationConfirmed": bool(profile.get("authorizationConfirmed")),
        "referenceImages": records,
        "referenceCount": len(records),
        "biometricEmbeddingsCreated": False,
        "note": (
            "References are stored as production assets only. Silver-Screen does not "
            "perform facial recognition or create biometric embeddings."
        ),
        "updatedAt": utc_now(),
    }
    profile_path = identity_dir / "star_profile.json"
    atomic_write_json(profile_path, payload)
    readme_path = identity_dir / "README.txt"
    atomic_write_text(
        readme_path,
        "Authorized star identity pack\n\n"
        "The first image is the primary video anchor. Additional images are retained "
        "for continuity review and future provider support. No biometric embeddings "
        "are created by Silver-Screen.\n",
    )
    workspace.register_artifact("starProfile", profile_path)
    workspace.register_artifact("starProfileReadme", readme_path)
    workspace.update(
        extra={
            "starProfile": {
                "mode": payload["mode"],
                "subjectName": payload["subjectName"],
                "referenceCount": payload["referenceCount"],
                "authorizationConfirmed": payload["authorizationConfirmed"],
            }
        }
    )
    return {
        "profilePath": str(profile_path),
        "referencePaths": [str(workspace.path / item["path"]) for item in records],
        "profile": payload,
    }
