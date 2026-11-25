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
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Lock
import cv2

try:
    from picamera2 import Picamera2
    HAS_PICAMERA2 = True
except ImportError:
    HAS_PICAMERA2 = False

# Tìm file haarcascade_frontalface_default.xml
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

    # Tự động tải nếu chưa có
    url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
    print(f"[*] Đang tải mô hình nhận diện khuôn mặt về {local_file}...")
    try:
        urllib.request.urlretrieve(url, str(local_file))
        return str(local_file)
    except Exception as exc:
        print(f"[-] Không tải được mô hình: {exc}")
        return None

cascade_file = get_cascade_path()
if not cascade_file:
    print("[-] LỖI: Không tìm thấy file haarcascade_frontalface_default.xml")
    sys.exit(1)

print(f"[+] Sử dụng mô hình: {cascade_file}")
face_cascade = cv2.CascadeClassifier(cascade_file)
if face_cascade.empty():
    print("[-] LỖI: Không thể tải mô hình Haar Cascade vào OpenCV.")
    sys.exit(1)

# Biến lưu trữ khung hình mới nhất để stream web
class StreamBuffer:
    def __init__(self):
        self.frame = None
        self.lock = Lock()

    def set_frame(self, frame_bytes):
        with self.lock:
            self.frame = frame_bytes

    def get_frame(self):
        with self.lock:
            return self.frame

buffer = StreamBuffer()

class WebStreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Pi Cam v2 - Live Face Detection</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { font-family: sans-serif; text-align: center; background: #1a1a1a; color: #fff; margin: 0; padding: 20px; }
                    h2 { margin-bottom: 10px; color: #4CAF50; }
                    .stream-box { display: inline-block; border: 3px solid #333; border-radius: 8px; overflow: hidden; max-width: 95vw; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
                    img { width: 100%; max-width: 640px; height: auto; display: block; }
                    p { color: #aaa; font-size: 14px; margin-top: 15px; }
                </style>
            </head>
            <body>
                <h2>Raspberry Pi Camera v2 - Live Face Detection</h2>
                <div class="stream-box">
                    <img src="/stream.mjpg" />
                </div>
                <p>Khung xanh: Khuôn mặt phát hiện | FPS & Số mặt hiển thị góc trái trên</p>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))

        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    frame = buffer.get_frame()
                    if frame is not None:
                        self.wfile.write(b'--FRAME\r\n')
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Content-Length', str(len(frame)))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b'\r\n')
                    time.sleep(0.04) # ~25 fps
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
            # Cấu hình 640x480 để Pi 3 xử lý mượt và ít nóng
            config = picam2.create_video_configuration(main={"size": (640, 480), "format": "RGB888"})
            picam2.configure(config)
            picam2.start()
            use_picam2 = True
            print("[+] Đang dùng thư viện Picamera2 (chuẩn Pi OS)")
        except Exception as e:
            print(f"[-] Không khởi tạo được Picamera2 ({e}), thử dùng OpenCV VideoCapture...")

    if not use_picam2:
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        print("[+] Đang dùng OpenCV VideoCapture(0)")

    prev_time = time.time()

    while True:
        if use_picam2:
            frame_rgb = picam2.capture_array()
            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        else:
            ret, frame_bgr = cap.read()
            if not ret:
                time.sleep(0.1)
                continue

        # Chuyển ảnh xám để nhận diện khuôn mặt
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.15,
            minNeighbors=4,
            minSize=(40, 40)
        )

        # Vẽ khung xanh quanh từng khuôn mặt
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

        # Tính FPS thực tế
        curr_time = time.time()
        fps = 1.0 / (curr_time - prev_time) if (curr_time - prev_time) > 0 else 0
        prev_time = curr_time

        # Vẽ thông tin FPS & số lượng mặt lên góc ảnh
        cv2.putText(
            frame_bgr,
            f"FPS: {fps:.1f} | Faces: {len(faces)}",
            (15, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2
        )

        # Nén thành JPEG để stream web
        ret, jpeg = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ret:
            buffer.set_frame(jpeg.tobytes())

def main():
    ip = get_ip()
    port = 8080

    print("=" * 60)
    print("      RASPBERRY PI CAM V2 - LIVE FACE DETECTION STREAM      ")
    print("=" * 60)
    print(f"[>] Mở trình duyệt trên máy tính/điện thoại và truy cập:")
    print(f"    👉 http://{ip}:{port}")
    print(f"    (Hoặc: http://localhost:{port} nếu mở ngay trên Pi)")
    print("=" * 60)
    print("[*] Đang khởi chạy web server...")

    # Chạy luồng đọc camera và xử lý AI
    cam_thread = Thread(target=run_camera, daemon=True)
    cam_thread.start()

    # Chạy Web Server stream
    server = HTTPServer(('0.0.0.0', port), WebStreamHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Đang tắt server...")
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
