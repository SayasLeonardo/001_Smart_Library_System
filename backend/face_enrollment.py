"""Server-side face registration for uploaded People Information images.

Uses the same OpenCV YuNet (detection) and SFace (recognition) models as the camera
station, with the same alignment and L2 normalisation, so encodings created here are
interchangeable with the ones the station creates.

Run `python backend/face_enrollment.py` during the Render build to pre-download models.
"""

import math
import os
import threading
import urllib.request
from pathlib import Path

MODEL_DIRECTORY = Path(
    os.environ.get("SMART_LIBRARY_MODEL_DIR") or (Path(__file__).resolve().parent / "models")
)
MODEL_URLS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx"
    ),
}
MIN_MODEL_BYTES = 100_000  # guards against saving an HTML/LFS-pointer error page as a model
MAX_SIDE = 1600
ENCODING_SIZE = 128


class FaceImageError(ValueError):
    """The image itself cannot be registered (no face, several faces, unreadable)."""


class FaceEngineUnavailable(RuntimeError):
    """OpenCV or the models are not available on this server."""


_lock = threading.Lock()
_engine = None


def ensure_models(download=True):
    MODEL_DIRECTORY.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in MODEL_URLS.items():
        path = MODEL_DIRECTORY / name
        if not path.is_file() or path.stat().st_size < MIN_MODEL_BYTES:
            if not download:
                raise FaceEngineUnavailable(f"Face model {name} is missing.")
            temporary = path.with_suffix(".download")
            try:
                with urllib.request.urlopen(url, timeout=120) as response, open(temporary, "wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                if temporary.stat().st_size < MIN_MODEL_BYTES:
                    raise FaceEngineUnavailable(f"Downloaded {name} is incomplete.")
                temporary.replace(path)
            except FaceEngineUnavailable:
                temporary.unlink(missing_ok=True)
                raise
            except Exception as error:  # network, DNS, disk
                temporary.unlink(missing_ok=True)
                raise FaceEngineUnavailable(f"Could not download {name}: {error}") from error
        paths[name] = path
    return paths


def _load_engine():
    global _engine
    if _engine is not None:
        return _engine
    try:
        import cv2
        import numpy  # noqa: F401
    except ImportError as error:
        raise FaceEngineUnavailable("OpenCV is not installed on the server.") from error
    paths = ensure_models()
    detector = cv2.FaceDetectorYN.create(
        str(paths["face_detection_yunet_2023mar.onnx"]), "", (320, 320), 0.7, 0.3, 5000
    )
    recognizer = cv2.FaceRecognizerSF.create(str(paths["face_recognition_sface_2021dec.onnx"]), "")
    _engine = (cv2, detector, recognizer)
    return _engine


def engine_available():
    try:
        with _lock:
            _load_engine()
        return True
    except FaceEngineUnavailable:
        return False


def encode_face_image(image_bytes):
    """Return a normalised 128-value face encoding for an image containing one face."""
    with _lock:
        cv2, detector, recognizer = _load_engine()
        import numpy as np

        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise FaceImageError("The image could not be read. Choose a valid PNG, JPEG, GIF, or WebP image.")
        height, width = image.shape[:2]
        largest = max(height, width)
        if largest > MAX_SIDE:
            scale = MAX_SIDE / largest
            image = cv2.resize(
                image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA
            )
            height, width = image.shape[:2]

        detector.setInputSize((width, height))
        _, faces = detector.detect(image)
        if faces is None or len(faces) == 0:
            raise FaceImageError(
                "No face was detected. Use a clear, front-facing photo with the face fully visible."
            )
        if len(faces) > 1:
            raise FaceImageError(
                f"Detected {len(faces)} faces. Use a photo containing only one person."
            )
        aligned = recognizer.alignCrop(image, faces[0])
        embedding = recognizer.feature(aligned).reshape(-1).astype(np.float32)

    values = [float(value) for value in embedding]
    if len(values) != ENCODING_SIZE or not all(math.isfinite(value) for value in values):
        raise FaceImageError("The face model returned an invalid encoding. Try a different photo.")
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude < 0.000001:
        raise FaceImageError("The face model returned an empty encoding. Try a different photo.")
    return [value / magnitude for value in values]


if __name__ == "__main__":
    # Build-time hook: failing to download must not fail the whole deploy; the backend then
    # falls back to leaving faces pending for the camera station, as before.
    try:
        ensure_models()
        print("Face models ready in", MODEL_DIRECTORY)
    except FaceEngineUnavailable as error:
        print("WARNING:", error)