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
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .scanner import ScanResult, scan_card

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"
RUNNING_STATES = {"waiting_for_card", "authenticating", "reading_dg1", "reading_dg2"}


class ScanManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._photo_path: Path | None = None
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
                return False, "Đang có một lượt quét chạy. Hãy đợi lượt đó kết thúc."
            self._photo_path = None
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
        progress = None
        if done is not None and total is not None:
            progress = {"done": done, "total": total, "percent": int(done * 100 / total) if total else 100}
        with self._lock:
            self._state.update({"status": stage, "message": message, "progress": progress})

    def _scan(self, key: str) -> None:
        try:
            result: ScanResult = scan_card(key, progress=self._update)
            with self._lock:
                self._photo_path = result.photo_path
                self._state = {
                    "status": "done",
                    "message": "Đọc CCCD thành công.",
                    "progress": None,
                    "data": result.fields,
                    "mrz_lines": result.mrz_lines,
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
        elif path == "/api/photo":
            photo = self.server.scan_manager.photo()
            if photo is None:
                self._send(404, b"No photo", "text/plain; charset=utf-8")
            else:
                self._send(200, photo[0], photo[1])
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
        if length < 0 or length > 4096:
            self._send_json(413, {"error": "Yêu cầu quá lớn."})
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
            started, message = self.server.scan_manager.start(value)
            if not started:
                self._send_json(409 if message.startswith("Đang có") else 400, {"error": message})
                return
            self._send_json(202, {"message": message})
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
        self.password = password
        super().__init__(address, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Web server đọc CCCD qua đầu đọc NFC trên Pi")
    parser.add_argument("--host", default="127.0.0.1", help="Mặc định chỉ truy cập ngay trên Pi")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    password = os.environ.get("CCCD_WEB_PASSWORD")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not password:
        parser.error("Đặt CCCD_WEB_PASSWORD trước khi mở server ra mạng LAN.")

    server = CCCDHTTPServer((args.host, args.port), WebHandler, password=password)
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
