"""Luồng đọc thẻ dùng chung cho giao diện web và dòng lệnh."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .bac import perform_bac
from .icao import DG1_FID, DG2_FID, extract_photo, read_binary_file, select_emrtd_application
from .mrz import parse_mrz
from .reader import list_readers, wait_for_card_with_uid

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
    card_uid: str | None = None
    mrz_key_source: str = "provided"


class MrzRequiredError(RuntimeError):
    def __init__(self, message: str, *, uid_available: bool):
        super().__init__(message)
        self.uid_available = uid_available


def scan_card(
    mrz_key_info: str | None,
    progress: ProgressCallback | None = None,
    ignored_uid: str | None = None,
) -> ScanResult:
    """Tìm MRZ đã đăng ký nếu có, rồi xác thực BAC và đọc DG1/DG2."""

    def report(stage: str, message: str, done: int | None = None, total: int | None = None):
        if progress is not None:
            progress(stage, message, done, total)

    available_readers = list_readers()
    if not available_readers:
        raise RuntimeError("Không tìm thấy đầu đọc thẻ PC/SC.")

    reader = available_readers[0]
    connection = None
    try:
        report("waiting_for_card", f"Đầu đọc {reader}. Đưa CCCD vào khe và giữ yên.")
        connection, card_uid = wait_for_card_with_uid(reader, ignored_uid=ignored_uid)

        mrz_key_source = "provided"
        if not mrz_key_info:
            if not card_uid:
                raise MrzRequiredError(
                    "Đầu đọc chưa lấy được mã NFC của thẻ. Hãy đăng ký MRZ bằng ảnh điện thoại; "
                    "cần kiểm tra đầu đọc có hỗ trợ nhận diện UID để dùng lại tự động.",
                    uid_available=False,
                )
            from .firebase_store import find_cccd_mrz_by_uid

            try:
                registered_key = find_cccd_mrz_by_uid(card_uid)
            except Exception as exc:
                raise RuntimeError(f"Không tra được MRZ đã đăng ký trên Firebase: {exc}") from exc
            if not registered_key:
                raise MrzRequiredError(
                    "Thẻ này chưa đăng ký MRZ. Giữ CCCD trên đầu đọc, rồi dùng điện thoại chụp mặt sau để đăng ký một lần.",
                    uid_available=True,
                )
            mrz_key_info = registered_key
            mrz_key_source = "firebase"

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
        return ScanResult(
            fields,
            mrz_lines,
            photo_path,
            len(dg1),
            len(dg2),
            card_uid=card_uid,
            mrz_key_source=mrz_key_source,
        )
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass
