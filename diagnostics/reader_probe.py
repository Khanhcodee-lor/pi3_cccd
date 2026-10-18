#!/usr/bin/env python3
"""Kiểm tra đầu đọc và khả năng giao tiếp cơ bản với thẻ CCCD."""

from smartcard.util import toHexString

from cccd_reader.icao import select_emrtd_application
from cccd_reader.reader import list_readers, wait_for_card


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
        print("SELECT ứng dụng eMRTD: thành công")

        data, sw1, sw2 = connection.transmit([0x00, 0x84, 0x00, 0x00, 0x08])
        if (sw1, sw2) != (0x90, 0x00) or len(data) != 8:
            raise RuntimeError(f"GET CHALLENGE thất bại: SW={sw1:02X}{sw2:02X}")
        print("GET CHALLENGE: thành công; thẻ phản hồi 8 byte")
        print(
            "Đầu đọc và thẻ giao tiếp được. Bài kiểm tra chưa xác thực BAC "
            "hay đọc dữ liệu cá nhân."
        )
        return 0
    except KeyboardInterrupt:
        print("\nĐã dừng theo yêu cầu.")
        return 130
    except Exception as exc:
        print(f"Kiểm tra thất bại: {exc}")
        return 1
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
