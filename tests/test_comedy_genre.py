from __future__ import annotations

from silver_screen.pipeline import validate_brief
from silver_screen.script_engine import build_film_from_brief


def test_comedy_is_a_first_class_operational_genre() -> None:
    normalized = validate_brief(
        {
            "premise": "A celebrity cow and her grumpy bulldog steal the spotlight at a chaotic premiere.",
            "genre": "comedy",
            "tone": "cinematic",
            "format": "trailer",
        }
    )
    assert normalized["genre"] == "comedy"
    film = build_film_from_brief(
        premise=normalized["premise"],
        genre=normalized["genre"],
        tone=normalized["tone"],
        fmt=normalized["format"],
        seed=normalized["seed"],
    )
    assert film["genre"] == "comedy"
    assert film["scenes"]
    assert "THE END" in film["script"]
