"""Ekranlarda tekrar kullanilan kucuk yardimci bilesenler."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class Header(QWidget):
    """Ust bar: geri butonu + ekran basligi."""
    back_clicked = pyqtSignal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.back_btn = QPushButton("←  Geri")   # sol ok
        self.back_btn.setObjectName("backButton")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(self.back_clicked.emit)

        self.title = QLabel(title)
        self.title.setObjectName("screenTitle")

        layout.addWidget(self.back_btn)
        layout.addSpacing(20)
        layout.addWidget(self.title)
        layout.addStretch()
