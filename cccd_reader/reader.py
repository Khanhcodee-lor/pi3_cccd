"""Kết nối PC/SC với đầu đọc thẻ NFC."""

import time

from smartcard.System import readers


def list_readers():
    return list(readers())


def wait_for_card(reader, poll_interval: float = 0.3):
    """Chờ đến khi thẻ được đặt lên đầu đọc rồi trả về kết nối."""
    while True:
        connection = reader.createConnection()
        try:
            connection.connect()
            return connection
        except Exception:
            time.sleep(poll_interval)
