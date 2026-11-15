"""Look for loss of detail at a wrist that was visible in a reference image."""

from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from io import BytesIO
from itertools import product
from math import dist
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any, Iterable
import subprocess

from PIL import Image

from .ai_video import _ffmpeg_path

MODEL_DIR = Path("/opt/silver-hand-review")
HAND_MODEL = "hand_landmarker.task"
POSE_MODEL = "pose_landmarker_full.task"
REVIEW_FPS = 8
MAX_REVIEW_FRAMES = 100
Point = tuple[float, float]
HAND_REVIEW_NOT_CHECKED = {"status": "insufficient_reference"}
HAND_REVIEW_UNAVAILABLE = {"status": "unavailable"}
BODY_REVIEW_UNAVAILABLE = {"status": "unavailable"}
BODY_LABELS = {"1": "the person starting on the left", "2": "the person starting on the right"}
HAND_REVIEW_WARNINGS = {
    "possible_loss": "A hand could not be detected in parts of this take where the wrist appeared visible. Check the footage before using it.",
    "inconclusive": "Hand detail could only be checked in part of this clip. Review it before using the take.",
    "insufficient_reference": "No reference hand could be tracked well enough to check hand continuity.",
    "unavailable": "The hand-continuity check was unavailable for this render.",
}
BODY_SEGMENTS = {
    "shoulders": (11, 12), "hips": (23, 24),
    "left torso": (11, 23), "right torso": (12, 24),
    "left upper arm": (11, 13), "right upper arm": (12, 14),
    "left forearm": (13, 15), "right forearm": (14, 16),
    "left thigh": (23, 25), "right thigh": (24, 26),
    "left shin": (25, 27), "right shin": (26, 28),
}
BODY_DRIFT_RATIO = 1.4


@dataclass(frozen=True)
class Wrist:
    position: Point
    elbow: Point
    crop: tuple[int, int, int, int]

    @property
    def forearm(self) -> float:
        return dist(self.position, self.elbow)

    def has_full_crop(self, size: tuple[int, int]) -> bool:
        left, top, right, bottom = self.crop
        width, height = size
        return 0 <= left and 0 <= top and right <= width and bottom <= height

    def crop_at_scale(self, factor: float) -> tuple[int, int, int, int]:
        left, top, right, bottom = self.crop
        x, y = (left + right) / 2, (top + bottom) / 2
        radius = (right - left) * factor / 2
        return tuple(round(value) for value in (x - radius, y - radius, x + radius, y + radius))


@dataclass(frozen=True)
class Body:
    center: Point
    scale: float
    wrists: dict[str, Wrist]
    silhouette: tuple[tuple[Point, Point, float], ...] = ()
    measurements: dict[str, float] = field(default_factory=dict)


@dataclass
class Track:
    body: Body
    velocity: Point = (0.0, 0.0)
    missed: int = 0

    def cost(self, body: Body) -> float:
        x, y = self.body.center
        vx, vy = self.velocity if self.missed < 2 else (0.0, 0.0)
        size_ratio = body.scale / self.body.scale
        if not 0.65 <= size_ratio <= 1.60:
            return float("inf")
        return dist((x + vx, y + vy), body.center) / max(self.body.scale, body.scale) + abs(1 - size_ratio)

    def update(self, body: Body | None) -> None:
        if body is None:
            self.missed += 1
            return
        x, y = self.body.center
        self.velocity = ((body.center[0] - x) / (self.missed + 1), (body.center[1] - y) / (self.missed + 1))
        self.body, self.missed = body, 0


def _match_bodies(tracks: list[Track], candidates: list[Body]) -> list[Body | None]:
    """Associate nearby bodies without counting the same person twice."""
    options = range(len(candidates))
    costs = [[track.cost(candidate) for candidate in candidates] for track in tracks]
    ranked = []
    for assignment in product([None, *options], repeat=len(tracks)):
        assigned = [choice for choice in assignment if choice is not None]
        if len(assigned) != len(set(assigned)):
            continue
        score = sum(0.90 if choice is None else costs[index][choice] for index, choice in enumerate(assignment))
        ranked.append((score, assignment))
    ranked.sort(key=lambda option: option[0])
    best, choices = ranked[0]
    return [
        candidates[choice] if choice is not None and all(
            other[index] == choice for score, other in ranked[1:] if score - best < 0.14
        ) else None
        for index, choice in enumerate(choices)
    ]


def _near_segment(point: Point, start: Point, end: Point, radius: float) -> bool:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    u = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / max(1.0, length_squared)
    return 0 <= u <= 1 and dist(point, (start[0] + u * dx, start[1] + u * dy)) <= radius


def _expected_hand_behind_body(wrist: Wrist, bodies: list[Body]) -> bool:
    x, y = wrist.position
    ex, ey = wrist.elbow
    palm = (x + (x - ex) * 0.22, y + (y - ey) * 0.22)
    return any(_near_segment(palm, start, end, radius) for body in bodies for start, end, radius in body.silhouette)


class HandReviewer:
    """Use pose and local hand crops to check up to two performers."""

    def __init__(self, model_dir: Path, reference: bytes, resources: ExitStack) -> None:
        import mediapipe as mp
        import numpy as np

        vision = mp.tasks.vision
        self._mp = mp
        self._array = np.ascontiguousarray
        self.pose = resources.enter_context(vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(model_dir / POSE_MODEL)),
                num_poses=3,
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
        self.start = sorted(self._bodies(rgb), key=lambda body: body.center[0])
        reference_points = [wrist for body in self.start for wrist in body.wrists.values()]
        self.reference = {
            (index, side): wrist for index, body in enumerate(self.start) for side, wrist in body.wrists.items()
            if not self._overlaps_wrist(wrist, reference_points) and self._has_hand(rgb, wrist)
        }

    def _image(self, source: Image.Image):
        return self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=self._array(source))

    def _bodies(self, image: Image.Image) -> list[Body]:
        detections = self.pose.detect(self._image(image))
        if len(detections.pose_landmarks) > 2:
            return []
        return [body for pose, world in zip(detections.pose_landmarks, detections.pose_world_landmarks)
                if (body := self._body(image.size, pose, world)) is not None]

    @staticmethod
    def _body(size: tuple[int, int], landmarks: list[Any], world: list[Any]) -> Body | None:
        width, height = size

        def confidence(index: int) -> float:
            mark = landmarks[index]
            return min(mark.visibility or 0, mark.presence or 0)

        def point(index: int) -> Point:
            mark = landmarks[index]
            return mark.x * width, mark.y * height

        def midpoint(first: int, second: int) -> Point:
            x, y = point(first)
            u, v = point(second)
            return (x + u) / 2, (y + v) / 2

        def world_point(index: int) -> tuple[float, float, float]:
            mark = world[index]
            return mark.x, mark.y, mark.z

        if any(confidence(index) < 0.58 for index in (11, 12)):
            return None
        center = midpoint(11, 12)
        shoulders = dist(point(11), point(12))
        hips = midpoint(23, 24) if min(confidence(23), confidence(24)) >= 0.58 else None
        scale = max(32.0, shoulders, dist(center, hips) if hips else 0.0)
        silhouette = []
        if hips:
            silhouette.append((center, hips, max(12.0, 0.42 * shoulders)))
        for hip, knee in ((23, 25), (24, 26)):
            if min(confidence(hip), confidence(knee)) >= 0.58:
                silhouette.append((point(hip), point(knee), max(10.0, 0.20 * scale)))
        wrists = {}
        for side, shoulder, elbow, wrist in (("left", 11, 13, 15), ("right", 12, 14, 16)):
            if any(confidence(index) < 0.72 for index in (wrist, elbow, shoulder)):
                continue
            px, py = point(wrist)
            ex, ey = point(elbow)
            length = dist((px, py), (ex, ey))
            radius = round(min(230, max(42, length * 0.74)))
            x = round(px + (px - ex) * 0.18)
            y = round(py + (py - ey) * 0.18)
            crop = (x - radius, y - radius, x + radius, y + radius)
            if length >= 25 and 0 <= px < width and 0 <= py < height:
                wrists[side] = Wrist((px, py), (ex, ey), crop)
        measurements = {
            name: dist(world_point(first), world_point(second))
            for name, (first, second) in BODY_SEGMENTS.items()
            if all(confidence(index) >= 0.72 and 0 <= landmarks[index].x <= 1 and 0 <= landmarks[index].y <= 1
                   for index in (first, second))
        }
        return Body(center, scale, wrists, tuple(silhouette), measurements)

    @staticmethod
    def _overlaps_wrist(wrist: Wrist, wrists: list[Wrist]) -> bool:
        return any(
            other is not wrist
            and dist(wrist.position, other.position) <= max(16, min(wrist.forearm, other.forearm) * 0.45)
            for other in wrists
        )

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

    def _hand_key(self, index: int, side: str) -> str:
        return f"{index + 1}:{side}" if len(self.start) == 2 else side

    def _frame(
        self, image: Image.Image, tracks: list[Track], *, check_body: bool,
    ) -> tuple[dict[str, bool], dict[int, tuple[str, ...]]]:
        bodies = self._bodies(image)
        assignments = _match_bodies(tracks, bodies)
        positions = [wrist for body in bodies for wrist in body.wrists.values()]
        for track, body in zip(tracks, assignments):
            track.update(body)
        proportions = {}
        if check_body:
            for index, actor in enumerate(assignments):
                if actor is not None:
                    changed = measure_body_changes(self.start[index].measurements, actor.measurements)
                    if changed is not None:
                        proportions[index] = changed
        observations = {}
        for (index, side), reference in self.reference.items():
            actor = assignments[index]
            wrist = actor.wrists.get(side) if actor else None
            if wrist is None or self._overlaps_wrist(wrist, positions):
                continue
            detected = self._has_hand(image, wrist)
            original_ratio = reference.forearm / self.start[index].scale
            ratio = wrist.forearm / actor.scale / original_ratio
            if detected or (
                wrist.has_full_crop(image.size) and 0.67 <= ratio <= 1.50
                and not _expected_hand_behind_body(wrist, bodies)
            ):
                observations[self._hand_key(index, side)] = detected
        return observations, proportions

    def review(self, data: bytes) -> dict:
        return self.review_continuity(data)["hand_review"]

    def review_continuity(self, data: bytes, *, check_body: bool = False) -> dict:
        bodies = [index for index, body in enumerate(self.start) if len(body.measurements) >= 3] if check_body else []
        reviews = {"hand_review": HAND_REVIEW_NOT_CHECKED.copy()}
        if check_body:
            reviews["body_review"] = {"status": "insufficient_reference"}
        if not self.reference and not bodies:
            return reviews
        ffmpeg = _ffmpeg_path()
        if not ffmpeg:
            return {name: {"status": "unavailable"} for name in reviews}
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
                return {name: {"status": "unavailable"} for name in reviews}
            tracks = [Track(body) for body in self.start]
            hands, proportions = [], []
            for frame in frames:
                with Image.open(frame) as decoded:
                    hand_frame, body_frame = self._frame(decoded.convert("RGB"), tracks, check_body=bool(bodies))
                    hands.append(hand_frame)
                    proportions.append(body_frame)
        reviews["hand_review"] = summarize_hand_review((self._hand_key(*key) for key in self.reference), hands)
        if bodies:
            reviews["body_review"] = summarize_body_review(bodies, proportions)
        return reviews


def measure_body_changes(reference: dict[str, float], current: dict[str, float]) -> tuple[str, ...] | None:
    """Use visible 3D segments and compare ratios so camera scale and pose are less influential."""
    ratios = {name: current[name] / length for name, length in reference.items()
              if length > 0.01 and current.get(name, 0.0) > 0.01}
    if len(ratios) < 3:
        return None
    torso = [ratios[name] for name in ("shoulders", "hips", "left torso", "right torso") if name in ratios]
    if len(torso) < 2:
        return None
    scale = median(torso)
    return tuple(name for name, value in ratios.items() if not 1 / BODY_DRIFT_RATIO <= value / scale <= BODY_DRIFT_RATIO)


def summarize_body_review(bodies: Iterable[int], frames: list[dict[int, tuple[str, ...]]]) -> dict:
    indices = list(bodies)
    coverage = []
    for body in indices:
        eligible = sum(body in frame for frame in frames)
        coverage.append(eligible / max(1, len(frames)))
    # Isolated pose estimates can return to the normal range during a sustained change.
    # Require four samples for the same body part within a six-sample window.
    for end in range(3, len(frames)):
        start = max(0, end - 5)
        window = frames[start:end + 1]
        for body in indices:
            observations = [frame.get(body, ()) for frame in window]
            counts = Counter(part for parts in observations for part in parts)
            flagged = sorted(part for part, count in counts.items() if count >= 4)
            if flagged:
                first = next(index for index, parts in enumerate(observations, start) if any(part in parts for part in flagged))
                return {"status": "possible_distortion", "body_parts": flagged,
                        "first_issue_seconds": first / REVIEW_FPS, "sampled_frames": len(frames)}
    return {"status": "tracked" if len(frames) >= 4 and min(coverage, default=0) >= 0.65 else "inconclusive",
            "sampled_frames": len(frames)}


def body_review_message(review: dict) -> str:
    return {
        "possible_distortion": "Visible body proportions may have changed. Compare the take with the starting photo before using it.",
        "inconclusive": "The person was not clear in enough frames to confirm body proportions.",
        "insufficient_reference": "The starting photo did not show enough of the body to check its proportions.",
        "unavailable": "Silver Screen could not check body proportions for this take.",
        "tracked": "Visible body proportions stayed within the continuity check’s tolerance.",
    }.get(review.get("status"), "")


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
        if known >= len(samples) * 0.6 and first_missing is not None:
            suspected_at[side] = first_missing / REVIEW_FPS
    status = "possible_loss" if suspected_at else ("tracked" if min(coverage) >= 0.70 else "inconclusive")
    result = {"status": status, "reference_hands": len(sides), "sampled_frames": len(samples)}
    if suspected_at:
        result.update(suspect_sides=list(dict.fromkeys(key.split(":")[-1] for key in suspected_at)),
                      first_issue_seconds=min(suspected_at.values()))
        if any(":" in key for key in suspected_at):
            result["suspect_hands"] = list(suspected_at)
    return result


def hand_review_message(review: dict) -> str:
    status = review.get("status")
    if status == "possible_loss":
        seconds = review.get("first_issue_seconds")
        if isinstance(seconds, (int, float)) and 0 <= seconds <= MAX_REVIEW_FRAMES / REVIEW_FPS:
            affected = []
            suspects = review.get("suspect_hands")
            if isinstance(suspects, list):
                for key in suspects:
                    if isinstance(key, str):
                        index, _, side = key.partition(":")
                        if index in BODY_LABELS and side in ("left", "right"):
                            affected.append(f"{side} hand of {BODY_LABELS[index]}")
            detail = f" Look at the {' and the '.join(affected)}." if affected else ""
            return f"{HAND_REVIEW_WARNINGS['possible_loss']} Start around {seconds:.1f}s.{detail}"
    return HAND_REVIEW_WARNINGS.get(status, "") if isinstance(status, str) else ""
