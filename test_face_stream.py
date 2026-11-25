#!/usr/bin/env python3
"""
Test Live Stream & Nhận diện khuôn mặt với Raspberry Pi Camera v2.
Xem trực tiếp qua trình duyệt web tại http://<IP_CỦA_PI>:8080
"""

import io
import os
import sys
import time
import socket
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from threading import Thread, Lock
import cv2

try:
    from picamera2 import Picamera2
    HAS_PICAMERA2 = True
except ImportError:
    HAS_PICAMERA2 = False

# Tìm hoặc tải file haarcascade_frontalface_default.xml
def get_cascade_path():
    script_dir = Path(__file__).resolve().parent
    local_file = script_dir / "haarcascade_frontalface_default.xml"
    if local_file.is_file():
        return str(local_file)

    system_paths = [
        "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
        "/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml",
        "/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    ]
    for p in system_paths:
        if os.path.isfile(p):
            return p

    if hasattr(cv2, 'data') and hasattr(cv2.data, 'haarcascades'):
        cv_data_path = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
        if os.path.isfile(cv_data_path):
            return cv_data_path

    url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
    print(f"[*] Đang tải mô hình nhận diện khuôn mặt về {local_file}...")
    try:
        urllib.request.urlretrieve(url, str(local_file))
        return str(local_file)
    except Exception as exc:
        print(f"[-] Không tải được mô hình: {exc}")
        return None

face_cascade = None
if hasattr(cv2, 'CascadeClassifier'):
    cascade_file = get_cascade_path()
    if cascade_file:
        print(f"[+] Sử dụng mô hình: {cascade_file}")
        face_cascade = cv2.CascadeClassifier(cascade_file)
        if face_cascade.empty():
            print("[-] CẢNH BÁO: Không thể nạp mô hình Haar Cascade vào OpenCV.")
            face_cascade = None
    else:
        print("[-] CẢNH BÁO: Không tìm thấy file haarcascade_frontalface_default.xml")
else:
    print("[!] Phiên bản OpenCV hiện tại (v5.x) không hỗ trợ CascadeClassifier, sẽ stream camera thuần.")

# Buffer lưu trữ khung hình mới nhất
class StreamBuffer:
    def __init__(self):
        self.frame: bytes | None = None
        self.lock = Lock()

    def set_frame(self, frame_bytes: bytes):
        with self.lock:
            self.frame = frame_bytes

    def get_frame(self) -> bytes | None:
        with self.lock:
            return self.frame

buffer = StreamBuffer()

HTML_PAGE = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <title>Pi Cam v2 - Nhận Diện Khuôn Mặt</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            text-align: center;
            background: #121212;
            color: #eee;
            margin: 0;
            padding: 16px;
        }
        h2 { margin: 10px 0; color: #4CAF50; font-size: 22px; }
        .stream-container {
            position: relative;
            display: inline-block;
            background: #1e1e1e;
            border: 2px solid #333;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 8px 24px rgba(0,0,0,0.6);
            max-width: 100%;
        }
        #cameraView {
            display: block;
            width: 100%;
            max-width: 640px;
            height: auto;
            aspect-ratio: 4 / 3;
            background: #000;
        }
        .status-bar {
            margin-top: 12px;
            font-size: 14px;
            color: #aaa;
        }
        .badge {
            display: inline-block;
            padding: 4px 10px;
            background: #2e7d32;
            color: #fff;
            border-radius: 20px;
            font-weight: bold;
            font-size: 12px;
            margin-bottom: 8px;
        }
    </style>
</head>
<body>
    <span class="badge">LIVE STREAM</span>
    <h2>Raspberry Pi Camera v2</h2>
    <div class="stream-container">
        <img id="cameraView" alt="Đang tải video..." src="/frame.jpg" />
    </div>
    <div class="status-bar" id="statusText">Đang kết nối camera...</div>

    <script>
        const img = document.getElementById('cameraView');
        const status = document.getElementById('statusText');
        let busy = false;
        let lastFrameUrl = null;
        let frameCount = 0;
        let fpsCounter = 0;
        let lastTime = performance.now();

        async function fetchFrame() {
            if (busy) return;
            busy = true;
            try {
                const response = await fetch('/frame.jpg?t=' + Date.now(), { cache: 'no-store' });
                if (response.ok) {
                    const blob = await response.blob();
                    const newUrl = URL.createObjectURL(blob);
                    const oldUrl = lastFrameUrl;
                    lastFrameUrl = newUrl;
                    img.onload = () => {
                        if (oldUrl) URL.revokeObjectURL(oldUrl);
                    };
                    img.src = newUrl;

                    frameCount++;
                    const now = performance.now();
                    if (now - lastTime >= 1000) {
                        fpsCounter = frameCount;
                        frameCount = 0;
                        lastTime = now;
                        status.textContent = 'Đang nhận luồng video (' + fpsCounter + ' FPS hiển thị)';
                    }
                }
            } catch (err) {
                status.textContent = 'Mất kết nối với camera, đang thử lại...';
            } finally {
                busy = false;
                setTimeout(fetchFrame, 40); // Lấy ~25 khung hình/giây
            }
        }

        fetchFrame();
    </script>
</body>
</html>
"""

class WebStreamHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/' or self.path.startswith('/?'):
            data = HTML_PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif self.path.startswith('/frame.jpg'):
            frame = buffer.get_frame()
            if frame is None:
                self.send_response(503)
                self.send_header('Retry-After', '1')
                self.end_headers()
                self.wfile.write(b"Camera warming up")
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(frame)))
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
                self.send_header('Pragma', 'no-cache')
                self.end_headers()
                self.wfile.write(frame)

        elif self.path.startswith('/stream.mjpg'):
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.end_headers()
            try:
                while True:
                    frame = buffer.get_frame()
                    if frame is not None:
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    time.sleep(0.04)
            except (ConnectionResetError, BrokenPipeError):
                pass
        else:
            self.send_error(404)

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def run_camera():
    print("[*] Đang khởi động camera...")
    use_picam2 = False

    if HAS_PICAMERA2:
        try:
            picam2 = Picamera2()
            # Dùng trực tiếp BGR888 chuẩn của OpenCV để màu sắc chuẩn xác nhất (không bị đảo kênh màu R/B)
            config = picam2.create_video_configuration(main={"size": (640, 480), "format": "BGR888"})
            picam2.configure(config)
            picam2.start()
            use_picam2 = True
            print("[+] Đang dùng Picamera2 định dạng BGR888 (màu sắc tự nhiên)")
        except Exception as e:
            print(f"[-] Picamera2 không khả dụng ({e}), thử VideoCapture...")

    if not use_picam2:
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        print("[+] Đang dùng OpenCV VideoCapture(0)")

    prev_time = time.time()
    fps_smooth = 0.0

    while True:
        try:
            if use_picam2:
                # capture_array() trả về mảng BGR nguyên bản từ camera
                frame_bgr = picam2.capture_array()
            else:
                ret, frame_bgr = cap.read()
                if not ret:
                    time.sleep(0.05)
                    continue

            # Nhận diện khuôn mặt nếu có mô hình
            face_count = 0
            if face_cascade is not None:
                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.15,
                    minNeighbors=4,
                    minSize=(40, 40)
                )
                face_count = len(faces)
                for (x, y, w, h) in faces:
                    cv2.rectangle(frame_bgr, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    cv2.putText(
                        frame_bgr,
                        "Face",
                        (x, y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 255, 0),
                        2
                    )

            # Tính FPS
            curr_time = time.time()
            dt = curr_time - prev_time
            prev_time = curr_time
            if dt > 0:
                current_fps = 1.0 / dt
                fps_smooth = 0.9 * fps_smooth + 0.1 * current_fps if fps_smooth > 0 else current_fps

            cv2.putText(
                frame_bgr,
                f"FPS: {fps_smooth:.1f} | Faces: {face_count}",
                (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 255),
                2
            )

            # Nén ảnh JPEG
            ret, jpeg = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ret:
                buffer.set_frame(jpeg.tobytes())

        except Exception as exc:
            print(f"[!] Lỗi trong luồng camera: {exc}")
            time.sleep(0.1)

def main():
    ip = get_ip()
    port = 8080

    print("=" * 60)
    print("      RASPBERRY PI CAM V2 - LIVE FACE DETECTION STREAM      ")
    print("=" * 60)
    print(f"[>] Mở trình duyệt trên máy tính/điện thoại và truy cập:")
    print(f"    👉 http://{ip}:{port}")
    print(f"    (Hoặc: http://localhost:{port} nếu mở ngay trên máy này)")
    print("=" * 60)

    # Chạy luồng đọc camera
    cam_thread = Thread(target=run_camera, daemon=True)
    cam_thread.start()

    # Chạy ThreadingHTTPServer (đa luồng)
    server = ThreadingHTTPServer(('0.0.0.0', port), WebStreamHandler)
    print(f"[*] Server đang lắng nghe tại port {port}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Đang tắt server...")
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
