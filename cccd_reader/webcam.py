"""Live USB webcam preview and automatic MRZ recognition."""

from __future__ import annotations

import os
import pwd
import select
import shutil
import subprocess
import tempfile
import threading
import time

from .mrz_ocr import read_mrz_key_live


def _jpeg_frames(stream, stop_event: threading.Event):
    """Yield complete JPEGs and stop waiting if the device stops producing frames."""
    buffer = bytearray()
    last_frame = time.monotonic()
    while not stop_event.is_set():
        ready, _, _ = select.select([stream], [], [], 0.5)
        if not ready:
            if time.monotonic() - last_frame > 10:
                raise TimeoutError("Webcam không gửi hình trong 10 giây.")
            continue
        chunk = os.read(stream.fileno(), 65536)
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
            last_frame = time.monotonic()
            yield frame


class WebcamManager:
    MAX_SESSION_SECONDS = 120

    def __init__(self):
        self._condition = threading.Condition()
        self._frame: bytes | None = None
        self._frame_at: float | None = None
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
            return {**self._state, "frame_ready": self._frame is not None}

    def frame(self) -> bytes | None:
        with self._condition:
            return self._frame

    def frame_snapshot(self) -> tuple[bytes | None, float | None]:
        with self._condition:
            age = time.monotonic() - self._frame_at if self._frame_at is not None else None
            return self._frame, age

    def start(self, recognize_mrz: bool = True) -> tuple[bool, str]:
        if not shutil.which("ffmpeg"):
            return False, "Pi chưa cài ffmpeg. Chạy: sudo apt install ffmpeg"
        if recognize_mrz and not shutil.which("tesseract"):
            return False, "Pi chưa cài Tesseract OCR. Chạy: sudo apt install tesseract-ocr"

        device = os.environ.get("CCCD_CAMERA_DEVICE", "/dev/video0")
        if not os.path.exists(device):
            return False, f"Không tìm thấy webcam {device}. Kiểm tra dây USB hoặc đặt CCCD_CAMERA_DEVICE đúng thiết bị."
        if not os.access(device, os.R_OK | os.W_OK):
            return False, self._video_permission_message(device)

        with self._condition:
            if self._state["status"] == "camera_scanning":
                return False, "Camera Pi đang chạy."
            self._frame = None
            self._frame_at = None
            self._sequence = 0
            self._stop_event = threading.Event()
            self._state = {
                "status": "camera_scanning",
                "message": (
                    "Đang mở camera Pi để xác thực khuôn mặt."
                    if not recognize_mrz
                    else "Đang mở camera Pi. Camera tự tắt sau 2 phút."
                ),
                "mrz_key_info": None,
            }
            stop_event = self._stop_event

        threading.Thread(
            target=self._capture_loop,
            args=(stop_event, recognize_mrz),
            daemon=True,
            name="cccd-camera",
        ).start()
        if recognize_mrz:
            threading.Thread(
                target=self._recognize_loop,
                args=(stop_event,),
                daemon=True,
                name="cccd-mrz-ocr",
            ).start()
        if recognize_mrz:
            return True, "Đang bật camera Pi. Đưa mặt sau CCCD vào khung hình."
        return True, "Camera xác thực đã bật. Nhìn thẳng vào camera rồi bấm xác thực."

    def stop(self) -> None:
        with self._condition:
            if self._stop_event is not None:
                self._stop_event.set()
            process = self._process
            self._process = None
            self._frame = None
            self._frame_at = None
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
            self._frame_at = None
            self._state = {
                "status": "camera_error",
                "message": message,
                "mrz_key_info": None,
            }
            stop_event.set()
            self._condition.notify_all()
        self._terminate(process)

    def _capture_loop(self, stop_event: threading.Event, recognize_mrz: bool) -> None:
        deadline = time.monotonic() + self.MAX_SESSION_SECONDS
        device = os.environ.get("CCCD_CAMERA_DEVICE", "/dev/video0")
        process: subprocess.Popen | None = None
        try:
            executable = shutil.which("ffmpeg") or "ffmpeg"
            profiles = (
                (
                    "MJPEG 1280x720",
                    ["-input_format", "mjpeg", "-framerate", "30", "-video_size", "1280x720"],
                    ["-c:v", "copy"],
                ),
                ("chế độ tự động", [], ["-vf", "fps=5", "-c:v", "mjpeg", "-q:v", "5"]),
            )

            for attempt, (profile_name, input_options, output_options) in enumerate(profiles):
                if stop_event.is_set():
                    return
                frame_count = 0
                error_tail = ""
                stream_timeout = ""
                with tempfile.TemporaryFile() as error_log:
                    process = subprocess.Popen(
                        [
                            executable,
                            "-hide_banner",
                            "-loglevel", "error",
                            "-fflags", "+nobuffer",
                            "-f", "v4l2",
                            *input_options,
                            "-i", device,
                            "-an", "-sn", "-dn",
                            *output_options,
                            "-f", "image2pipe",
                            "-flush_packets", "1",
                            "pipe:1",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=error_log,
                        bufsize=0,
                    )
                    with self._condition:
                        if stop_event.is_set() or self._stop_event is not stop_event:
                            self._terminate(process)
                            return
                        self._process = process

                    try:
                        assert process.stdout is not None
                        for image in _jpeg_frames(process.stdout, stop_event):
                            if stop_event.is_set():
                                return
                            if time.monotonic() >= deadline:
                                with self._condition:
                                    if self._stop_event is stop_event:
                                        self._frame = None
                                        self._frame_at = None
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
                                self._frame_at = time.monotonic()
                                self._sequence += 1
                                frame_count += 1
                                self._state["message"] = (
                                    "Đưa mặt sau CCCD vào khung hình, giữ thẻ nằm ngang; Pi đang tự tìm MRZ…"
                                    if recognize_mrz
                                    else "Camera đang chờ khuôn mặt để xác thực…"
                                )
                                self._condition.notify_all()
                    except TimeoutError as exc:
                        stream_timeout = str(exc)
                    finally:
                        if process.stdout is not None:
                            process.stdout.close()
                        self._terminate(process)
                        with self._condition:
                            if self._process is process:
                                self._process = None

                    error_log.seek(0, os.SEEK_END)
                    log_size = error_log.tell()
                    error_log.seek(max(0, log_size - 2048))
                    error_tail = error_log.read().decode("utf-8", errors="replace").strip()
                    if stream_timeout:
                        error_tail = f"{stream_timeout} {error_tail}".strip()

                if stop_event.is_set():
                    return
                if frame_count:
                    self._set_camera_error(
                        stop_event,
                        self._camera_failure_message(device, error_tail, profile_name),
                    )
                    return
                if attempt == len(profiles) - 1:
                    self._set_camera_error(
                        stop_event,
                        self._camera_failure_message(device, error_tail, profile_name),
                    )
                    return
        except OSError as exc:
            self._set_camera_error(stop_event, f"Không mở được webcam {device}: {exc}")
        finally:
            with self._condition:
                if self._process is process:
                    self._process = None
            self._terminate(process)

    @staticmethod
    def _camera_failure_message(device: str, detail: str, profile_name: str) -> str:
        normalized = detail.lower()
        if "permission denied" in normalized or "operation not permitted" in normalized:
            return WebcamManager._video_permission_message(device)
        if detail:
            detail = " ".join(detail.split())
            return f"Không mở được webcam {device}: {detail}"
        return (
            f"Webcam {device} không phát hình ở cấu hình {profile_name}. Kiểm tra webcam có đang được ứng dụng khác dùng "
            "không và đặt CCCD_CAMERA_DEVICE đúng thiết bị."
        )

    @staticmethod
    def _video_permission_message(device: str) -> str:
        try:
            username = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError:
            username = "<tên-user>"
        return (
            f"Pi không có quyền truy cập webcam {device}. Chạy `sudo usermod -aG video {username}`, "
            "rồi khởi động lại Pi để web server nhận quyền mới."
        )

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
                self._frame_at = None
                self._state = {
                    "status": "camera_done",
                    "message": "Đã nhận diện MRZ. Mã đã được điền vào ô.",
                    "mrz_key_info": key,
                }
                stop_event.set()
                self._condition.notify_all()
            return
