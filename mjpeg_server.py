#!/usr/bin/env python3
"""Unified live stream + record button app for Raspberry Pi Camera Module 3."""

from __future__ import annotations

import io
import socket
import socketserver
import subprocess
import sys
import time
from datetime import datetime
from enum import Enum
from http import server
from pathlib import Path
from threading import Condition

from gpiozero import Button
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, MJPEGEncoder
from picamera2.outputs import FileOutput, FfmpegOutput

PORT = 8000
WIDTH = 1280
HEIGHT = 720
RECORD_PIN = 17
BRIGHTNESS_MODE_PIN = 27
ZOOM_MODE_PIN = 22
UP_PIN = 23
DOWN_PIN = 24

RECORDINGS_DIR = Path("recordings")
BRIGHTNESS_STEP = 0.05
BRIGHTNESS_MIN = -1.0
BRIGHTNESS_MAX = 1.0
ZOOM_STEP = 0.1
ZOOM_MIN = 1.0
ZOOM_MAX = 4.0

output = None
picam2 = None

PAGE = f"""\
<html>
<head>
<title>Picamera2 Live Stream</title>
<style>
body {{ margin: 0; background: #111; color: #fff; font-family: sans-serif; }}
.wrap {{ display: grid; place-items: center; min-height: 100vh; }}
img {{ max-width: 100vw; max-height: 100vh; object-fit: contain; }}
</style>
</head>
<body>
<div class="wrap">
<img src="/stream.mjpg" width="{WIDTH}" height="{HEIGHT}" />
</div>
</body>
</html>
"""


class StreamingOutput(io.BufferedIOBase):
    def __init__(self) -> None:
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()
        return len(buf)


class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/index.html")
            self.end_headers()
        elif self.path == "/index.html":
            content = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", 0)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except Exception as exc:
                print(f"[STREAM] Client disconnected: {exc}")
        else:
            self.send_error(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class Mode(Enum):
    ARM = "arm"
    BRIGHTNESS = "brightness"
    ZOOM = "zoom"


class ButtonController:
    def __init__(self, camera) -> None:
        self.picam2 = camera
        self.h264_encoder = H264Encoder(10_000_000)

        self.mode = Mode.ARM
        self.recording = False
        self.current_file = None

        self.brightness = 0.0
        self.zoom_factor = 1.0
        self.scaler_crop_max = self._resolve_scaler_crop_max()

        self.picam2.set_controls({"Brightness": self.brightness})
        self._apply_zoom()

        self.record_button = Button(RECORD_PIN, pull_up=True, bounce_time=0.1)
        self.brightness_button = Button(BRIGHTNESS_MODE_PIN, pull_up=True, bounce_time=0.15)
        self.zoom_button = Button(ZOOM_MODE_PIN, pull_up=True, bounce_time=0.15)
        self.up_button = Button(
            UP_PIN,
            pull_up=True,
            bounce_time=0.05,
            hold_time=0.1,
            hold_repeat=True,
        )
        self.down_button = Button(
            DOWN_PIN,
            pull_up=True,
            bounce_time=0.05,
            hold_time=0.1,
            hold_repeat=True,
        )

        self._wire_callbacks()
        self._debug_pin_map()
        print(f"[MODE] Active mode: {self.mode.value}")

    def _wire_callbacks(self) -> None:
        self.record_button.when_pressed = self.handle_record_button
        self.record_button.when_released = lambda: self._debug_release(RECORD_PIN, "Record")
        self.brightness_button.when_pressed = self.handle_brightness_button
        self.brightness_button.when_released = lambda: self._debug_release(BRIGHTNESS_MODE_PIN, "Brightness mode")
        self.zoom_button.when_pressed = self.handle_zoom_button
        self.zoom_button.when_released = lambda: self._debug_release(ZOOM_MODE_PIN, "Zoom mode")

        self.up_button.when_pressed = self.handle_up
        self.up_button.when_held = self.handle_up
        self.up_button.when_released = lambda: self._debug_release(UP_PIN, "Up")
        self.down_button.when_pressed = self.handle_down
        self.down_button.when_held = self.handle_down
        self.down_button.when_released = lambda: self._debug_release(DOWN_PIN, "Down")

    def _debug_pin_map(self) -> None:
        print("[GPIO] Button mapping:")
        print(f"[GPIO] Record: GPIO{RECORD_PIN} (click-only, debounce=100ms)")
        print(f"[GPIO] Brightness mode: GPIO{BRIGHTNESS_MODE_PIN} (click-only, debounce=150ms)")
        print(f"[GPIO] Zoom mode: GPIO{ZOOM_MODE_PIN} (click-only, debounce=150ms)")
        print(f"[GPIO] Up: GPIO{UP_PIN} (hold-repeat, debounce=50ms, hold_time=100ms)")
        print(f"[GPIO] Down: GPIO{DOWN_PIN} (hold-repeat, debounce=50ms, hold_time=100ms)")
        print("[GPIO] === GPIO State Check (should be HIGH when released) ===")
        print(f"[GPIO] Record (GPIO{RECORD_PIN}): {self.record_button.value} (1=HIGH/released, 0=LOW/pressed)")
        print(f"[GPIO] Brightness (GPIO{BRIGHTNESS_MODE_PIN}): {self.brightness_button.value}")
        print(f"[GPIO] Zoom (GPIO{ZOOM_MODE_PIN}): {self.zoom_button.value}")
        print(f"[GPIO] Up (GPIO{UP_PIN}): {self.up_button.value}")
        print(f"[GPIO] Down (GPIO{DOWN_PIN}): {self.down_button.value}")

    def _debug_release(self, pin: int, name: str) -> None:
        print(f"[BTN] {name} (GPIO{pin}) released")

    def handle_record_button(self) -> None:
        timestamp = time.time()
        print(f"[BTN] Record (GPIO{RECORD_PIN}) PRESSED at {timestamp:.3f}")
        if not self.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def handle_brightness_button(self) -> None:
        timestamp = time.time()
        print(f"[BTN] Brightness mode (GPIO{BRIGHTNESS_MODE_PIN}) PRESSED at {timestamp:.3f}")
        if self.mode == Mode.BRIGHTNESS:
            self.set_mode(Mode.ARM)
        else:
            self.set_mode(Mode.BRIGHTNESS)

    def handle_zoom_button(self) -> None:
        timestamp = time.time()
        print(f"[BTN] Zoom mode (GPIO{ZOOM_MODE_PIN}) PRESSED at {timestamp:.3f}")
        if self.mode == Mode.ZOOM:
            self.set_mode(Mode.ARM)
        else:
            self.set_mode(Mode.ZOOM)

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        print(f"[MODE] Active mode: {self.mode.value}")

    def handle_up(self) -> None:
        timestamp = time.time()
        is_held = self.up_button.is_held if hasattr(self.up_button, 'is_held') else False
        event_type = "HELD" if is_held else "PRESSED"
        print(f"[BTN] Up (GPIO{UP_PIN}) {event_type} at {timestamp:.3f}")
        if self.mode == Mode.BRIGHTNESS:
            self.adjust_brightness(BRIGHTNESS_STEP)
        elif self.mode == Mode.ZOOM:
            self.adjust_zoom(ZOOM_STEP)
        else:
            self.arm_up()

    def handle_down(self) -> None:
        timestamp = time.time()
        is_held = self.down_button.is_held if hasattr(self.down_button, 'is_held') else False
        event_type = "HELD" if is_held else "PRESSED"
        print(f"[BTN] Down (GPIO{DOWN_PIN}) {event_type} at {timestamp:.3f}")
        if self.mode == Mode.BRIGHTNESS:
            self.adjust_brightness(-BRIGHTNESS_STEP)
        elif self.mode == Mode.ZOOM:
            self.adjust_zoom(-ZOOM_STEP)
        else:
            self.arm_down()

    def arm_up(self) -> None:
        # Replace with robotic arm control later.
        print("[ARM] ARM UP")

    def arm_down(self) -> None:
        # Replace with robotic arm control later.
        print("[ARM] ARM DOWN")

    def start_recording(self) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file = RECORDINGS_DIR / f"recording_{timestamp}.mp4"

        self.h264_encoder.output = FfmpegOutput(str(self.current_file))
        self.picam2.start_encoder(self.h264_encoder)
        self.recording = True
        print(f"[REC] Started: {self.current_file.name}")

    def stop_recording(self) -> None:
        if not self.recording:
            return

        self.picam2.stop_encoder(self.h264_encoder)
        self.recording = False
        print(f"[REC] Stopped: {self.current_file.name}")
        self.current_file = None

    def adjust_brightness(self, delta: float) -> None:
        new_value = self._clamp(self.brightness + delta, BRIGHTNESS_MIN, BRIGHTNESS_MAX)
        if new_value == self.brightness:
            return

        self.brightness = new_value
        self.picam2.set_controls({"Brightness": self.brightness})
        print(f"[BRIGHTNESS] Set to {self.brightness:.2f}")

    def adjust_zoom(self, delta: float) -> None:
        new_zoom = self._clamp(self.zoom_factor + delta, ZOOM_MIN, ZOOM_MAX)
        if new_zoom == self.zoom_factor:
            return

        self.zoom_factor = new_zoom
        self._apply_zoom()
        print(f"[ZOOM] Set to {self.zoom_factor:.2f}x")

    def _resolve_scaler_crop_max(self) -> tuple[int, int, int, int]:
        crop = self.picam2.camera_properties.get("ScalerCropMaximum")
        if crop is None:
            return (0, 0, 4608, 2592)
        return tuple(int(value) for value in crop)

    def _apply_zoom(self) -> None:
        max_x, max_y, max_w, max_h = self.scaler_crop_max

        crop_w = int(max_w / self.zoom_factor)
        crop_h = int(max_h / self.zoom_factor)
        crop_x = max_x + (max_w - crop_w) // 2
        crop_y = max_y + (max_h - crop_h) // 2

        self.picam2.set_controls({"ScalerCrop": (crop_x, crop_y, crop_w, crop_h)})

    @staticmethod
    def _clamp(value: float, min_value: float, max_value: float) -> float:
        return max(min_value, min(value, max_value))

    def close(self) -> None:
        if self.recording:
            self.stop_recording()

        self.record_button.close()
        self.brightness_button.close()
        self.zoom_button.close()
        self.up_button.close()
        self.down_button.close()


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def print_camera_lock_info() -> None:
    print("[ERROR] Camera is busy. Another process is using it.")
    try:
        result = subprocess.run(
            ["fuser", "-v", "/dev/media0", "/dev/media1", "/dev/media2", "/dev/media3", "/dev/video0"],
            check=False,
            capture_output=True,
            text=True,
        )
        output_text = (result.stdout + "\n" + result.stderr).strip()
        if output_text:
            print("[ERROR] Camera device users:")
            print(output_text)
    except Exception:
        print("[ERROR] Could not inspect camera lock holders with fuser.")

    print("[HINT] Stop other camera apps, then run this app again.")


def main() -> None:
    global output, picam2

    picam2_instance = None
    server_instance = None
    controller = None

    try:
        # Create and configure camera once
        try:
            picam2_instance = Picamera2()
        except RuntimeError as exc:
            print_camera_lock_info()
            print(f"[DETAIL] {exc}")
            return

        picam2 = picam2_instance
        config = picam2_instance.create_video_configuration(main={"size": (WIDTH, HEIGHT)})
        picam2_instance.configure(config)
        picam2_instance.start()

        # Wire all buttons and mode state machine (BEFORE starting encoder so controls are set)
        controller = ButtonController(picam2_instance)

        # Start MJPEG stream
        output_instance = StreamingOutput()
        output = output_instance
        mjpeg_encoder = MJPEGEncoder()
        picam2_instance.start_recording(mjpeg_encoder, FileOutput(output_instance))

        # Print connection info
        address = ("0.0.0.0", PORT)
        ip_address = get_local_ip()
        print("[INIT] Unified camera app started")
        print(f"[URL] http://{ip_address}:{PORT}/")
        print("[READY] Live stream active. Use buttons for record/mode control")

        # Start HTTP server
        server_instance = StreamingServer(address, StreamingHandler)
        try:
            server_instance.serve_forever()
        except KeyboardInterrupt:
            print("\n[RUN] KeyboardInterrupt received")
    finally:
        if server_instance is not None:
            try:
                server_instance.shutdown()
                server_instance.server_close()
            except Exception:
                pass
        if controller is not None:
            try:
                controller.close()
            except Exception:
                pass
        if picam2_instance is not None:
            try:
                picam2_instance.stop_recording()
            except Exception:
                pass
            try:
                picam2_instance.stop()
            except Exception:
                pass
        print("[SHUTDOWN] Complete")


if __name__ == "__main__":
    output = None
    picam2 = None
    try:
        main()
    except KeyboardInterrupt:
        print("\n[RUN] KeyboardInterrupt received")
    except Exception as exc:
        print(f"[ERROR] Unexpected failure: {exc}")
        sys.exit(1)