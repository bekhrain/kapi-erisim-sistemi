"""
Kamera yakalama soyutlamasi (OpenCV).

- Logitech C270 (USB) veya cogu USB kamera:  cv2.VideoCapture(0) ile calisir.
- Raspberry Pi Camera Module 3: en saglikli yol picamera2'dir. Bu modul once
  cv2'yi dener; Pi kamerasi cv2 ile acilamazsa picamera2'ye gecmek icin
  asagidaki _open_picamera2 yolu genisletilebilir.

Kareler QThread icinde okunur ve 'frame_ready(numpy.ndarray BGR)' sinyali ile
arayuze iletilir.
"""
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

try:
    import cv2
except ImportError:  # kamera olmadan da uygulama acilabilsin
    cv2 = None


class CameraWorker(QThread):
    """Arka planda kameradan kare okuyan is parcacigi."""
    frame_ready = pyqtSignal(np.ndarray)   # BGR kare
    error = pyqtSignal(str)

    def __init__(self, camera_index: int = 0, parent=None):
        super().__init__(parent)
        self.camera_index = camera_index
        self._running = False
        self._cap = None

    def run(self):
        if cv2 is None:
            self.error.emit("OpenCV (cv2) kurulu degil.")
            return

        self._cap = cv2.VideoCapture(self.camera_index)
        if not self._cap.isOpened():
            self.error.emit("Kamera acilamadi. Bagli mi?")
            return

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self._running = True
        while self._running:
            ok, frame = self._cap.read()
            if ok:
                self.frame_ready.emit(frame)
            self.msleep(30)   # ~30 FPS

        self._cap.release()

    def stop(self):
        self._running = False
        self.wait(1000)
