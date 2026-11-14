"""Look for loss of detail at a wrist that was visible in a reference image."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from io import BytesIO
from math import dist
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable
import subprocess

from PIL import Image

from .ai_video import _ffmpeg_path

MODEL_DIR = Path("/opt/silver-hand-review")
HAND_MODEL = "hand_landmarker.task"
POSE_MODEL = "pose_landmarker_full.task"
REVIEW_FPS = 8
MAX_REVIEW_FRAMES = 100
HAND_REVIEW_NOT_CHECKED = {"status": "insufficient_reference"}
HAND_REVIEW_UNAVAILABLE = {"status": "unavailable"}
HAND_REVIEW_WARNINGS = {
    "possible_loss": "A hand could not be detected in parts of this take where the wrist appeared visible. Check the footage before using it.",
    "inconclusive": "Hand detail could only be checked in part of this clip. Review it before using the take.",
    "insufficient_reference": "No reference hand could be tracked well enough to check hand continuity.",
    "unavailable": "The hand-continuity check was unavailable for this render.",
}


@dataclass(frozen=True)
class Wrist:
    position: tuple[float, float]
    elbow: tuple[float, float]
    crop: tuple[int, int, int, int]

    @property
    def forearm(self) -> float:
        return dist(self.position, self.elbow)

    def crop_at_scale(self, factor: float) -> tuple[int, int, int, int]:
        left, top, right, bottom = self.crop
        x, y = (left + right) / 2, (top + bottom) / 2
        radius = (right - left) * factor / 2
        return tuple(round(value) for value in (x - radius, y - radius, x + radius, y + radius))


class HandReviewer:
    """Use pose to locate the wrists and run a separate detector on each crop."""

    def __init__(self, model_dir: Path, reference: bytes, resources: ExitStack) -> None:
        import mediapipe as mp
        import numpy as np

        vision = mp.tasks.vision
        self._mp = mp
        self._array = np.ascontiguousarray
        self.pose = resources.enter_context(vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(model_dir / POSE_MODEL)),
                num_poses=2,
            )
        ))
        self.hands = resources.enter_context(vision.HandLandmarker.create_from_options(
            vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(model_dir / HAND_MODEL)),
                num_hands=2,
            )
        ))
        with Image.open(BytesIO(reference)) as photo:
            rgb = photo.convert("RGB")
        self.reference = {side: wrist for side, wrist in self._wrists(rgb).items() if self._has_hand(rgb, wrist)}

    def _image(self, source: Image.Image):
        return self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=self._array(source))

    def _wrists(self, image: Image.Image) -> dict[str, Wrist]:
        bodies = self.pose.detect(self._image(image)).pose_landmarks
        if len(bodies) != 1:
            return {}
        width, height = image.size
        body = bodies[0]
        wrists = {}
        for side, shoulder, elbow, wrist in (("left", 11, 13, 15), ("right", 12, 14, 16)):
            point, joint = body[wrist], body[elbow]
            if any(
                min(part.visibility or 0, part.presence or 0) < 0.72
                for part in (point, joint, body[shoulder])
            ):
                continue
            px, py = point.x * width, point.y * height
            ex, ey = joint.x * width, joint.y * height
            length = dist((px, py), (ex, ey))
            radius = round(min(230, max(42, length * 0.74)))
            x = round(px + (px - ex) * 0.18)
            y = round(py + (py - ey) * 0.18)
            crop = (x - radius, y - radius, x + radius, y + radius)
            if length >= 25 and 0 <= crop[0] < crop[2] <= width and 0 <= crop[1] < crop[3] <= height:
                wrists[side] = Wrist((px, py), (ex, ey), crop)
        return wrists

    def _has_hand(self, image: Image.Image, wrist: Wrist) -> bool:
        for bounds in (wrist.crop, wrist.crop_at_scale(0.75)):
            crop = image.crop(bounds).resize((256, 256), Image.Resampling.LANCZOS)
            left, top, right, bottom = bounds
            landmarks = self.hands.detect(self._image(crop)).hand_landmarks
            for points in landmarks:
                if len(points) != 21:
                    continue
                location = left + points[0].x * (right - left), top + points[0].y * (bottom - top)
                if dist(location, wrist.position) <= max(16, wrist.forearm * 0.40):
                    return True
        return False

    def _frame(self, image: Image.Image) -> dict[str, bool]:
        wrists = self._wrists(image)
        observations = {}
        for side, reference in self.reference.items():
            wrist = wrists.get(side)
            if wrist is None:
                continue
            detected = self._has_hand(image, wrist)
            if detected or 0.70 <= wrist.forearm / reference.forearm <= 1.40:
                observations[side] = detected
        return observations

    def review(self, data: bytes) -> dict:
        if not self.reference:
            return HAND_REVIEW_NOT_CHECKED.copy()
        ffmpeg = _ffmpeg_path()
        if not ffmpeg:
            return HAND_REVIEW_UNAVAILABLE.copy()
        with TemporaryDirectory(prefix="silver-hand-review-") as directory:
            root = Path(directory)
            source = root / "render.mp4"
            source.write_bytes(data)
            # Keep the source resolution. The hands can occupy just tens of pixels.
            subprocess.run([
                ffmpeg, "-nostdin", "-v", "error", "-xerror", "-threads", "1", "-i", str(source),
                "-map", "0:V:0", "-vf", f"fps={REVIEW_FPS}", "-frames:v", str(MAX_REVIEW_FRAMES),
                "-an", "-q:v", "2", "-threads", "1", str(root / "frame-%03d.jpg"),
            ], check=True, capture_output=True, timeout=30)
            frames = sorted(root.glob("frame-*.jpg"))
            if not frames:
                return HAND_REVIEW_UNAVAILABLE.copy()
            samples = []
            for frame in frames:
                with Image.open(frame) as decoded:
                    samples.append(self._frame(decoded.convert("RGB")))
        return summarize_hand_review(self.reference, samples)


def summarize_hand_review(reference_sides: Iterable[str], samples: list[dict[str, bool]]) -> dict:
    """Only flag sustained failure to detect a hand at a clearly visible wrist."""
    sides = tuple(reference_sides)
    if not sides:
        return HAND_REVIEW_NOT_CHECKED.copy()
    suspected_at = {}
    coverage = []
    for side in sides:
        current = detected = known = 0
        first_missing = None
        for index, frame in enumerate(samples):
            value = frame.get(side)
            known += value is not None
            detected += value is True
            current = current + 1 if value is False else 0
            if first_missing is None and current >= 4:
                first_missing = index + 1 - current
        coverage.append(detected / max(1, len(samples)))
        if known >= len(samples) * 0.6 and first_missing is not None and known - detected >= known * 0.27:
            suspected_at[side] = first_missing / REVIEW_FPS
    status = "possible_loss" if suspected_at else ("tracked" if min(coverage) >= 0.70 else "inconclusive")
    result = {"status": status, "reference_hands": len(sides), "sampled_frames": len(samples)}
    if suspected_at:
        result.update(suspect_sides=list(suspected_at), first_issue_seconds=min(suspected_at.values()))
    return result


def hand_review_message(review: dict) -> str:
    status = review.get("status")
    if status == "possible_loss":
        seconds = review.get("first_issue_seconds")
        if isinstance(seconds, (int, float)) and 0 <= seconds <= MAX_REVIEW_FRAMES / REVIEW_FPS:
            return f"{HAND_REVIEW_WARNINGS['possible_loss']} Start around {seconds:.1f}s."
    return HAND_REVIEW_WARNINGS.get(status, "") if isinstance(status, str) else ""
