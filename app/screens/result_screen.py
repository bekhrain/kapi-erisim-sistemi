"""
Sonuc ekrani: giris basarili/basarisiz geri bildirimi.
Birkac saniye sonra otomatik olarak ana menuye doner.

Fiziksel tepkiyi (selenoid kilit rolesi, buzzer, 3'lu trafik lambasi)
bu ekran DEGIL, main.py'deki MainWindow._show_result verir:
KapiDonanim.giris_onaylandi() / giris_reddedildi(). Donanimin tek
sahibi ana penceredir (bkz. app/hardware/kapi_donanim.py).
"""
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.style import GREEN, RED


class ResultScreen(QWidget):
    finished = pyqtSignal()   # ana menuye don

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(20)
        root.addStretch()

        self.icon = QLabel("")
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setStyleSheet("font-size: 130px;")
        root.addWidget(self.icon)

        self.text = QLabel("")
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setStyleSheet("font-size: 30px; font-weight: bold;")
        root.addWidget(self.text)

        self.detail = QLabel("")
        self.detail.setObjectName("hint")
        self.detail.setAlignment(Qt.AlignCenter)
        root.addWidget(self.detail)

        root.addStretch()

    def show_result(self, success: bool, message: str):
        if success:
            self.icon.setText("✅")
            self.text.setText("Giris Basarili")
            self.text.setStyleSheet(f"font-size: 30px; font-weight: bold; color: {GREEN};")
        else:
            self.icon.setText("⛔")
            self.text.setText("Giris Reddedildi")
            self.text.setStyleSheet(f"font-size: 30px; font-weight: bold; color: {RED};")

        self.detail.setText(message)
        # 3 saniye sonra ana menuye don
        QTimer.singleShot(3000, self.finished.emit)
