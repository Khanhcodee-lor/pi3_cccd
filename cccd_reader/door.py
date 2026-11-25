"""Điều khiển relay mở khóa điện qua GPIO BCM trên Raspberry Pi."""

from __future__ import annotations

import os
import time


def unlock_door() -> None:
    pin_value = os.environ.get("CCCD_DOOR_GPIO_PIN")
    if not pin_value:
        raise RuntimeError("Chưa cấu hình relay. Đặt CCCD_DOOR_GPIO_PIN theo số chân BCM.")
    try:
        pin = int(pin_value)
        active_high = os.environ.get("CCCD_DOOR_RELAY_ACTIVE_HIGH", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        duration = float(os.environ.get("CCCD_DOOR_UNLOCK_SECONDS", "3"))
    except ValueError as exc:
        raise RuntimeError("Cấu hình chân relay hoặc thời gian mở cửa không hợp lệ.") from exc
    if not 0.1 <= duration <= 10:
        raise RuntimeError("CCCD_DOOR_UNLOCK_SECONDS phải nằm trong khoảng 0.1 đến 10 giây.")

    try:
        from gpiozero import OutputDevice
    except ImportError as exc:
        raise RuntimeError("Chưa cài gpiozero. Chạy pip install -r requirements.txt.") from exc

    try:
        with OutputDevice(pin, active_high=active_high, initial_value=False) as relay:
            relay.on()
            try:
                time.sleep(duration)
            finally:
                relay.off()
    except Exception as exc:
        raise RuntimeError(f"Không kích hoạt được relay GPIO BCM {pin}: {exc}") from exc
