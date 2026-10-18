"""Luồng đọc thẻ dùng chung cho giao diện web và dòng lệnh."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .bac import perform_bac
from .icao import DG1_FID, DG2_FID, extract_photo, read_binary_file, select_emrtd_application
from .mrz import parse_mrz
from .reader import list_readers, wait_for_card

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
ProgressCallback = Callable[[str, str, int | None, int | None], None]


@dataclass(frozen=True)
class ScanResult:
    fields: dict[str, str]
    mrz_lines: list[str]
    photo_path: Path | None
    dg1_size: int
    dg2_size: int


def scan_card(mrz_key_info: str, progress: ProgressCallback | None = None) -> ScanResult:
    """Chờ thẻ, xác thực BAC, đọc DG1/DG2 và trả về dữ liệu đã tách."""

    def report(stage: str, message: str, done: int | None = None, total: int | None = None):
        if progress is not None:
            progress(stage, message, done, total)

    available_readers = list_readers()
    if not available_readers:
        raise RuntimeError("Không tìm thấy đầu đọc thẻ PC/SC.")

    reader = available_readers[0]
    connection = None
    try:
        report("waiting_for_card", f"Đầu đọc {reader}. Đặt CCCD lên đầu đọc và giữ yên.")
        connection = wait_for_card(reader)

        report("authenticating", "Đã nhận thẻ. Đang xác thực BAC…")
        select_emrtd_application(connection)
        secure_messaging = perform_bac(connection, mrz_key_info)

        report("reading_dg1", "Đang đọc thông tin MRZ trên thẻ…")
        dg1 = read_binary_file(
            secure_messaging,
            DG1_FID,
            progress=lambda done, total: report(
                "reading_dg1", "Đang đọc thông tin MRZ trên thẻ…", done, total
            ),
        )
        mrz_lines, fields = parse_mrz(dg1)

        report("reading_dg2", "Đang đọc ảnh chân dung…")
        dg2 = read_binary_file(
            secure_messaging,
            DG2_FID,
            progress=lambda done, total: report(
                "reading_dg2", "Đang đọc ảnh chân dung…", done, total
            ),
        )
        photo_path = extract_photo(dg2, OUTPUT_DIR)
        if photo_path is None:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUTPUT_DIR / "dg2.bin").write_bytes(dg2)

        report("done", "Đọc CCCD thành công.")
        return ScanResult(fields, mrz_lines, photo_path, len(dg1), len(dg2))
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass
