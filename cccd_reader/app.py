"""Điểm chạy chính của ứng dụng đọc CCCD."""

from getpass import getpass
from pathlib import Path
import sys

from smartcard.util import toHexString

from .bac import perform_bac
from .icao import (
    DG1_FID,
    DG2_FID,
    extract_photo,
    read_binary_file,
    select_emrtd_application,
)
from .reader import list_readers, wait_for_card

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


def _read_mrz_key_info() -> str:
    while True:
        value = getpass("Nhập MRZ key info 24 ký tự (không hiện trên màn hình): ")
        value = "".join(value.split()).upper()
        if len(value) == 24 and all(
            char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<" for char in value
        ):
            return value
        print("MRZ key info cần đúng 24 ký tự ICAO.")


def _show_dg1(dg1: bytes) -> None:
    text = dg1.decode("ascii", errors="ignore")
    lines = [
        "".join(char for char in line if char.isalnum() or char == "<").strip()
        for line in text.splitlines()
    ]
    for line in lines:
        if len(line) >= 20:
            print(f"  {line}")


def _show_progress(done: int, total: int) -> None:
    percent = int(done * 100 / total) if total else 100
    print(f"\r  Đang đọc: {done}/{total} byte ({percent}%)", end="", flush=True)
    if done == total:
        print()


def main() -> int:
    try:
        available_readers = list_readers()
    except Exception as exc:
        print(f"Không truy cập được dịch vụ PC/SC: {exc}")
        return 1
    if not available_readers:
        print("Không tìm thấy đầu đọc thẻ PC/SC.")
        return 1

    reader = available_readers[0]
    print(f"Đầu đọc: {reader}")
    print("Đặt thẻ CCCD lên đầu đọc và giữ yên...")

    connection = None
    try:
        connection = wait_for_card(reader)
        print(f"Đã nhận thẻ. ATR: {toHexString(connection.getATR())}")
        select_emrtd_application(connection)

        mrz_key_info = _read_mrz_key_info()
        secure_messaging = perform_bac(connection, mrz_key_info)
        print("Xác thực BAC thành công.")

        dg1 = read_binary_file(secure_messaging, DG1_FID, progress=_show_progress)
        print(f"Đọc DG1 thành công ({len(dg1)} byte):")
        _show_dg1(dg1)

        dg2 = read_binary_file(secure_messaging, DG2_FID, progress=_show_progress)
        photo_path = extract_photo(dg2, OUTPUT_DIR)
        if photo_path is None:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUTPUT_DIR / "dg2.bin").write_bytes(dg2)
            print("Đã đọc DG2 nhưng chưa nhận diện được định dạng ảnh; lưu tại output/dg2.bin.")
        else:
            print(f"Đã trích xuất ảnh: {photo_path}")
        return 0
    except KeyboardInterrupt:
        print("\nĐã dừng theo yêu cầu.")
        return 130
    except Exception as exc:
        print(f"Lỗi đọc thẻ: {exc}")
        return 1
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
