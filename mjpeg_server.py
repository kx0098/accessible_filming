#!/usr/bin/env python3
"""Unified live stream + record button app for Raspberry Pi Camera Module 3."""

from __future__ import annotations

import io
import socket
import socketserver
from datetime import datetime
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
RECORDINGS_DIR = Path("recordings")

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


class Recorder:
    def __init__(self, camera) -> None:
        self.picam2 = camera
        self.h264_encoder = H264Encoder(10_000_000)
        self.recording = False
        self.current_file = None
        print(f"[GPIO] Record button on GPIO{RECORD_PIN}")

    def toggle_recording(self) -> None:
        if not self.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file = RECORDINGS_DIR / f"recording_{timestamp}.mp4"

        self.h264_encoder.output = FfmpegOutput(str(self.current_file))
        self.picam2.start_encoder(self.h264_encoder)
        self.recording = True
        print(f"[REC] Started: {self.current_file.name}")

    def stop_recording(self) -> None:
        self.picam2.stop_encoder(self.h264_encoder)
        self.recording = False
        print(f"[REC] Stopped: {self.current_file.name}")
        self.current_file = None


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    global output, picam2

    picam2_instance = None
    record_button = None
    server_instance = None
    recorder_instance = None

    try:
        # Create and configure camera once
        picam2_instance = Picamera2()
        picam2 = picam2_instance
        config = picam2_instance.create_video_configuration(main={"size": (WIDTH, HEIGHT)})
        picam2_instance.configure(config)
        picam2_instance.start()

        # Start MJPEG stream
        output_instance = StreamingOutput()
        output = output_instance
        mjpeg_encoder = MJPEGEncoder()
        picam2_instance.start_recording(mjpeg_encoder, FileOutput(output_instance))

        # Wire record button
        recorder_instance = Recorder(picam2_instance)
        record_button = Button(RECORD_PIN, pull_up=True, bounce_time=0.2)
        record_button.when_pressed = recorder_instance.toggle_recording

        # Print connection info
        address = ("0.0.0.0", PORT)
        ip_address = get_local_ip()
        print("[CAMERA] Live stream started")
        print(f"[URL] http://{ip_address}:{PORT}/")
        print("[READY] Press the button to start/stop recording")

        # Start HTTP server
        server_instance = StreamingServer(address, StreamingHandler)
        try:
            server_instance.serve_forever()
        except KeyboardInterrupt:
            print("\n[RUN] KeyboardInterrupt received")
    finally:
        if record_button is not None:
            try:
                record_button.close()
            except Exception:
                pass
        if server_instance is not None:
            try:
                server_instance.shutdown()
                server_instance.server_close()
            except Exception:
                pass
        if recorder_instance is not None and recorder_instance.recording:
            try:
                recorder_instance.stop_recording()
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
    main()