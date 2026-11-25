"""Điều khiển servo hoặc relay mở cửa qua GPIO BCM trên Raspberry Pi."""

from __future__ import annotations

import os
import time


def unlock_door() -> str:
    actuator = os.environ.get("CCCD_DOOR_ACTUATOR", "servo").strip().lower()
    if actuator not in {"servo", "relay"}:
        raise RuntimeError("CCCD_DOOR_ACTUATOR chỉ nhận `servo` hoặc `relay`.")

    pin_value = os.environ.get("CCCD_DOOR_GPIO_PIN")
    if not pin_value:
        raise RuntimeError(f"Chưa cấu hình {actuator}. Đặt CCCD_DOOR_GPIO_PIN theo số chân BCM.")
    try:
        pin = int(pin_value)
        duration = float(os.environ.get("CCCD_DOOR_UNLOCK_SECONDS", "3"))
    except ValueError as exc:
        raise RuntimeError("Cấu hình chân GPIO hoặc thời gian mở cửa không hợp lệ.") from exc
    if not 0.1 <= duration <= 10:
        raise RuntimeError("CCCD_DOOR_UNLOCK_SECONDS phải nằm trong khoảng 0.1 đến 10 giây.")

    if actuator == "servo":
        _move_servo(pin, duration)
        return "servo"

    active_high = os.environ.get("CCCD_DOOR_RELAY_ACTIVE_HIGH", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
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
    return "relay"


def _move_servo(pin: int, duration: float) -> None:
    try:
        closed_angle = float(os.environ.get("CCCD_DOOR_SERVO_CLOSED_ANGLE", "0"))
        open_angle = float(os.environ.get("CCCD_DOOR_SERVO_OPEN_ANGLE", "90"))
        min_angle = float(os.environ.get("CCCD_DOOR_SERVO_MIN_ANGLE", "-90"))
        max_angle = float(os.environ.get("CCCD_DOOR_SERVO_MAX_ANGLE", "90"))
        min_pulse_width = float(os.environ.get("CCCD_DOOR_SERVO_MIN_PULSE_WIDTH", "0.0005"))
        max_pulse_width = float(os.environ.get("CCCD_DOOR_SERVO_MAX_PULSE_WIDTH", "0.0025"))
    except ValueError as exc:
        raise RuntimeError("Cấu hình góc hoặc xung PWM của servo không hợp lệ.") from exc

    if min_angle >= max_angle or not min_angle <= closed_angle <= max_angle or not min_angle <= open_angle <= max_angle:
        raise RuntimeError("Góc đóng/mở servo phải nằm trong khoảng CCCD_DOOR_SERVO_MIN_ANGLE đến MAX_ANGLE.")
    if closed_angle == open_angle:
        raise RuntimeError("Góc đóng và góc mở servo phải khác nhau.")
    if min_pulse_width <= 0 or max_pulse_width <= min_pulse_width:
        raise RuntimeError("CCCD_DOOR_SERVO pulse width không hợp lệ.")

    try:
        from gpiozero import AngularServo
    except ImportError as exc:
        raise RuntimeError("Chưa cài gpiozero. Chạy pip install -r requirements.txt.") from exc

    try:
        with AngularServo(
            pin,
            initial_angle=closed_angle,
            min_angle=min_angle,
            max_angle=max_angle,
            min_pulse_width=min_pulse_width,
            max_pulse_width=max_pulse_width,
        ) as servo:
            servo.angle = open_angle
            time.sleep(duration)
            servo.angle = closed_angle
            time.sleep(0.5)
    except Exception as exc:
        raise RuntimeError(f"Không điều khiển được servo GPIO BCM {pin}: {exc}") from exc
