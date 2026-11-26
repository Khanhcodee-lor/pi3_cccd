"""Kết nối PC/SC với đầu đọc thẻ NFC."""

import hmac
import time

from smartcard.System import readers


def list_readers():
    return list(readers())


def read_card_uid(connection) -> str | None:
    """Đọc UID của thẻ bằng lệnh PC/SC thường dùng trên ACR122U."""
    try:
        data, sw1, sw2 = connection.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
    except Exception:
        return None
    if (sw1, sw2) != (0x90, 0x00) or not data:
        return None
    return bytes(data).hex().upper()


def wait_for_card_with_uid(reader, poll_interval: float = 0.3, ignored_uid: str | None = None):
    """Chờ thẻ; có thể bỏ qua thẻ vừa quét nếu nó còn đặt trên đầu đọc."""
    ignored_uid = ignored_uid.upper() if ignored_uid else None
    while True:
        connection = reader.createConnection()
        try:
            connection.connect()
            uid = read_card_uid(connection)
            if ignored_uid and uid and hmac.compare_digest(uid, ignored_uid):
                try:
                    connection.disconnect()
                except Exception:
                    pass
                time.sleep(poll_interval)
                continue
            return connection, uid
        except Exception:
            # Một lần kết nối thất bại cho biết thẻ cũ đã được lấy khỏi đầu đọc.
            ignored_uid = None
            time.sleep(poll_interval)


def wait_for_card(reader, poll_interval: float = 0.3):
    """Chờ đến khi thẻ được đặt lên đầu đọc rồi trả về kết nối."""
    connection, _uid = wait_for_card_with_uid(reader, poll_interval=poll_interval)
    return connection
