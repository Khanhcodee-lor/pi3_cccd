#!/usr/bin/env python3
"""
Test Live Stream & Nhận diện khuôn mặt với Raspberry Pi Camera v2.1 NoIR (Mạch đen).
Xem trực tiếp qua trình duyệt web tại http://<IP_CỦA_PI>:8080
"""

import io
import os
import sys
import time
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from threading import Thread, Lock
import cv2
import numpy as np

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

# Buffer lưu trữ khung hình và tuỳ chọn màu sắc
class StreamBuffer:
    def __init__(self):
        self.frame: bytes | None = None
        self.swap_rb: bool = True  # Mặc định đảo kênh R-B vì Picamera2 xuất RGB, OpenCV cần BGR
        self.red_gain: float = 0.85 # Tinh chỉnh hạ đỏ cho NoIR
        self.blue_gain: float = 1.05
        self.awb_mode: int = 0
        self.awb_changed: bool = False
        self.lock = Lock()

    def set_frame(self, frame_bytes: bytes):
        with self.lock:
            self.frame = frame_bytes

    def get_frame(self) -> bytes | None:
        with self.lock:
            return self.frame

    def toggle_color(self) -> bool:
        with self.lock:
            self.swap_rb = not self.swap_rb
            return self.swap_rb

    def set_color_tuning(self, red_gain: float | None = None, awb_mode: int | None = None):
        with self.lock:
            if red_gain is not None:
                self.red_gain = max(0.3, min(1.6, red_gain))
            if awb_mode is not None:
                self.awb_mode = awb_mode
                self.awb_changed = True

    def get_color_settings(self) -> tuple[bool, float, float, int]:
        with self.lock:
            return self.swap_rb, self.red_gain, self.blue_gain, self.awb_mode

    def pop_awb_change(self) -> int | None:
        with self.lock:
            if self.awb_changed:
                self.awb_changed = False
                return self.awb_mode
            return None

buffer = StreamBuffer()

HTML_PAGE = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <title>Pi Cam v2.1 NoIR - Live Stream</title>
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
        h2 { margin: 8px 0; color: #4CAF50; font-size: 22px; }
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
        .controls {
            margin: 16px auto;
            max-width: 640px;
            background: #1e1e1e;
            padding: 14px 18px;
            border-radius: 12px;
            border: 1px solid #333;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
            justify-content: center;
        }
        .control-group {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
        }
        select, input[type=range] {
            background: #2a2a2a;
            color: #fff;
            border: 1px solid #555;
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 14px;
        }
        input[type=range] {
            cursor: pointer;
            width: 120px;
        }
        .btn {
            background: #333;
            color: #fff;
            border: 1px solid #555;
            padding: 6px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }
        .btn:hover { background: #4CAF50; border-color: #4CAF50; }
        .status-bar {
            font-size: 14px;
            color: #aaa;
            margin-top: 10px;
        }
        .badge {
            display: inline-block;
            padding: 4px 10px;
            background: #2e7d32;
            color: #fff;
            border-radius: 20px;
            font-weight: bold;
            font-size: 12px;
            margin-bottom: 6px;
        }
        .info-noir {
            background: #291800;
            color: #ffb74d;
            border: 1px solid #ff9800;
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 13px;
            max-width: 640px;
            margin: 10px auto;
            text-align: left;
        }
    </style>
</head>
<body>
    <span class="badge">LIVE STREAM</span>
    <h2>Raspberry Pi Camera v2.1 (NoIR)</h2>
    <div class="stream-container">
        <img id="cameraView" alt="Đang tải video..." src="/frame.jpg" />
    </div>

    <div class="info-noir">
        💡 <b>Cam NoIR (Mạch đen)</b> không có kính lọc hồng ngoại để nhìn đêm. Đèn phòng phát ra tia hồng ngoại khiến camera bị đỏ. Bạn dùng thanh trượt dưới đây để hạ sắc đỏ hoặc chọn Cân bằng trắng phù hợp.
    </div>

    <div class="controls">
        <div class="control-group">
            <label>Cân bằng trắng (AWB):</label>
            <select id="awbSelect" onchange="updateSettings()">
                <option value="0" selected>Tự động (Auto NoIR)</option>
                <option value="3">Đèn huỳnh quang (Fluorescent)</option>
                <option value="5">Ánh sáng ngày (Daylight)</option>
                <option value="4">Trong nhà (Indoor)</option>
                <option value="1">Đèn sợi đốt (Tungsten)</option>
            </select>
        </div>

        <div class="control-group">
            <label>Sắc Đỏ:</label>
            <input type="range" id="redSlider" min="40" max="130" value="85" oninput="updateSettings()">
            <span id="redVal">85%</span>
        </div>

        <button class="btn" onclick="toggleColor()">🔄 Đổi kênh R/B</button>
        <button class="btn" onclick="presetAntiRed()">✨ Khử đỏ nhanh</button>
    </div>

    <div class="status-bar" id="statusText">Đang kết nối camera...</div>

    <script>
        const img = document.getElementById('cameraView');
        const status = document.getElementById('statusText');
        const redSlider = document.getElementById('redSlider');
        const redVal = document.getElementById('redVal');
        const awbSelect = document.getElementById('awbSelect');

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

        async function updateSettings() {
            const redGain = redSlider.value / 100.0;
            redVal.textContent = redSlider.value + '%';
            const awbMode = awbSelect.value;
            try {
                await fetch('/set-color?red=' + redGain + '&awb=' + awbMode, { method: 'POST' });
            } catch(e) {}
        }

        async function presetAntiRed() {
            redSlider.value = 75;
            redVal.textContent = '75%';
            awbSelect.value = '3';
            updateSettings();
        }

        async function toggleColor() {
            try {
                const res = await fetch('/toggle-color', { method: 'POST' });
                const data = await res.json();
                status.textContent = 'Đã đổi chế độ màu (Swap: ' + data.swap_rb + ')';
            } catch(e) {
                alert('Không thể đổi màu: ' + e);
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

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/toggle-color':
            is_swapped = buffer.toggle_color()
            resp = ('{"status": "ok", "swap_rb": %s}' % ('true' if is_swapped else 'false')).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        elif parsed.path == '/set-color':
            query = urllib.parse.parse_qs(parsed.query)
            red_val = float(query['red'][0]) if 'red' in query else None
            awb_val = int(query['awb'][0]) if 'awb' in query else None
            buffer.set_color_tuning(red_gain=red_val, awb_mode=awb_val)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '15')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_error(404)

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
            # Ưu tiên nạp file tuning chuẩn dành riêng cho camera NoIR (mạch đen)
            tuning_noir = "/usr/share/libcamera/ipa/rpi/vc4/imx219_noir.json"
            if os.path.isfile(tuning_noir):
                print(f"[+] Phát hiện camera NoIR, nạp file tuning tối ưu: {tuning_noir}")
                picam2 = Picamera2(tuning=tuning_noir)
            else:
                picam2 = Picamera2()

            config = picam2.create_video_configuration(main={"size": (640, 480), "format": "RGB888"})
            picam2.configure(config)
            picam2.start()

            # Tinh chỉnh ISP phần cứng
            try:
                controls = {}
                if "Contrast" in picam2.camera_controls:
                    controls["Contrast"] = 1.25  # Tăng độ tương phản để xóa mù sương
                if "Sharpness" in picam2.camera_controls:
                    controls["Sharpness"] = 1.6  # Tăng độ sắc nét chi tiết
                if "Saturation" in picam2.camera_controls:
                    controls["Saturation"] = 1.15 # Tăng độ tươi màu
                if controls:
                    picam2.set_controls(controls)
                    print(f"[+] Đã cấu hình ISP tối ưu chất lượng hình ảnh: {controls}")
            except Exception as e:
                print(f"[-] Không thể set camera controls: {e}")

            use_picam2 = True
            print("[+] Đang dùng Picamera2 chuẩn libcamera NoIR")
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
                new_awb = buffer.pop_awb_change()
                if new_awb is not None:
                    try:
                        picam2.set_controls({"AwbMode": new_awb})
                    except Exception:
                        pass
                frame_raw = picam2.capture_array()
            else:
                ret, frame_raw = cap.read()
                if not ret:
                    time.sleep(0.05)
                    continue

            swap_rb, red_gain, blue_gain, _ = buffer.get_color_settings()

            # Đổi kênh màu R/B nếu cần (OpenCV imencode cần BGR)
            if swap_rb:
                frame_bgr = frame_raw[:, :, ::-1].copy()
            else:
                frame_bgr = frame_raw.copy()

            # Tinh chỉnh cân bằng màu (Khử ám đỏ cho NoIR):
            if red_gain != 1.0 or blue_gain != 1.0:
                if red_gain != 1.0:
                    frame_bgr[:, :, 2] = np.clip(frame_bgr[:, :, 2].astype(np.float32) * red_gain, 0, 255).astype(np.uint8)
                if blue_gain != 1.0:
                    frame_bgr[:, :, 0] = np.clip(frame_bgr[:, :, 0].astype(np.float32) * blue_gain, 0, 255).astype(np.uint8)

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

            # Nén ảnh JPEG chất lượng cao
            ret, jpeg = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ret:
                buffer.set_frame(jpeg.tobytes())

        except Exception as exc:
            print(f"[!] Lỗi trong luồng camera: {exc}")
            time.sleep(0.1)

def main():
    ip = get_ip()
    port = 8080

    print("=" * 60)
    print("   RASPBERRY PI CAM V2.1 (NOIR) - LIVE FACE DETECTION STREAM   ")
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
