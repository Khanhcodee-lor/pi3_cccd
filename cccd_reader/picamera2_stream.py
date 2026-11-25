"""Stream Raspberry Pi CSI camera JPEG frames to stdout for the web app."""

from __future__ import annotations

import os
import signal
import sys
import threading
import traceback


_STOP = threading.Event()


def _request_stop(_signum, _frame) -> None:
    _STOP.set()


def main() -> int:
    # Keep stdout exclusively for the JPEG byte stream. Picamera2 diagnostics
    # and any library prints must not corrupt the frames sent to the parent.
    jpeg_stream = sys.stdout.buffer
    sys.stdout = sys.stderr
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    camera = None
    recording = False
    try:
        from picamera2 import Picamera2
        from picamera2.encoders import JpegEncoder
        from picamera2.outputs import FileOutput

        tuning_noir = "/usr/share/libcamera/ipa/rpi/vc4/imx219_noir.json"
        camera = Picamera2(tuning=tuning_noir) if os.path.isfile(tuning_noir) else Picamera2()
        configuration = camera.create_video_configuration(
            main={"size": (1280, 720), "format": "RGB888"}
        )
        camera.configure(configuration)
        camera.start_recording(JpegEncoder(q=75), FileOutput(jpeg_stream))
        recording = True

        while not _STOP.wait(0.5):
            pass
        return 0
    except Exception as exc:
        print(f"Picamera2 không khởi động được: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        if camera is not None:
            if recording:
                try:
                    camera.stop_recording()
                except Exception as exc:
                    print(f"Không dừng được luồng Picamera2: {exc}", file=sys.stderr, flush=True)
            try:
                camera.close()
            except Exception as exc:
                print(f"Không đóng được Camera Pi: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
