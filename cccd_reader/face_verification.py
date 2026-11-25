"""So khớp khuôn mặt trên ảnh CCCD với một khung hình webcam trực tiếp."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DETECTOR = PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"
DEFAULT_RECOGNIZER = PROJECT_ROOT / "models" / "face_recognition_sface_2021dec.onnx"


@lru_cache(maxsize=1)
def _load_models():
    detector_model = Path(os.environ.get("CCCD_FACE_DETECTOR_MODEL", DEFAULT_DETECTOR)).expanduser()
    recognizer_model = Path(os.environ.get("CCCD_FACE_RECOGNIZER_MODEL", DEFAULT_RECOGNIZER)).expanduser()
    if not detector_model.is_file() or not recognizer_model.is_file():
        raise RuntimeError(
            "Chưa có model nhận diện khuôn mặt. Chạy: "
            ".venv/bin/python -m cccd_reader.download_face_models"
        )

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Chưa cài OpenCV. Chạy pip install -r requirements.txt.") from exc

    if not hasattr(cv2, "FaceDetectorYN_create") or not hasattr(cv2, "FaceRecognizerSF_create"):
        raise RuntimeError("OpenCV hiện tại thiếu FaceDetectorYN/FaceRecognizerSF; hãy cài opencv-contrib-python-headless.")

    detector = cv2.FaceDetectorYN_create(
        str(detector_model), "", (320, 320), 0.8, 0.3, 5000
    )
    recognizer = cv2.FaceRecognizerSF_create(str(recognizer_model), "")
    return cv2, detector, recognizer


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
    aligned = recognizer.alignCrop(image, faces[0])
    return recognizer.feature(aligned)


def compare_faces(card_photo: Path, camera_jpeg: bytes, threshold: float) -> tuple[float, bool]:
    """Trả về cosine similarity và kết quả so với ngưỡng đã cấu hình."""
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

    card_embedding = _embedding(cv2, detector, recognizer, card_image, "ảnh CCCD")
    live_embedding = _embedding(cv2, detector, recognizer, live_image, "camera")
    score = float(
        recognizer.match(card_embedding, live_embedding, cv2.FaceRecognizerSF_FR_COSINE)
    )
    return score, score >= threshold
