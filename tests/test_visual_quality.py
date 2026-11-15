from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from silver_screen import visual_quality
from silver_screen.ai_video import scene_prompt
from silver_screen.script_engine import build_film_from_brief


def _frames(root: Path, values: list[int]) -> list[Path]:
    paths = []
    for index, value in enumerate(values, start=1):
        image = Image.new("RGB", (192, 108), (value, value, value))
        if index % 2 == 0:
            for x in range(30, 160):
                image.putpixel((x, 54), (255 - value, 255 - value, 255 - value))
        path = root / f"frame_{index:03d}.jpg"
        image.save(path)
        paths.append(path)
    return paths


def test_stable_clip_report_is_explainable(monkeypatch, tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0" * 20000)
    frames = _frames(tmp_path, [90, 92, 94, 96, 98, 100, 102, 104])
    monkeypatch.setattr(visual_quality, "_sample_frames", lambda *args, **kwargs: frames)
    report = visual_quality.analyze_clip(
        clip,
        config={
            "acceptScore": 0.30,
            "hardRejectScore": 0.20,
            "minimumSharpness": 0.01,
            "maximumFlicker": 0.50,
            "maximumFreezeRatio": 0.95,
            "minimumIdentityConsistency": 0.10,
            "sampleFrames": 8,
        },
    )
    assert report["schemaVersion"] == 1
    assert report["identityMethod"].startswith("non-biometric")
    assert report["metrics"]["sampledFrames"] == 8
    assert 0 <= report["score"] <= 1


def test_flicker_and_freeze_create_targeted_findings(monkeypatch, tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0" * 20000)
    frames = _frames(tmp_path, [5, 250, 5, 250, 5, 250, 5, 250])
    monkeypatch.setattr(visual_quality, "_sample_frames", lambda *args, **kwargs: frames)
    report = visual_quality.analyze_clip(
        clip,
        config={
            "acceptScore": 0.90,
            "hardRejectScore": 0.80,
            "minimumSharpness": 0.80,
            "maximumFlicker": 0.05,
            "maximumFreezeRatio": 0.40,
            "minimumIdentityConsistency": 0.90,
            "sampleFrames": 8,
        },
    )
    codes = {item["code"] for item in report["findings"]}
    assert "flicker" in codes
    assert report["hardFailure"] is True
    assert report["repairDirective"]


def test_product_sharpness_distinguishes_plain_background_and_severe_blur(monkeypatch, tmp_path: Path) -> None:
    clip = tmp_path / "product.mp4"
    clip.touch()
    monkeypatch.setattr(visual_quality, "probe_media", lambda *args: {"duration": 4.0})
    monkeypatch.setattr(visual_quality, "_has_unplanned_cut", lambda *args: False)
    config = {"singleShot": True, "minimumSharpness": 0.18}

    def moving_mug(blur: int) -> list[Path]:
        frames = []
        for index in range(8):
            image = Image.new("RGB", (768, 448), (220, 202, 182))
            draw = ImageDraw.Draw(image)
            x = 230 + index * 25
            draw.rounded_rectangle((x, 95, x + 152, 347), radius=18, fill=(12, 97, 98))
            draw.rounded_rectangle((x - 4, 75, x + 156, 112), radius=8, fill=(65, 68, 68))
            draw.rectangle((x + 1, 85, x + 151, 98), fill=(209, 214, 216))
            draw.rectangle((x + 16, 323, x + 136, 352), fill=(29, 38, 40))
            path = tmp_path / f"{blur}-{index}.jpg"
            image.filter(ImageFilter.GaussianBlur(blur)).save(path)
            frames.append(path)
        return frames

    sharp = moving_mug(0)
    monkeypatch.setattr(visual_quality, "_sample_frames", lambda *args: sharp)
    report = visual_quality.analyze_clip(clip, config=config)
    assert not report["hardFailure"], report
    blurred = moving_mug(23)
    monkeypatch.setattr(visual_quality, "_sample_frames", lambda *args: blurred)
    report = visual_quality.analyze_clip(clip, config=config)
    assert report["hardFailure"]
    assert any(finding["code"] == "soft_or_blurred" for finding in report["findings"])


def test_visual_retake_directive_reaches_provider_prompt() -> None:
    state = build_film_from_brief(
        premise="A courier realizes a routine handoff is a surveillance test.",
        genre="thriller",
        tone="cinematic",
        title="Handoff",
        fmt="trailer",
        cast=[{"name": "Cody", "role": "Lead", "description": "Restrained operative"}],
        creative_direction={"profile": "modern_spy_thriller"},
        shot_direction={"audioStrategy": "dub_later"},
    )
    shot = {
        "id": "shot_0001",
        "order": 1,
        "segment": 1,
        "continuityUsed": False,
        "visualQualityRetake": {
            "directive": "Lock exposure and preserve sharp facial detail."
        },
    }
    prompt = scene_prompt(state, state["scenes"][0], shot)
    assert "TARGETED" not in prompt or "Lock exposure" in prompt
    assert "Lock exposure and preserve sharp facial detail" in prompt


def test_visual_quality_page_compiles() -> None:
    page = Path("pages/8_Visual_Quality_Supervisor.py")
    compile(page.read_text(encoding="utf-8"), str(page), "exec")
