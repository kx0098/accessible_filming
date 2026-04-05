#!/usr/bin/env python3
"""Accessible filming controller for Raspberry Pi 5 + Camera Module 3.

Buttons are wired from GPIO pins to GND and use internal pull-ups.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from signal import pause

from gpiozero import Button
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput


class Mode(Enum):
    DEFAULT = "default"
    BRIGHTNESS = "brightness"
    ZOOM = "zoom"


class AccessibleFilmingController:
    RECORD_PIN = 17
    BRIGHTNESS_MODE_PIN = 27
    ZOOM_MODE_PIN = 22
    UP_PIN = 23
    DOWN_PIN = 24

    BRIGHTNESS_STEP = 0.05
    BRIGHTNESS_MIN = -1.0
    BRIGHTNESS_MAX = 1.0

    ZOOM_STEP = 0.1
    ZOOM_MIN = 1.0
    ZOOM_MAX = 4.0

    def __init__(self) -> None:
        self.mode = Mode.DEFAULT
        self.is_recording = False
        self.recordings_dir = Path("recordings")
        self.recordings_dir.mkdir(exist_ok=True)

        self.brightness = 0.0
        self.zoom_factor = 1.0

        self.picam2 = Picamera2()
        self.encoder = H264Encoder()
        self._setup_camera()

        self.record_button = Button(self.RECORD_PIN, pull_up=True, bounce_time=0.08)
        self.brightness_button = Button(self.BRIGHTNESS_MODE_PIN, pull_up=True, bounce_time=0.08)
        self.zoom_button = Button(self.ZOOM_MODE_PIN, pull_up=True, bounce_time=0.08)
        self.up_button = Button(
            self.UP_PIN,
            pull_up=True,
            bounce_time=0.03,
            hold_time=0.25,
            hold_repeat=True,
        )
        self.down_button = Button(
            self.DOWN_PIN,
            pull_up=True,
            bounce_time=0.03,
            hold_time=0.25,
            hold_repeat=True,
        )

        self._wire_callbacks()
        self._debug_pin_map()
        print("[INIT] Controller ready. Waiting for button input...")

    def _setup_camera(self) -> None:
        video_config = self.picam2.create_video_configuration(main={"size": (1920, 1080)})
        self.picam2.configure(video_config)
        self.picam2.start()

        self.picam2.set_controls({"Brightness": self.brightness})
        self.scaler_crop_max = self._resolve_scaler_crop_max()
        self._apply_zoom()

        print("[CAMERA] Camera initialized and started")
        print(f"[CAMERA] ScalerCrop max: {self.scaler_crop_max}")

    def _resolve_scaler_crop_max(self) -> tuple[int, int, int, int]:
        crop = self.picam2.camera_properties.get("ScalerCropMaximum")
        if crop is None:
            # Conservative fallback for compatibility if property is missing.
            return (0, 0, 4608, 2592)
        return tuple(int(value) for value in crop)

    def _wire_callbacks(self) -> None:
        self.record_button.when_pressed = self.toggle_recording
        self.brightness_button.when_pressed = self.toggle_brightness_mode
        self.zoom_button.when_pressed = self.toggle_zoom_mode

        self.up_button.when_pressed = self.handle_up
        self.up_button.when_held = self.handle_up
        self.down_button.when_pressed = self.handle_down
        self.down_button.when_held = self.handle_down

    def _debug_pin_map(self) -> None:
        print("[GPIO] Button mapping:")
        print(f"[GPIO] Record: GPIO{self.RECORD_PIN}")
        print(f"[GPIO] Brightness mode: GPIO{self.BRIGHTNESS_MODE_PIN}")
        print(f"[GPIO] Zoom mode: GPIO{self.ZOOM_MODE_PIN}")
        print(f"[GPIO] Up: GPIO{self.UP_PIN}")
        print(f"[GPIO] Down: GPIO{self.DOWN_PIN}")

    def run(self) -> None:
        print("[RUN] Event loop started. Press Ctrl+C to exit.")
        pause()

    def toggle_recording(self) -> None:
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        filename = self._build_recording_filename()
        file_output = FileOutput(str(filename))
        self.picam2.start_recording(self.encoder, file_output)
        self.is_recording = True
        print(f"[REC] Started recording: {filename}")

    def stop_recording(self) -> None:
        self.picam2.stop_recording()
        self.is_recording = False
        print("[REC] Stopped recording")

    def _build_recording_filename(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.recordings_dir / f"recording_{timestamp}.h264"

    def toggle_brightness_mode(self) -> None:
        if self.mode == Mode.BRIGHTNESS:
            self.set_mode(Mode.DEFAULT)
        else:
            self.set_mode(Mode.BRIGHTNESS)

    def toggle_zoom_mode(self) -> None:
        if self.mode == Mode.ZOOM:
            self.set_mode(Mode.DEFAULT)
        else:
            self.set_mode(Mode.ZOOM)

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        print(f"[MODE] Switched to: {self.mode.value}")

    def handle_up(self) -> None:
        if self.mode == Mode.BRIGHTNESS:
            self.adjust_brightness(self.BRIGHTNESS_STEP)
        elif self.mode == Mode.ZOOM:
            self.adjust_zoom(self.ZOOM_STEP)
        else:
            self.default_up_action()

    def handle_down(self) -> None:
        if self.mode == Mode.BRIGHTNESS:
            self.adjust_brightness(-self.BRIGHTNESS_STEP)
        elif self.mode == Mode.ZOOM:
            self.adjust_zoom(-self.ZOOM_STEP)
        else:
            self.default_down_action()

    def default_up_action(self) -> None:
        # Replace this with robotic arm upward movement control.
        print("[ARM] Default up action triggered")

    def default_down_action(self) -> None:
        # Replace this with robotic arm downward movement control.
        print("[ARM] Default down action triggered")

    def adjust_brightness(self, delta: float) -> None:
        new_value = self._clamp(self.brightness + delta, self.BRIGHTNESS_MIN, self.BRIGHTNESS_MAX)
        if new_value == self.brightness:
            return

        self.brightness = new_value
        self.picam2.set_controls({"Brightness": self.brightness})
        print(f"[BRIGHTNESS] Set to {self.brightness:.2f}")

    def adjust_zoom(self, delta: float) -> None:
        new_zoom = self._clamp(self.zoom_factor + delta, self.ZOOM_MIN, self.ZOOM_MAX)
        if new_zoom == self.zoom_factor:
            return

        self.zoom_factor = new_zoom
        self._apply_zoom()
        print(f"[ZOOM] Set to {self.zoom_factor:.2f}x")

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
        print("[SHUTDOWN] Cleaning up resources...")
        if self.is_recording:
            self.stop_recording()

        self.picam2.stop()
        self.record_button.close()
        self.brightness_button.close()
        self.zoom_button.close()
        self.up_button.close()
        self.down_button.close()
        print("[SHUTDOWN] Complete")


def main() -> None:
    controller = AccessibleFilmingController()
    try:
        controller.run()
    except KeyboardInterrupt:
        print("\n[RUN] KeyboardInterrupt received")
    finally:
        controller.close()


if __name__ == "__main__":
    main()
