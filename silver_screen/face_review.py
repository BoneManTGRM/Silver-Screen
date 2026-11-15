"""Compare visible faces in a private clip with its starting reference."""

from __future__ import annotations

from io import BytesIO
from itertools import permutations
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps

from .ai_video import _ffmpeg_path


DETECTOR_FILE = "face_detection_yunet_2026may.onnx"
COMPARATOR_FILE = "face_recognition_sface_2021dec.onnx"
FACE_REVIEW_UNAVAILABLE = {"status": "unavailable"}
_SAMPLE_FPS = 8
_MAX_FRAMES = 97
# Use a closer match than a recognition check. The user is reviewing visual
# continuity, and a recognizable face can still look noticeably different.
_SIMILARITY = 0.76


class FaceReviewer:
    def __init__(self, model_dir: Path, photo: bytes):
        import cv2
        import numpy as np

        self.cv = cv2
        self.np = np
        self.detector = cv2.FaceDetectorYN.create(str(model_dir / DETECTOR_FILE), "", (320, 320), 0.80)
        self.comparator = cv2.FaceRecognizerSF.create(str(model_dir / COMPARATOR_FILE), "")
        with Image.open(BytesIO(photo)) as source:
            self.reference = self._features(ImageOps.exif_transpose(source), limit=3)

    def _features(self, source: Image.Image, *, limit: int) -> list:
        photo = source.convert("RGB")
        photo.thumbnail((960, 960), Image.Resampling.LANCZOS)
        image = self.cv.cvtColor(self.np.asarray(photo), self.cv.COLOR_RGB2BGR)
        self.detector.setInputSize(photo.size)
        _, detections = self.detector.detect(image)
        if detections is None:
            return []
        faces = sorted(
            (face for face in detections if min(face[2:4]) >= 36),
            key=lambda face: float(face[2] * face[3]), reverse=True,
        )
        return [
            self.comparator.feature(self.comparator.alignCrop(image, face)).copy()
            for face in faces[:limit]
        ]

    def _frame_score(self, frame: Image.Image) -> float | None:
        candidates = self._features(frame, limit=6)
        if len(candidates) < len(self.reference):
            return None
        scores = [
            [self.comparator.match(reference, face, self.cv.FaceRecognizerSF_FR_COSINE) for face in candidates]
            for reference in self.reference
        ]
        # Each reference must be matched to a different face. Choose the
        # assignment that best preserves the least-similar face in the group.
        return max(
            min(scores[index][match] for index, match in enumerate(assignment))
            for assignment in permutations(range(len(candidates)), len(scores))
        )

    def review(self, data: bytes) -> dict:
        if not self.reference:
            return {"status": "no_reference_face"}
        ffmpeg = _ffmpeg_path()
        if not ffmpeg:
            return FACE_REVIEW_UNAVAILABLE.copy()
        with TemporaryDirectory(prefix="silver-face-review-") as folder:
            root = Path(folder)
            video = root / "render.mp4"
            video.write_bytes(data)
            subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-threads", "1", "-i", str(video),
                 "-map", "0:V:0", "-vf", f"fps={_SAMPLE_FPS}", "-frames:v", str(_MAX_FRAMES),
                 "-an", "-q:v", "2", "-threads", "1", str(root / "frame-%03d.jpg")],
                check=True, capture_output=True, timeout=35,
            )
            scores = []
            for path in sorted(root.glob("frame-*.jpg")):
                with Image.open(path) as frame:
                    scores.append(self._frame_score(frame))
        return summarize_face_review(scores, reference_faces=len(self.reference))


def summarize_face_review(scores: list[float | None], *, reference_faces: int) -> dict:
    matching = sum(score is not None and score >= _SIMILARITY for score in scores)
    result = {"reference_faces": reference_faces, "sampled_frames": len(scores),
              "visible_frames": sum(score is not None for score in scores)}
    for index, frame in enumerate(zip(scores, scores[1:], scores[2:])):
        if all(score is not None and score < _SIMILARITY for score in frame):
            return dict(result, status="possible_drift", first_issue_seconds=index / _SAMPLE_FPS)
    return dict(result, status="matched" if len(scores) >= 3 and matching / len(scores) >= 0.75 else "inconclusive")


def face_review_message(review: dict) -> str:
    return {
        "possible_drift": "The face may have drifted from the starting photo. Compare the take with your reference before using it.",
        "inconclusive": "The face was not clear in enough frames to confirm continuity. Review the take against your reference.",
        "unavailable": "Silver Screen could not check face continuity for this take.",
        "no_reference_face": "The continuity check could not find a clear face in the starting image.",
        "matched": "Visible faces matched the starting photo in sampled frames.",
    }.get(review.get("status"), "")
