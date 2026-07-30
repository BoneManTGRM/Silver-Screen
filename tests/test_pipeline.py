from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from silver_screen.cli import main as cli_main
from silver_screen.health import health_report
from silver_screen.media import HAS_PIL, process_media
from silver_screen.pipeline import BriefValidationError, run_pipeline, validate_brief
from silver_screen.runtime import RunWorkspace, list_runs
from silver_screen.science import FORMATS
from silver_screen.script_engine import build_film_from_brief
from silver_screen.tgrm import detect_fractures, run_tgrm

BASE_BRIEF = {
    "premise": (
        "A repair technician discovers that every system she fixes remembers "
        "the pain of the break and begins to dream."
    ),
    "genre": "scifi",
    "tone": "cinematic",
    "format": "short",
}


def test_validate_brief_normalizes_aliases_and_seed() -> None:
    normalized = validate_brief(
        {
            "premise": "A doctor finds a city that forgets one resident every sunrise.",
            "genre": "science fiction",
            "tone": "poetic",
            "format": "movie",
        }
    )
    assert normalized["genre"] == "scifi"
    assert normalized["tone"] == "cinematic"
    assert normalized["format"] == "feature"
    assert isinstance(normalized["seed"], int)


def test_validate_brief_rejects_invalid_input() -> None:
    with pytest.raises(BriefValidationError):
        validate_brief({"premise": "Too short"})
    with pytest.raises(BriefValidationError):
        validate_brief({"premise": "A valid premise with enough detail.", "seed": "not-a-number"})


def test_generation_is_reproducible() -> None:
    normalized = validate_brief(BASE_BRIEF)
    first = build_film_from_brief(
        premise=normalized["premise"],
        genre=normalized["genre"],
        tone=normalized["tone"],
        fmt=normalized["format"],
        seed=normalized["seed"],
    )
    second = build_film_from_brief(
        premise=normalized["premise"],
        genre=normalized["genre"],
        tone=normalized["tone"],
        fmt=normalized["format"],
        seed=normalized["seed"],
    )
    assert first["id"] == second["id"]
    assert first["script"] == second["script"]
    assert first["scenes"] == second["scenes"]


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_all_formats_build_balanced_structures(fmt: str) -> None:
    film = build_film_from_brief(
        premise=BASE_BRIEF["premise"],
        genre="scifi",
        tone="cinematic",
        fmt=fmt,
        seed=42,
    )
    meta = FORMATS[fmt]
    assert len(film["scenes"]) == meta["scenes"]
    assert len(film["acts"]) == meta["acts"]
    assert len(film["chapters"]) == meta["chapters"]
    counts = [act["scene_count"] for act in film["acts"]]
    assert max(counts) - min(counts) <= 1
    assert "FADE OUT." in film["script"]
    assert "THE END" in film["script"]


def test_pipeline_repairs_and_finishes_without_remaining_fractures() -> None:
    result = run_pipeline(BASE_BRIEF, persist=False, render_media=False)
    assert result["status"] == "complete"
    assert result["metrics"]["finalScore"] >= result["metrics"]["initialScore"]
    assert result["metrics"]["rolledBackRepairs"] == 0
    assert result["remainingFractures"] == []
    assert detect_fractures(result["state"]) == []


def test_tgrm_restores_missing_ending() -> None:
    film = build_film_from_brief(
        premise=BASE_BRIEF["premise"],
        genre="scifi",
        tone="cinematic",
        fmt="short",
        seed=99,
    )
    film["script"] = film["script"].replace("FADE OUT.", "").replace("THE END", "")
    assert any(fracture.class_ == "missing_ending" for fracture in detect_fractures(film))
    repaired = run_tgrm(film, max_cycles=8, energy_budget=60)
    assert "FADE OUT." in repaired["state"]["script"]
    assert "THE END" in repaired["state"]["script"]
    assert not any(
        fracture["class_"] == "missing_ending"
        for fracture in repaired["remainingFractures"]
    )


def test_energy_budget_stops_before_unaffordable_repair() -> None:
    film = build_film_from_brief(
        premise=BASE_BRIEF["premise"],
        genre="scifi",
        tone="cinematic",
        fmt="short",
        seed=101,
    )
    film["script"] = film["script"].replace("FADE OUT.", "").replace("THE END", "")
    result = run_tgrm(film, max_cycles=8, energy_budget=3)
    assert result["metrics"]["stopReason"] == "energy_budget_exhausted"
    assert result["metrics"]["acceptedRepairs"] == 0
    assert any(
        fracture["class_"] == "missing_ending"
        for fracture in result["remainingFractures"]
    )


def test_pipeline_persists_complete_bundle(tmp_path: Path) -> None:
    result = run_pipeline(
        BASE_BRIEF,
        output_root=str(tmp_path),
        persist=True,
        render_media=False,
    )
    workspace = Path(result["run"]["workspace"])
    manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["progress"] == 100
    for name in ("brief", "film", "outline", "screenplay", "tgrm", "result", "bundle"):
        assert name in result["artifacts"]
        assert Path(result["artifacts"][name]).exists()
    bundle = Path(result["artifacts"]["bundle"])
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert {"manifest.json", "screenplay.txt", "film.json", "tgrm.json"} <= names


def test_list_runs_reads_durable_manifest(tmp_path: Path) -> None:
    result = run_pipeline(
        BASE_BRIEF,
        output_root=str(tmp_path),
        persist=True,
        render_media=False,
    )
    records = list_runs(tmp_path)
    assert records
    assert records[0]["runId"] == result["run"]["id"]
    assert records[0]["status"] == "complete"


def test_workspace_rejects_path_escape(tmp_path: Path) -> None:
    workspace = RunWorkspace(tmp_path, "ss_safe_test")
    with pytest.raises(ValueError):
        workspace.write_text("../escape.txt", "not allowed")


@pytest.mark.skipif(not HAS_PIL, reason="Pillow is optional")
def test_media_cards_are_valid_pngs(tmp_path: Path) -> None:
    film = build_film_from_brief(
        premise=BASE_BRIEF["premise"],
        genre="scifi",
        tone="cinematic",
        fmt="short",
        seed=88,
    )
    media = process_media(film, out_dir=tmp_path, max_chapters=2, video_mode="cards")
    assert media["ok"] is True
    assert len(media["card_paths"]) == 2
    for path in media["card_paths"]:
        data = Path(path).read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")


def test_health_report_has_operational_contract(tmp_path: Path) -> None:
    report = health_report(tmp_path)
    assert report["ready"] is True
    assert report["checks"]["outputWritable"] is True
    assert report["status"] in {"ready", "degraded"}


def test_cli_validate_and_run_without_persistence(capsys: pytest.CaptureFixture[str]) -> None:
    validate_code = cli_main(
        [
            "validate",
            "--premise",
            "A courier discovers that each delivered letter changes yesterday.",
            "--genre",
            "thriller",
        ]
    )
    assert validate_code == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["genre"] == "thriller"

    run_code = cli_main(
        [
            "run",
            "--premise",
            "A courier discovers that each delivered letter changes yesterday.",
            "--genre",
            "thriller",
            "--media",
            "off",
            "--no-persist",
            "--json",
        ]
    )
    assert run_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "complete"
    assert summary["workspace"] is None


def test_custom_cast_is_preserved() -> None:
    result = run_pipeline(
        {
            **BASE_BRIEF,
            "cast": [
                {"name": "Sara Vale", "role": "Lead engineer"},
                {"name": "Nico Hart", "role": "Witness"},
            ],
        },
        persist=False,
        render_media=False,
    )
    names = [character["name"] for character in result["state"]["characters"]]
    assert names == ["Sara Vale", "Nico Hart"]
