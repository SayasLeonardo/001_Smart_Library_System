"""YuNet face detector wrapper. Always returns a plain list of faces."""

import threading

import cv2
import numpy as np


class FaceDetector:
    """Wraps cv2.FaceDetectorYN (needs OpenCV 4.8+ for the 2023mar model).

    detect() returns a list. Each item is a float32 array of 15 values:
    x, y, width, height, 5 landmark (x, y) pairs, and a confidence score.
    Items can be passed straight to FaceRecognizerSF.alignCrop().
    """

    def __init__(
        self,
        model_path,
        score_threshold=0.8,
        nms_threshold=0.3,
        top_k=5000,
        max_side=960,
        min_face_size=48,
    ):
        self._lock = threading.Lock()
        self._max_side = max_side
        self._min_face_size = min_face_size
        self._detector = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), score_threshold, nms_threshold, top_k
        )

    @staticmethod
    def _as_bgr(image):
        if image is None or getattr(image, "size", 0) == 0:
            return None
        if image.dtype != np.uint8:
            image = cv2.convertScaleAbs(image)
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if image.ndim == 3 and image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        if image.ndim == 3 and image.shape[2] == 3:
            return np.ascontiguousarray(image)
        return None

    def detect(self, image):
        """Return a list of faces, largest first. Empty list if none found."""
        image = self._as_bgr(image)
        if image is None:
            return []

        height, width = image.shape[:2]

        # Shrink very large images (e.g. phone photos used for enrollment)
        # so detection stays fast on a Raspberry Pi.
        scale = 1.0
        longest = max(height, width)
        if self._max_side and longest > self._max_side:
            scale = self._max_side / float(longest)
            image = cv2.resize(
                image,
                (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        small_height, small_width = image.shape[:2]
        with self._lock:
            self._detector.setInputSize((small_width, small_height))
            _, faces = self._detector.detect(image)

        if faces is None or len(faces) == 0:
            return []

        faces = np.asarray(faces, dtype=np.float32)
        if scale != 1.0:
            faces[:, :14] /= scale  # box + landmarks back to original coordinates

        # Ignore tiny faces (people far behind the person at the kiosk).
        if self._min_face_size:
            faces = faces[faces[:, 2] >= self._min_face_size]
            if len(faces) == 0:
                return []

        order = np.argsort(-(faces[:, 2] * faces[:, 3]))  # largest face first
        return [faces[i] for i in order]
