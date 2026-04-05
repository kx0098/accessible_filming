from datetime import datetime
from pathlib import Path
from signal import pause
from time import sleep

from gpiozero import Button
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput

RECORD_BUTTON_GPIO = 17
RECORDINGS_DIR = Path(__file__).resolve().parent

class Recorder:
    def __init__(self):
        self.picam2 = Picamera2()
        video_config = self.picam2.create_video_configuration(
            main={"size": (1920, 1080)}
        )
        self.picam2.configure(video_config)
        self.picam2.start()
        sleep(1)

        self.encoder = H264Encoder(10_000_000)
        self.recording = False
        self.current_file = None
        print("[CAMERA] Camera ready")
        print(f"[GPIO] Record button on GPIO{RECORD_BUTTON_GPIO}")

    def toggle_recording(self):
        if not self.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file = RECORDINGS_DIR / f"recording_{timestamp}.mp4"

        self.picam2.start_recording(
            self.encoder,
            FfmpegOutput(str(self.current_file))
        )

        self.recording = True
        print(f"[REC] Started: {self.current_file}")

    def stop_recording(self):
        self.picam2.stop_recording()

        self.recording = False
        print(f"[REC] Stopped: {self.current_file}")
        self.current_file = None

    def close(self):
        print("[SHUTDOWN] Cleaning up camera")
        if self.recording:
            self.stop_recording()

        self.picam2.stop()
        print("[SHUTDOWN] Done")


def main():
    recorder = Recorder()
    record_button = Button(
        RECORD_BUTTON_GPIO,
        pull_up=True,
        bounce_time=0.2
    )

    record_button.when_pressed = recorder.toggle_recording

    print("[READY] Press the button once to start recording, again to stop.")
    try:
        pause()
    except KeyboardInterrupt:
        print("\n[RUN] KeyboardInterrupt received")
    finally:
        record_button.close()
        recorder.close()


if __name__ == "__main__":
    main()
