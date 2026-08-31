"""
Admin giris ekrani: dokunmatik numpad ile yonetici PIN'i girilir.
Dogru PIN girilirse 'unlocked' sinyali yayilir ve admin paneli acilir.
"""
import secrets

from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from app import guvenlik, store
from app.config import ADMIN_PIN
from app.screens.widgets import Header


class AdminLogin(QWidget):
    back_requested = pyqtSignal()
    unlocked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pin = ""
        self._kilit_sayaci = QTimer(self)
        self._kilit_sayaci.setInterval(1000)
        self._kilit_sayaci.timeout.connect(self._kilit_tik)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 24, 40, 30)
        root.setSpacing(16)

        header = Header("Admin Girisi")
        header.back_clicked.connect(self.back_requested.emit)
        root.addWidget(header)

        self.display = QLabel("")
        self.display.setObjectName("pinDisplay")
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setFixedHeight(80)
        root.addWidget(self.display)

        self.status = QLabel("Yonetici PIN kodunu girin")
        self.status.setObjectName("hint")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        grid = QGridLayout()
        grid.setSpacing(12)
        keys = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
        ]
        for text, r, c in keys:
            grid.addWidget(self._digit(text), r, c)

        del_btn = QPushButton("⌫")
        del_btn.setObjectName("keyDel")
        del_btn.clicked.connect(self._backspace)
        grid.addWidget(del_btn, 3, 0)

        grid.addWidget(self._digit("0"), 3, 1)

        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("keyOk")
        ok_btn.clicked.connect(self._submit)
        grid.addWidget(ok_btn, 3, 2)

        root.addLayout(grid, stretch=1)

    def _digit(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("keypad")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._add(text))
        return btn

    def _add(self, digit: str):
        if guvenlik.admin_pin.kilitli_mi() or len(self._pin) >= 8:
            return
        self._pin += digit
        self._refresh()

    def _backspace(self):
        self._pin = self._pin[:-1]
        self._refresh()

    def _refresh(self):
        self.display.setText("●" * len(self._pin))

    def _submit(self):
        if guvenlik.admin_pin.kilitli_mi():
            self._kilit_goster()
            return

        # compare_digest: karsilastirma suresi girilen PIN'e gore
        # degismesin. Duz "==" ile ilk hatali haneden sonra donerdi ve
        # olculen sure hangi hanenin dogru oldugunu ele verebilirdi.
        if secrets.compare_digest(self._pin, ADMIN_PIN):
            guvenlik.admin_pin.basarili()
            store.log_access(True, "admin", detail="admin paneli acildi")
            self.unlocked.emit()
            self.reset()
            return

        kilitlendi = guvenlik.admin_pin.basarisiz()
        # Admin paneline yapilan yanlis denemeler de kayda gecer:
        # yetkisiz erisim girisimi web arayuzunde gorunur olsun.
        store.log_access(False, "admin", detail="hatali admin PIN")
        self._pin = ""
        self._refresh()
        if kilitlendi:
            self._kilit_goster()
        else:
            kalan = guvenlik.admin_pin.kalan_hak()
            self.status.setText(f"Hatali PIN ({kalan} deneme hakki kaldi)")

    # --- Kilit ---
    def _kilit_goster(self):
        self.status.setText(
            f"Kilitlendi. {guvenlik.admin_pin.kalan_saniye()} saniye bekleyin."
        )
        if not self._kilit_sayaci.isActive():
            self._kilit_sayaci.start()

    def _kilit_tik(self):
        if guvenlik.admin_pin.kilitli_mi():
            self.status.setText(
                f"Kilitlendi. "
                f"{guvenlik.admin_pin.kalan_saniye()} saniye bekleyin."
            )
        else:
            self._kilit_sayaci.stop()
            self.status.setText("Yonetici PIN kodunu girin")

    def reset(self):
        self._pin = ""
        self._refresh()
        if guvenlik.admin_pin.kilitli_mi():
            self._kilit_goster()
        else:
            self.status.setText("Yonetici PIN kodunu girin")
