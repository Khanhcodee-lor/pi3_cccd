"""Live USB webcam preview and automatic MRZ recognition."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
import os

from .mrz_ocr import read_mrz_key_live


def _jpeg_frames(stream, stop_event: threading.Event):
    """Yield complete JPEGs from fswebcam's continuous stdout stream."""
    buffer = bytearray()
    while not stop_event.is_set():
        chunk = stream.read(65536)
        if not chunk:
            return
        buffer.extend(chunk)

        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                # Keep one byte in case the next read completes an SOI marker.
                buffer[:] = buffer[-1:]
                break
            if start:
                del buffer[:start]
            end = buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(buffer) > 4 * 1024 * 1024:
                    buffer.clear()
                break
            frame = bytes(buffer[: end + 2])
            del buffer[: end + 2]
            yield frame


class WebcamManager:
    MAX_SESSION_SECONDS = 120

    def __init__(self):
        self._condition = threading.Condition()
        self._frame: bytes | None = None
        self._sequence = 0
        self._stop_event: threading.Event | None = None
        self._process: subprocess.Popen | None = None
        self._state = {
            "status": "camera_stopped",
            "message": "Camera Pi đang tắt.",
            "mrz_key_info": None,
        }

    def snapshot(self) -> dict:
        with self._condition:
            return dict(self._state)

    def frame(self) -> bytes | None:
        with self._condition:
            return self._frame

    def start(self) -> tuple[bool, str]:
        if not shutil.which("fswebcam"):
            return False, "Pi chưa cài fswebcam. Chạy: sudo apt install fswebcam"
        if not shutil.which("tesseract"):
            return False, "Pi chưa cài Tesseract OCR. Chạy: sudo apt install tesseract-ocr"

        with self._condition:
            if self._state["status"] == "camera_scanning":
                return False, "Camera Pi đang chạy."
            self._frame = None
            self._sequence = 0
            self._stop_event = threading.Event()
            self._state = {
                "status": "camera_scanning",
                "message": "Đang mở camera Pi. Camera tự tắt sau 2 phút.",
                "mrz_key_info": None,
            }
            stop_event = self._stop_event

        threading.Thread(target=self._capture_loop, args=(stop_event,), daemon=True, name="cccd-camera").start()
        threading.Thread(target=self._recognize_loop, args=(stop_event,), daemon=True, name="cccd-mrz-ocr").start()
        return True, "Đang bật camera Pi. Đưa mặt sau CCCD vào khung hình."

    def stop(self) -> None:
        with self._condition:
            if self._stop_event is not None:
                self._stop_event.set()
            process = self._process
            self._process = None
            self._frame = None
            self._state = {
                "status": "camera_stopped",
                "message": "Camera Pi đã tắt.",
                "mrz_key_info": None,
            }
            self._condition.notify_all()
        self._terminate(process)

    @staticmethod
    def _terminate(process: subprocess.Popen | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _set_camera_error(self, stop_event: threading.Event, message: str) -> None:
        with self._condition:
            if self._stop_event is not stop_event:
                return
            process = self._process
            self._process = None
            self._frame = None
            self._state = {
                "status": "camera_error",
                "message": message,
                "mrz_key_info": None,
            }
            stop_event.set()
            self._condition.notify_all()
        self._terminate(process)

    def _capture_loop(self, stop_event: threading.Event) -> None:
        deadline = time.monotonic() + self.MAX_SESSION_SECONDS
        device = os.environ.get("CCCD_CAMERA_DEVICE", "/dev/video0")
        process: subprocess.Popen | None = None
        try:
            process = subprocess.Popen(
                [
                    shutil.which("fswebcam") or "fswebcam",
                    "--device", device,
                    "--resolution", "640x480",
                    "--loop", "0.2",
                    "--no-banner",
                    "--jpeg", "75",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            with self._condition:
                if stop_event.is_set() or self._stop_event is not stop_event:
                    return
                self._process = process

            assert process.stdout is not None
            for image in _jpeg_frames(process.stdout, stop_event):
                if stop_event.is_set():
                    return
                if time.monotonic() >= deadline:
                    with self._condition:
                        if self._stop_event is stop_event:
                            self._frame = None
                            self._state = {
                                "status": "camera_stopped",
                                "message": "Camera tự tắt sau 2 phút. Bấm bật lại nếu cần quét tiếp.",
                                "mrz_key_info": None,
                            }
                            stop_event.set()
                            self._condition.notify_all()
                    return

                with self._condition:
                    if self._stop_event is not stop_event or stop_event.is_set():
                        return
                    self._frame = image
                    self._sequence += 1
                    self._state["message"] = "Đưa mặt sau CCCD vào khung hình, giữ thẻ nằm ngang; Pi đang tự tìm MRZ…"
                    self._condition.notify_all()

            if not stop_event.is_set():
                self._set_camera_error(
                    stop_event,
                    f"Luồng webcam từ {device} đã dừng. Kiểm tra webcam và quyền truy cập nhóm video trên Pi.",
                )
        except OSError as exc:
            self._set_camera_error(stop_event, f"Không mở được webcam {device}: {exc}")
        finally:
            with self._condition:
                if self._process is process:
                    self._process = None
            self._terminate(process)

    def _recognize_loop(self, stop_event: threading.Event) -> None:
        last_sequence = 0
        last_attempt = 0.0
        while not stop_event.is_set():
            with self._condition:
                self._condition.wait_for(
                    lambda: stop_event.is_set() or self._sequence > last_sequence,
                    timeout=0.5,
                )
                if stop_event.is_set():
                    return
                if self._sequence <= last_sequence or self._frame is None:
                    continue
                last_sequence = self._sequence
                image = self._frame

            now = time.monotonic()
            if now - last_attempt < 1.25:
                continue
            last_attempt = now
            try:
                key = read_mrz_key_live(image)
            except ValueError:
                continue
            except RuntimeError as exc:
                self._set_camera_error(stop_event, str(exc))
                return

            with self._condition:
                if self._stop_event is not stop_event or stop_event.is_set():
                    return
                self._frame = None
                self._state = {
                    "status": "camera_done",
                    "message": "Đã nhận diện MRZ. Mã đã được điền vào ô.",
                    "mrz_key_info": key,
                }
                stop_event.set()
                self._condition.notify_all()
            return
