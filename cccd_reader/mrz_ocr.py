"""Nhận dạng MRZ TD1 từ ảnh CCCD bằng Tesseract cài trên máy chủ."""

from __future__ import annotations

from io import BytesIO
import os
import re
import shutil
import subprocess
import tempfile
import warnings
from collections.abc import Iterator
from pathlib import Path


_ALLOWED = re.compile(r"[^A-Z0-9<]")
_DIGIT_FIXES = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"})


def _check_digit(value: str) -> str:
    weights = (7, 3, 1)
    total = 0
    for index, char in enumerate(value):
        if char == "<":
            number = 0
        elif char.isdigit():
            number = int(char)
        elif "A" <= char <= "Z":
            number = ord(char) - ord("A") + 10
        else:
            return "?"
        total += number * weights[index % 3]
    return str(total % 10)


def _normalized_digits(value: str) -> str:
    return value.translate(_DIGIT_FIXES)


def _mrz_rows(text: str) -> list[str]:
    rows = []
    for line in text.upper().splitlines():
        cleaned = _ALLOWED.sub("", line)
        if len(cleaned) >= 22:
            rows.append(cleaned)
    return rows


def _key_from_text(text: str) -> str | None:
    rows = _mrz_rows(text)
    first_rows = [row for row in rows if row.startswith(("ID", "1D")) and len(row) >= 28]
    second_rows = [row for row in rows if len(row) >= 28 and row[:6].translate(_DIGIT_FIXES).isdigit()]

    for first in first_rows:
        for second in second_rows:
            # MRZ TD1 has 30 characters per row. OCR can occasionally add or
            # drop a character, so test nearby 30-character windows.
            first_variants = [first[start : start + 30] for start in range(max(1, len(first) - 29)) if len(first[start : start + 30]) == 30]
            second_variants = [second[start : start + 30] for start in range(max(1, len(second) - 29)) if len(second[start : start + 30]) == 30]
            for line1 in first_variants:
                if line1[:2] == "1D":
                    line1 = "ID" + line1[2:]
                for line2 in second_variants:
                    document = line1[5:14]
                    document_check = _normalized_digits(line1[14:15])
                    birth = _normalized_digits(line2[0:6])
                    birth_check = _normalized_digits(line2[6:7])
                    expiry = _normalized_digits(line2[8:14])
                    expiry_check = _normalized_digits(line2[14:15])
                    if not birth.isdigit() or not expiry.isdigit():
                        continue

                    # Vietnamese citizen ID numbers are numeric. Repair only
                    # common OCR confusions there, and require all three ICAO
                    # check digits before returning a key.
                    if document.isalnum():
                        document = _normalized_digits(document)
                    if (
                        document.isdigit()
                        and document_check.isdigit()
                        and birth_check.isdigit()
                        and expiry_check.isdigit()
                        and _check_digit(document) == document_check
                        and _check_digit(birth) == birth_check
                        and _check_digit(expiry) == expiry_check
                    ):
                        return document + document_check + birth + birth_check + expiry + expiry_check
    return None


def _ocr(
    image_bytes: bytes,
    *,
    rotations: tuple[int, ...] = (0, 90, 180, 270),
    fast: bool = False,
) -> Iterator[str]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pi chưa cài Pillow. Chạy: sudo apt install python3-pil") from exc

    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("Pi chưa cài Tesseract OCR. Chạy: sudo apt install tesseract-ocr")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(image_bytes))
            if image.width * image.height > 20_000_000:
                raise ValueError("Ảnh quá lớn; hãy chụp hoặc chọn ảnh nhỏ hơn 20 megapixel.")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.load()
    except (OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("Không đọc được ảnh. Chọn ảnh JPG, PNG hoặc WebP rõ nét.") from exc

    for angle in rotations:
        oriented = image.rotate(angle, expand=True) if angle else image
        # In live mode the MRZ is expected along the lower edge of a horizontal card.
        crops = (
            [oriented.crop((0, oriented.height * 2 // 5, oriented.width, oriented.height))]
            if fast
            else [oriented, oriented.crop((0, oriented.height // 2, oriented.width, oriented.height))]
        )
        for crop_index, crop in enumerate(crops):
            gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
            if fast:
                # 720p webcam frames already have enough detail for MRZ OCR.
                # Only enlarge lower-resolution streams; upscaling 720p adds
                # work on the Pi without restoring detail.
                if gray.width < 800:
                    gray = gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS)
                elif gray.width > 2000:
                    ratio = 2000 / gray.width
                    gray = gray.resize((2000, int(gray.height * ratio)), Image.Resampling.LANCZOS)
            elif gray.width < 1400:
                scale = min(3, max(2, 1400 // max(1, gray.width)))
                gray = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
            elif gray.width > 2600:
                ratio = 2600 / gray.width
                gray = gray.resize((2600, int(gray.height * ratio)), Image.Resampling.LANCZOS)
            source = BytesIO()
            gray.save(source, format="PNG")
            modes = (6,) if fast or crop_index == 0 else (6, 11)
            for psm in modes:
                try:
                    result = subprocess.run(
                        [executable, "stdin", "stdout", "--psm", str(psm), "-c", "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"],
                        input=source.getvalue(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        timeout=5 if fast else 10,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    continue
                except OSError as exc:
                    raise RuntimeError("Không khởi chạy được Tesseract OCR trên Pi.") from exc
                if result.returncode == 0:
                    yield result.stdout.decode("utf-8", errors="ignore")


def read_mrz_key(image_bytes: bytes) -> str:
    """Đọc MRZ key info 24 ký tự từ ảnh; từ chối nếu check digit không khớp."""
    if not image_bytes:
        raise ValueError("Chưa nhận được ảnh.")
    for output in _ocr(image_bytes):
        key = _key_from_text(output)
        if key:
            return key
    raise ValueError("Chưa đọc chắc được MRZ. Hãy chụp thẳng, đủ sáng và để rõ cả 2 dòng đầu của mã MRZ.")


def capture_camera_image() -> bytes:
    """Chụp một khung hình tạm từ webcam USB nối với Pi."""
    executable = shutil.which("fswebcam")
    if not executable:
        raise RuntimeError("Pi chưa cài fswebcam. Chạy: sudo apt install fswebcam")

    device = os.environ.get("CCCD_CAMERA_DEVICE", "/dev/video0")
    try:
        with tempfile.TemporaryDirectory(prefix="cccd-mrz-") as temp_dir:
            image_path = Path(temp_dir) / "capture.jpg"
            result = subprocess.run(
                [
                    executable,
                    "--device", device,
                    "--resolution", "1280x720",
                    "--skip", "2",
                    "--no-banner",
                    str(image_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            if result.returncode != 0 or not image_path.is_file() or image_path.stat().st_size == 0:
                raise RuntimeError(
                    f"Không chụp được từ {device}. Kiểm tra webcam và quyền truy cập nhóm video trên Pi."
                )
            image_bytes = image_path.read_bytes()
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Webcam Pi không phản hồi. Kiểm tra webcam có đang được ứng dụng khác sử dụng không.") from exc
    except OSError as exc:
        raise RuntimeError("Không khởi chạy được fswebcam trên Pi.") from exc

    return image_bytes


def read_mrz_key_live(image_bytes: bytes) -> str:
    """Thử nhanh MRZ trên một khung hình webcam đang chạy."""
    if not image_bytes:
        raise ValueError("Chưa có ảnh webcam.")
    for output in _ocr(image_bytes, rotations=(0,), fast=True):
        key = _key_from_text(output)
        if key:
            return key
    raise ValueError("MRZ chưa nhận diện được trong khung hình.")


def read_mrz_key_from_camera() -> str:
    """Chụp một ảnh từ webcam USB nối với Pi rồi nhận dạng MRZ."""
    return read_mrz_key(capture_camera_image())
