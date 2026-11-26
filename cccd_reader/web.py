"""Web server cục bộ để bắt đầu quét và xem thông tin CCCD."""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import mimetypes
import os
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .scanner import ScanResult, scan_card
from .webcam import WebcamManager

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"
RUNNING_STATES = {
    "waiting_for_card",
    "authenticating",
    "reading_dg1",
    "reading_dg2",
    "saving_firebase",
    "verifying_face",
    "opening_door",
}


class ScanManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._photo_path: Path | None = None
        self._record_id: str | None = None
        self._scan_finished_at: float | None = None
        self._face_verifying = False
        self._state = {
            "status": "idle",
            "message": "Nhập MRZ key info rồi bắt đầu quét.",
            "progress": None,
            "data": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._state)

    def start(self, mrz_key_info: str) -> tuple[bool, str]:
        key = "".join(mrz_key_info.split()).upper()
        if len(key) != 24 or any(char not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<" for char in key):
            return False, "MRZ key info cần đúng 24 ký tự gồm chữ in hoa, số hoặc dấu <."

        with self._lock:
            if self._state["status"] in RUNNING_STATES:
                return False, "Đang xử lý một lượt. Hãy đợi lượt đó kết thúc."
            self._photo_path = None
            self._record_id = None
            self._scan_finished_at = None
            self._face_verifying = False
            self._state = {
                "status": "waiting_for_card",
                "message": "Đang chờ CCCD trên đầu đọc…",
                "progress": None,
                "data": None,
            }

        thread = threading.Thread(target=self._scan, args=(key,), daemon=True)
        thread.start()
        return True, "Đã bắt đầu quét."

    def clear(self) -> None:
        with self._lock:
            if self._state["status"] in RUNNING_STATES:
                return
            self._photo_path = None
            self._record_id = None
            self._scan_finished_at = None
            self._face_verifying = False
            self._state = {
                "status": "idle",
                "message": "Nhập MRZ key info rồi bắt đầu quét.",
                "progress": None,
                "data": None,
            }

    def photo(self) -> tuple[bytes, str] | None:
        with self._lock:
            path = self._photo_path
        if path is None or not path.is_file():
            return None
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return path.read_bytes(), content_type

    def _update(self, stage: str, message: str, done: int | None, total: int | None) -> None:
        if stage == "done":
            stage = "saving_firebase"
            message = "Đã đọc CCCD. Đang lưu thông tin lên Firebase…"
        progress = None
        if done is not None and total is not None:
            progress = {"done": done, "total": total, "percent": int(done * 100 / total) if total else 100}
        with self._lock:
            self._state.update({"status": stage, "message": message, "progress": progress})

    def _scan(self, key: str) -> None:
        try:
            result: ScanResult = scan_card(key, progress=self._update)
            from .firebase_store import save_cccd

            record_id = save_cccd(result.fields)
            photo_in_firebase = False
            photo_message = ""
            if result.photo_path is not None:
                try:
                    from .firebase_store import save_cccd_photo

                    save_cccd_photo(record_id, result.photo_path)
                    photo_in_firebase = True
                    photo_message = " Đã lưu cả ảnh chân dung lên Firebase Storage."
                except Exception as exc:
                    photo_message = f" Ảnh chân dung chưa được lưu lên Firebase: {exc}"
            else:
                photo_message = " Không trích xuất được ảnh DG2, chỉ lưu dữ liệu chữ."
            with self._lock:
                self._photo_path = result.photo_path
                self._record_id = record_id
                self._scan_finished_at = time.monotonic()
                self._state = {
                    "status": "done",
                    "message": "Đọc CCCD thành công và đã lưu dữ liệu lên Firebase." + photo_message,
                    "progress": None,
                    "data": result.fields,
                    "mrz_lines": result.mrz_lines,
                    "record_id": record_id,
                    "photo_in_firebase": photo_in_firebase,
                    "can_verify_face": result.photo_path is not None,
                }
        except Exception as exc:
            with self._lock:
                self._photo_path = None
                self._state = {
                    "status": "error",
                    "message": str(exc) or "Không đọc được thẻ.",
                    "progress": None,
                    "data": None,
                }

    def begin_face_verification(self) -> Path:
        with self._lock:
            if self._state["status"] == "door_opened":
                raise RuntimeError("Cửa đã mở cho CCCD này. Quét thẻ mới để bắt đầu lượt khác.")
            if self._face_verifying:
                raise RuntimeError("Đang xác thực khuôn mặt. Hãy đợi lượt đó kết thúc.")
            if self._state["status"] not in {"done", "face_mismatch", "face_error", "face_matched"}:
                raise RuntimeError("Hãy đọc và lưu CCCD lên Firebase trước khi xác thực khuôn mặt.")
            if not self._record_id:
                raise RuntimeError("Chưa có bản ghi CCCD đã lưu trên Firebase.")
            if self._photo_path is None:
                raise RuntimeError("Không có ảnh chân dung từ DG2 trên CCCD để đối chiếu.")
            if self._scan_finished_at is None or time.monotonic() - self._scan_finished_at > 300:
                raise RuntimeError("Lượt quét CCCD đã hết hạn. Hãy quét lại thẻ.")
            self._face_verifying = True
            self._state = {
                **self._state,
                "status": "verifying_face",
                "message": "Đang so khớp khuôn mặt với ảnh trên CCCD…",
                "progress": None,
            }
            return self._photo_path

    def finish_face_verification(
        self,
        *,
        status: str,
        message: str,
        score: float | None = None,
    ) -> None:
        with self._lock:
            self._face_verifying = False
            self._state = {
                **self._state,
                "status": status,
                "message": message,
                "progress": None,
                "face_score": score,
            }

    def set_face_opening(self, message: str, score: float) -> None:
        """Show a successful match while the servo/relay is being actuated."""
        with self._lock:
            self._state = {
                **self._state,
                "status": "opening_door",
                "message": message,
                "progress": None,
                "face_score": score,
            }


class WebHandler(BaseHTTPRequestHandler):
    server: "CCCDHTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        # Không ghi body, MRZ key hoặc dữ liệu CCCD vào log truy cập.
        super().log_message(format, *args)

    def _send(self, status: int, body: bytes, content_type: str, no_store: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store" if no_store else "public, max-age=60")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _authorized(self) -> bool:
        password = self.server.password
        if not password:
            return True
        supplied = self.headers.get("Authorization", "")
        if not supplied.startswith("Basic "):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="CCCD NFC", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        try:
            actual = base64.b64decode(supplied[6:], validate=True)
        except (ValueError, binascii.Error):
            actual = b""
        expected = f"cccd:{password}".encode("utf-8")
        if hmac.compare_digest(actual, expected):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="CCCD NFC", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self) -> None:
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path == "/":
            index = WEB_ROOT / "index.html"
            self._send(200, index.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._send_json(200, self.server.scan_manager.snapshot())
        elif path == "/api/history":
            cursor = parse_qs(urlparse(self.path).query).get("cursor", [""])[0] or None
            try:
                from .firebase_store import list_cccd_page

                self._send_json(200, list_cccd_page(cursor=cursor))
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:
                self._send_json(503, {"error": f"Không tải được lịch sử Firestore: {exc}"})
        elif path.startswith("/api/history/photo/"):
            record_id = path.removeprefix("/api/history/photo/")
            try:
                from .firebase_store import get_cccd_photo

                photo = get_cccd_photo(record_id)
                if photo is None:
                    self._send(404, b"No photo", "text/plain; charset=utf-8")
                else:
                    self._send(200, photo[0], photo[1])
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:
                self._send_json(503, {"error": f"Không tải được ảnh CCCD: {exc}"})
        elif path == "/api/photo":
            photo = self.server.scan_manager.photo()
            if photo is None:
                self._send(404, b"No photo", "text/plain; charset=utf-8")
            else:
                self._send(200, photo[0], photo[1])
        elif path == "/api/camera/status":
            self._send_json(200, self.server.webcam_manager.snapshot())
        elif path == "/api/camera/frame":
            frame = self.server.webcam_manager.frame()
            if frame is None:
                self._send(404, b"No camera frame", "text/plain; charset=utf-8")
            else:
                self._send(200, frame, "image/jpeg")
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if not self._authorized():
            return
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Content-Length không hợp lệ."})
            return
        if length < 0 or length > (8 * 1024 * 1024 if path == "/api/mrz-ocr" else 4096):
            self._send_json(413, {"error": "Yêu cầu quá lớn."})
            return
        if path == "/api/camera/start":
            if length:
                self.rfile.read(length)
            started, message = self.server.webcam_manager.start()
            if not started:
                self._send_json(409 if message.startswith("Camera Pi đang chạy") else 503, {"error": message})
                return
            self._send_json(202, {"message": message})
            return
        if path == "/api/face/camera/start":
            if length:
                self.rfile.read(length)
            started, message = self.server.webcam_manager.start(recognize_mrz=False)
            if not started:
                self._send_json(409 if message.startswith("Camera Pi đang chạy") else 503, {"error": message})
                return
            self._send_json(202, {"message": message})
            return
        if path == "/api/camera/stop":
            if length:
                self.rfile.read(length)
            self.server.webcam_manager.stop()
            self._send_json(200, {"message": "Camera Pi đã tắt."})
            return
        if path == "/api/face/camera/stop":
            if length:
                self.rfile.read(length)
            self.server.webcam_manager.stop()
            self._send_json(200, {"message": "Camera xác thực đã tắt."})
            return
        if path == "/api/mrz-ocr":
            if length == 0:
                self._send_json(400, {"error": "Chưa nhận được ảnh."})
                return
            image_bytes = self.rfile.read(length)
            try:
                from .mrz_ocr import read_mrz_key

                key = read_mrz_key(image_bytes)
            except RuntimeError as exc:
                self._send_json(503, {"error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(422, {"error": str(exc)})
                return
            self._send_json(200, {"mrz_key_info": key, "message": "Đã nhận diện MRZ. Kiểm tra mã rồi bấm Quét thẻ."})
            return
        if path == "/api/mrz-camera":
            if length:
                self.rfile.read(length)
                self._send_json(400, {"error": "Endpoint webcam không nhận nội dung gửi lên."})
                return
            try:
                from .mrz_ocr import read_mrz_key_from_camera

                key = read_mrz_key_from_camera()
            except RuntimeError as exc:
                self._send_json(503, {"error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(422, {"error": str(exc)})
                return
            self._send_json(200, {"mrz_key_info": key, "message": "Đã đọc MRZ bằng webcam Pi. Kiểm tra mã rồi bấm Quét thẻ."})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "JSON không hợp lệ."})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "Dữ liệu gửi lên không hợp lệ."})
            return

        if path == "/api/scan":
            value = payload.get("mrz_key_info", "")
            if not isinstance(value, str):
                self._send_json(400, {"error": "MRZ key info không hợp lệ."})
                return
            try:
                from .firebase_store import ensure_firebase_ready

                ensure_firebase_ready()
            except RuntimeError as exc:
                self._send_json(503, {"error": str(exc)})
                return
            started, message = self.server.scan_manager.start(value)
            if not started:
                self._send_json(409 if message.startswith("Đang xử lý") else 400, {"error": message})
                return
            self._send_json(202, {"message": message})
        elif path == "/api/face/verify":
            photo_path = None
            score = None
            try:
                photo_path = self.server.scan_manager.begin_face_verification()
                frame, age = self.server.webcam_manager.frame_snapshot()
                if frame is None or age is None or age > 2.0:
                    raise RuntimeError("Chưa có khung hình webcam mới. Bật camera xác thực và thử lại.")

                from .face_verification import compare_faces

                try:
                    threshold = float(os.environ.get("CCCD_FACE_THRESHOLD", "0.363"))
                except ValueError as exc:
                    raise RuntimeError("CCCD_FACE_THRESHOLD cần là số từ 0 đến 1.") from exc
                if not 0 < threshold <= 1:
                    raise RuntimeError("CCCD_FACE_THRESHOLD cần nằm trong khoảng lớn hơn 0 đến 1.")

                score, matched = compare_faces(photo_path, frame, threshold)
                if not matched:
                    message = f"Khuôn mặt chưa khớp với ảnh CCCD (điểm {score:.3f}). Hãy thử lại."
                    self.server.scan_manager.finish_face_verification(
                        status="face_mismatch", message=message, score=score
                    )
                    self._send_json(200, {"matched": False, "unlocked": False, "score": score, "message": message})
                    return

                match_message = f"Khuôn mặt đã khớp (điểm {score:.3f})."
                self.server.scan_manager.set_face_opening(f"{match_message} Đang điều khiển servo…", score)
                try:
                    from .door import unlock_door

                    actuator = unlock_door()
                except Exception as exc:
                    message = f"{match_message} Chưa mở được cửa: {exc}"
                    self.server.scan_manager.finish_face_verification(
                        status="face_matched", message=message, score=score
                    )
                    self._send_json(
                        200,
                        {
                            "matched": True,
                            "unlocked": False,
                            "status": "face_matched",
                            "score": score,
                            "message": message,
                        },
                    )
                    return

                message = f"{match_message} Đã điều khiển {actuator} mở cửa."
                self.server.scan_manager.finish_face_verification(
                    status="door_opened", message=message, score=score
                )
                self._send_json(
                    200,
                    {
                        "matched": True,
                        "unlocked": True,
                        "status": "door_opened",
                        "score": score,
                        "message": message,
                    },
                )
            except RuntimeError as exc:
                message = str(exc) or "Không xác thực được khuôn mặt."
                if photo_path is not None:
                    self.server.scan_manager.finish_face_verification(
                        status="face_error", message=message, score=score
                    )
                self._send_json(503, {"matched": False, "unlocked": False, "score": score, "error": message})
            except Exception as exc:
                message = f"Lỗi xác thực/mở cửa: {exc}"
                if photo_path is not None:
                    self.server.scan_manager.finish_face_verification(
                        status="face_error", message=message, score=score
                    )
                self._send_json(503, {"matched": score is not None, "unlocked": False, "score": score, "error": message})
        elif path == "/api/clear":
            self.server.scan_manager.clear()
            self._send_json(200, {"message": "Đã xóa thông tin khỏi trang."})
        else:
            self._send_json(404, {"error": "Not found"})


class CCCDHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, password: str | None = None):
        self.scan_manager = ScanManager()
        self.webcam_manager = WebcamManager()
        self.password = password
        super().__init__(address, handler)


def _preload_face_models() -> None:
    try:
        from .face_verification import preload_face_models

        preload_face_models()
        print("Model nhận diện khuôn mặt đã sẵn sàng.", flush=True)
    except Exception as exc:
        print(f"Chưa nạp được model khuôn mặt lúc khởi động: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Web server đọc CCCD qua đầu đọc NFC trên Pi")
    parser.add_argument("--host", default="127.0.0.1", help="Mặc định chỉ truy cập ngay trên Pi")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    password = os.environ.get("CCCD_WEB_PASSWORD")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not password:
        parser.error("Đặt CCCD_WEB_PASSWORD trước khi mở server ra mạng LAN.")

    server = CCCDHTTPServer((args.host, args.port), WebHandler, password=password)
    threading.Thread(
        target=_preload_face_models,
        name="face-model-preload",
        daemon=True,
    ).start()
    print(f"Web server đang chạy tại http://{args.host}:{args.port}")
    print("Nhấn Ctrl+C để dừng.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang dừng web server…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
