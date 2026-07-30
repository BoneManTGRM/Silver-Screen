from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from silver_screen.runtime import RunWorkspace
from silver_screen.star_profile import (
    PROJECT_STARTERS,
    build_star_cast,
    identity_locked_description,
    normalize_star_profile,
    persist_star_profile,
    reorder_primary_reference,
    starter_payload,
    validate_star_profile,
)


def _image(name: str, byte: bytes) -> io.BytesIO:
    upload = io.BytesIO(byte * 2048)
    upload.name = name
    return upload


def test_blank_project_is_the_generic_default() -> None:
    blank = starter_payload(None)
    assert blank["label"] == "Blank original project"
    assert blank["lead_name"] == ""
    assert blank["mode"] == "fictional_character"
    assert "Moonie Moo" not in blank["title"]
    assert "moonie_moo_demo" in PROJECT_STARTERS
    assert starter_payload("moonie_moo_demo")["lead_name"] == "Moonie Moo"


def test_self_star_requires_reference_and_authorization() -> None:
    profile = normalize_star_profile(
        {
            "mode": "self",
            "subjectName": "Test Star",
            "authorizationConfirmed": False,
        }
    )
    with pytest.raises(ValueError, match="reference image"):
        validate_star_profile(profile, 0)
    with pytest.raises(ValueError, match="permission"):
        validate_star_profile(profile, 1)
    profile["authorizationConfirmed"] = True
    validate_star_profile(profile, 1)


def test_identity_lock_and_cast_keep_the_user_as_lead() -> None:
    profile = normalize_star_profile(
        {
            "mode": "self",
            "subjectName": "Alex Star",
            "role": "Lead detective",
            "appearance": "Short dark hair and rectangular glasses",
            "wardrobe": "Tailored navy suit",
            "identityInvariants": "Keep the glasses and hairstyle unchanged",
            "authorizationConfirmed": True,
        }
    )
    description = identity_locked_description(profile)
    assert "exact same real person" in description
    assert "Do not replace the lead" in description
    cast = build_star_cast(
        profile,
        support_name="Morgan",
        support_role="Partner",
        support_description="A distinct supporting detective",
    )
    assert cast[0]["name"] == "Alex Star"
    assert cast[0]["role"] == "Lead detective"
    assert cast[1]["name"] == "Morgan"


def test_primary_reference_is_passed_first() -> None:
    first = _image("front.jpg", b"A")
    second = _image("full-body.jpg", b"B")
    third = _image("three-quarter.jpg", b"C")
    ordered = reorder_primary_reference([first, second, third], 1)
    assert ordered[0] is second
    assert ordered[1] is first
    assert ordered[2] is third


def test_identity_pack_is_durable_and_has_no_biometric_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = RunWorkspace("runs", "ss_star_profile_test")
    profile = normalize_star_profile(
        {
            "mode": "self",
            "subjectName": "Alex Star",
            "role": "Lead protagonist",
            "authorizationConfirmed": True,
        }
    )
    result = persist_star_profile(
        workspace.run_id,
        profile,
        [_image("primary.jpg", b"Z")],
        output_root="runs",
    )
    profile_path = Path(result["profilePath"])
    assert profile_path.exists()
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert payload["subjectName"] == "Alex Star"
    assert payload["referenceCount"] == 1
    assert payload["biometricEmbeddingsCreated"] is False
    reference = Path(result["referencePaths"][0])
    assert reference.exists()
    manifest = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    assert "starProfile" in manifest["artifacts"]
    assert "identityReference01" in manifest["artifacts"]


def test_general_purpose_pages_compile() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "streamlit_app.py",
        root / "pages" / "0_Star_Vehicle_Studio.py",
        root / "pages" / "1_Full_Blueprint_Production.py",
        root / "pages" / "3_Voice_Studio.py",
        root / "silver_screen" / "star_vehicle_page.py",
    ]
    for path in paths:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
