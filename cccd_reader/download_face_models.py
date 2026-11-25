"""Tải các model YuNet/SFace từ OpenCV Zoo vào thư mục models/."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def main() -> int:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for filename, (url, expected_hash) in MODELS.items():
        target = MODEL_DIR / filename
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected_hash:
            print(f"Đã có model hợp lệ: {target}")
            continue

        temporary = target.with_suffix(target.suffix + ".download")
        digest = hashlib.sha256()
        print(f"Đang tải {filename}…")
        try:
            with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected_hash:
                raise RuntimeError(f"SHA-256 không khớp cho {filename}; xóa file tải dở.")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Đã lưu: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
