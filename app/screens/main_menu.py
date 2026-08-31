"""
Ana menu: 3 giris yontemi (dikey/portrait yerlesim).
Kullanici hangisine basarsa o yontemin ekrani acilir.
Ustte canli saat/tarih bulunur; sag alt kosede yoneticiye ozel
"Admin" butonu vardir.

RFID KARTTA DOKUNMA YOK
-----------------------
Bu ekran ayni zamanda RFID okuyucuyu dinler durumdadir. Personelin
buyuk cogunlugu kartla giriyor; her giriste once ekrana dokunup
"RFID Kart" secmek zorunda kalmalari dokunmatik paneli gereksiz yere
asindiriyordu. Artik menude beklerken kart okutmak yeterli.
Okuyucunun kendisi burada DEGIL, MainWindow'da yasar (tek paylasimli
okuyucu); bu ekran sadece durum satirini gunceller.
"""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,

)
from datetime import datetime

from app.config import IS_RASPBERRY_PI


class MainMenu(QWidget):
    # secilen giris yontemini disariya bildirir: "rfid" | "face" | "password"
    method_selected = pyqtSignal(str)
    # sag alttaki Admin butonu
    admin_requested = pyqtSignal()
    # sadece mock modda: paylasimli okuyucuya sahte kart okuttur
    kart_simule_istendi = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        # Saati her saniye guncelle
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 14, 24, 14)
        root.setSpacing(8)

        # Canli saat / tarih. Pi'de donanim saati YOK; sistem saati
        # aciliste agdan (NTP) alinir. Ag yoksa saat kayabilir.
        self.clock = QLabel("")
        self.clock.setObjectName("clock")
        self.clock.setAlignment(Qt.AlignCenter)
        root.addWidget(self.clock)

        # Baslik: kurum adi
        title = QLabel("Erciyes Üniversitesi")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)

        subtitle = QLabel("Bilgi İşlem Daire Başkanlığı")
        subtitle.setObjectName("orgSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)

        hint = QLabel("Kartınızı okutun veya bir giriş yöntemi seçin")
        hint.setObjectName("subtitle")
        hint.setAlignment(Qt.AlignCenter)

        # Kart durum satiri. Okuyucu surekli dinlemede oldugu icin
        # kullanicinin karti okundugunda bir sey oldugunu anlamasi lazim;
        # aksi halde ekranda hicbir degisiklik olmadan sonuc ekrani acilir
        # ve kullanici kartin okundugundan emin olamaz.
        self.kart_durum = QLabel("Kartınızı okuyucuya yaklaştırabilirsiniz")
        self.kart_durum.setObjectName("hint")
        self.kart_durum.setAlignment(Qt.AlignCenter)

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(hint)
        root.addWidget(self.kart_durum)
        root.addSpacing(4)

        # 3 buyuk secim butonu -> DIKEY olarak alt alta
        buttons = [
            ("rfid", "Kart ile Giriş", "Kartınızı okutun"),
            ("face", "Yüz ile Giriş", "Kameraya bakın"),
            ("password", "Şifre ile Giriş", "PIN kodu girin"),
        ]

        for method, name, desc in buttons:
            btn = self._make_button(name, desc)
            btn.clicked.connect(lambda _, m=method: self.method_selected.emit(m))
            root.addWidget(btn, stretch=1)

        # Alt sira: solda (sadece mock modda) kart simulasyonu, sagda Admin
        bottom = QHBoxLayout()

        # Masaustunde gercek okuyucu yok; menude beklerken kart okutmayi
        # test edebilmek icin buton. Raspberry Pi'de gizlenir.
        self.sim_btn = QPushButton("Kart Simüle Et")
        self.sim_btn.setObjectName("simButton")
        self.sim_btn.setCursor(Qt.PointingHandCursor)
        self.sim_btn.clicked.connect(self.kart_simule_istendi.emit)
        if IS_RASPBERRY_PI:
            self.sim_btn.hide()
        bottom.addWidget(self.sim_btn)

        bottom.addStretch()
        self.admin_btn = QPushButton("Yönetim")
        self.admin_btn.setObjectName("adminButton")
        self.admin_btn.setCursor(Qt.PointingHandCursor)
        self.admin_btn.clicked.connect(self.admin_requested.emit)
        bottom.addWidget(self.admin_btn)
        root.addLayout(bottom)

    def _make_button(self, name: str, desc: str) -> QPushButton:
        # SIMGELER KALDIRILDI (21.08.2026). Emoji, sistem yazi tipine
        # gore cihazdan cihaza farkli boyda ciziliyor ve butondaki
        # metni asagi kaydiriyordu. Yontem adi zaten yaziyor; simge
        # bir bilgi eklemiyordu.
        btn = QPushButton(name + "\n" + desc)
        btn.setObjectName("menuButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(150)
        # Dikeyde de buyuyebilsin: kalan tum ekran alanini butonlar doldurur
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return btn

    # --- Kart durum satiri (MainWindow tarafindan cagrilir) ---
    def kart_bekleniyor(self):
        """Menuye donuldu / dinleme yeniden basladi."""
        self.kart_durum.setText("Kartınızı okuyucuya yaklaştırabilirsiniz")

    def kart_okundu(self):
        """Kart yakalandi, dogrulama arka planda suruyor."""
        self.kart_durum.setText("Kart okundu, kontrol ediliyor…")

    def kart_hatasi(self, mesaj: str):
        """Okuyucu baslatilamadi / okuma hatasi. Sifre ve yuz calismaya
        devam ettigi icin ekrani kilitlemek yerine sadece bilgi verilir."""
        self.kart_durum.setText(mesaj)

    def _update_clock(self):
        self.clock.setText(datetime.now().strftime("%d.%m.%Y   %H:%M:%S"))
