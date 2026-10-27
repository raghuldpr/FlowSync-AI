"""Frame sources behind one interface: a phone stream (MJPEG/RTSP URL), a video file, or a camera index."""
import time

import cv2


class CvSource:
    """read() returns a BGR frame or None. Stream sources reconnect with a 1 s back-off; files simply end."""

    def __init__(self, target: str):
        self.target = int(target) if str(target).isdigit() else target
        self.is_file = isinstance(self.target, str) and not self.target.lower().startswith(("http://", "https://", "rtsp://"))
        self.cap = None
        self._last_try = 0.0
        self._open()

    def _open(self):
        self._last_try = time.monotonic()
        self.cap = cv2.VideoCapture(self.target)
        if self.cap is not None and self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)          # low latency: don't queue old frames

    def read(self):
        if self.cap is None or not self.cap.isOpened():
            if not self.is_file and time.monotonic() - self._last_try >= 1.0:
                self._open()
            return None
        ok, frame = self.cap.read()
        if ok:
            return frame
        if self.is_file:
            return None
        self.cap.release()
        self.cap = None
        return None

    def release(self):
        if self.cap is not None:
            self.cap.release()
