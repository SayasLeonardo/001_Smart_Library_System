"""Picamera2 capture helper. Frames are processed in memory and never recorded.

This is the ONLY module that opens the camera. app.py creates exactly one Camera
(in its camera worker thread) and shares the encoded JPEG frames with the
/video_feed route, so the stream and face recognition never fight over it.
"""

import sys
import time
import traceback

from picamera2 import Picamera2


def log_error(message):
    """Print to stderr immediately so errors show up in the terminal."""
    print(f"[camera] {message}", file=sys.stderr, flush=True)


class Camera:
    READ_FAILURE_LIMIT = 5
    RECONNECT_DELAY_SECONDS = 1.0

    def __init__(self, device_index=0, size=(640, 480), fps=15):
        self.device_index = device_index
        self.size = size
        self.fps = fps
        self.picam2 = None
        self._read_failures = 0
        self._next_reconnect_at = 0.0
        # Raises (after printing the real error) if the camera cannot be opened.
        self._open()

    def _open(self):
        picam2 = None
        try:
            picam2 = Picamera2(self.device_index)
            config = picam2.create_video_configuration(
                main={"size": self.size, "format": "RGB888"},  # RGB888 = BGR order in memory, what OpenCV expects
                buffer_count=4,
                controls={"FrameRate": self.fps},
            )
            picam2.configure(config)
            picam2.start()
        except Exception:
            log_error(f"Could not open camera {self.device_index}:")
            traceback.print_exc()
            if picam2 is not None:
                self._release(picam2)
            raise
        self.picam2 = picam2
        log_error(f"Camera {self.device_index} started at {self.size[0]}x{self.size[1]} @ {self.fps} fps")

    @staticmethod
    def _release(picam2):
        try:
            picam2.stop()
        except Exception:
            log_error("Error while stopping camera:")
            traceback.print_exc()
        try:
            picam2.close()
        except Exception:
            log_error("Error while closing camera:")
            traceback.print_exc()

    def read(self):
        """Return a BGR numpy frame, or None if no frame is available right now."""
        if self.picam2 is None:
            now = time.monotonic()
            if now < self._next_reconnect_at:
                return None
            try:
                self._open()
            except Exception:
                # _open() already printed the real error; retry after a delay.
                self._next_reconnect_at = now + self.RECONNECT_DELAY_SECONDS
                return None

        try:
            frame = self.picam2.capture_array("main")
        except Exception:
            self._read_failures += 1
            log_error(f"Frame capture failed ({self._read_failures}/{self.READ_FAILURE_LIMIT}):")
            traceback.print_exc()
            frame = None

        if frame is not None:
            self._read_failures = 0
            return frame

        if self._read_failures >= self.READ_FAILURE_LIMIT:
            log_error("Too many failed reads; releasing the camera to reconnect.")
            self._release(self.picam2)
            self.picam2 = None
            self._read_failures = 0
            self._next_reconnect_at = time.monotonic() + self.RECONNECT_DELAY_SECONDS
        return None

    def close(self):
        if self.picam2 is not None:
            self._release(self.picam2)
            self.picam2 = None
