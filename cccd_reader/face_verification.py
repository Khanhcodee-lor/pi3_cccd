"""So khớp khuôn mặt trên ảnh CCCD với một khung hình webcam trực tiếp."""

from __future__ import annotations

import os
from pathlib import Path
import threading

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DETECTOR = PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"
DEFAULT_RECOGNIZER = PROJECT_ROOT / "models" / "face_recognition_sface_2021dec.onnx"
_MODEL_LOCK = threading.Lock()
_MODEL_CACHE = None


def _load_models():
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    with _MODEL_LOCK:
        if _MODEL_CACHE is not None:
            return _MODEL_CACHE

    detector_model = Path(os.environ.get("CCCD_FACE_DETECTOR_MODEL", DEFAULT_DETECTOR)).expanduser()
    recognizer_model = Path(os.environ.get("CCCD_FACE_RECOGNIZER_MODEL", DEFAULT_RECOGNIZER)).expanduser()
    if detector_model == DEFAULT_DETECTOR or recognizer_model == DEFAULT_RECOGNIZER:
        try:
            from .download_face_models import ensure_models

            ensure_models()
        except Exception as exc:
            raise RuntimeError(
                "Không tự tải được model nhận diện khuôn mặt. Pi cần có Internet; "
                f"hãy thử lại sau. Chi tiết: {exc}"
            ) from exc

    if not detector_model.is_file() or not recognizer_model.is_file():
        missing = [str(path) for path in (detector_model, recognizer_model) if not path.is_file()]
        raise RuntimeError(f"Không tìm thấy model nhận diện khuôn mặt: {', '.join(missing)}")

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Chưa cài OpenCV. Chạy pip install -r requirements.txt.") from exc

    if not hasattr(cv2, "FaceDetectorYN_create") or not hasattr(cv2, "FaceRecognizerSF_create"):
        raise RuntimeError("OpenCV hiện tại thiếu FaceDetectorYN/FaceRecognizerSF; hãy cài opencv-contrib-python-headless.")

    with _MODEL_LOCK:
        if _MODEL_CACHE is not None:
            return _MODEL_CACHE
        detector = cv2.FaceDetectorYN_create(
            str(detector_model), "", (320, 320), 0.8, 0.3, 5000
        )
        recognizer = cv2.FaceRecognizerSF_create(str(recognizer_model), "")
        _MODEL_CACHE = cv2, detector, recognizer
        return _MODEL_CACHE


def preload_face_models() -> None:
    """Tải và nạp model vào bộ nhớ để sẵn sàng xác thực."""
    _load_models()


def _decode_image(cv2, content: bytes, source: str):
    import numpy as np

    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise RuntimeError(f"Không đọc được ảnh {source}; hãy kiểm tra định dạng ảnh trên CCCD/webcam.")
    return image


def _embedding(cv2, detector, recognizer, image, source: str):
    detector.setInputSize((image.shape[1], image.shape[0]))
    _result, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        raise RuntimeError(f"Không tìm thấy khuôn mặt trong {source}.")
    if len(faces) != 1:
        raise RuntimeError(f"Cần đúng một khuôn mặt trong {source}.")
    face = faces[0]
    aligned = recognizer.alignCrop(image, face)
    landmarks = _face_landmarks(image, face)
    return recognizer.feature(aligned), landmarks


def _face_landmarks(image, face) -> dict[str, object]:
    height, width = image.shape[:2]
    points = [
        [float(face[offset]), float(face[offset + 1])]
        for offset in range(4, 14, 2)
    ]
    return {
        "width": width,
        "height": height,
        "box": [float(value) for value in face[:4]],
        "points": points,
    }


def compare_faces(card_photo: Path, camera_jpeg: bytes, threshold: float) -> tuple[float, bool]:
    """Trả về cosine similarity và kết quả so với ngưỡng đã cấu hình."""
    score, matched, _landmarks = compare_faces_with_details(
        card_photo, camera_jpeg, threshold
    )
    return score, matched


def compare_faces_with_details(
    card_photo: Path, camera_jpeg: bytes, threshold: float
) -> tuple[float, bool, dict[str, dict[str, object]]]:
    """So khớp khuôn mặt và trả thêm khung, mốc để giao diện hiển thị."""
    if not card_photo.is_file():
        raise RuntimeError("Không còn ảnh chân dung từ CCCD để đối chiếu. Hãy quét lại thẻ.")

    cv2, detector, recognizer = _load_models()
    card_image = cv2.imread(str(card_photo), cv2.IMREAD_COLOR)
    if card_image is None:
        try:
            import numpy as np
            from PIL import Image

            with Image.open(card_photo) as source:
                rgb = np.asarray(source.convert("RGB"))
            card_image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except Exception as exc:
            raise RuntimeError("Không giải mã được ảnh DG2 trên CCCD.") from exc
    live_image = _decode_image(cv2, camera_jpeg, "camera")

    card_embedding, card_landmarks = _embedding(
        cv2, detector, recognizer, card_image, "ảnh CCCD"
    )
    live_embedding, live_landmarks = _embedding(
        cv2, detector, recognizer, live_image, "camera"
    )
    score = float(
        recognizer.match(card_embedding, live_embedding, cv2.FaceRecognizerSF_FR_COSINE)
    )
    return score, score >= threshold, {
        "card": card_landmarks,
        "camera": live_landmarks,
    }
