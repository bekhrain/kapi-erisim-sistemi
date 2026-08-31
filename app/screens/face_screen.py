"""
Yuz TANIMA ekrani.

ONCEKI HALI NEDEN YANLISTI
--------------------------
Bu ekran eskiden yalnizca yuz ALGILIYORDU (Haar cascade) ve bir yuz
kararli gorulunce kapiyi aciyordu: _recognize() kosulsuz True donerdi.
Yani kameraya bakan HERKES iceri girerdi. Kart ve PIN sunucuya
baglanmisken burasi acik kalmisti; role takildigi gun bu, kilidi
olmayan bir kapi demek olurdu.

SIMDIKI AKIS
------------
    her kare  ->  YuNet ile yuz bul            (~10 ms, akici onizleme)
    N kare     ->  yuz kararli gorulduyse devam  (YUZ_KARE_ONAY)
    bir kez    ->  SFace ile 128-d vektor cikar  (~40-60 ms)
    arka plan  ->  DogrulamaWorker("yuz", vektor)

Vektor cikarma ve karsilastirma ARKA PLANDA yapilir; arayuz is
parcaciginda yapilsaydi onizleme her denemede yarim saniye donardi.

MODEL YOKSA KAPI ACILMAZ
------------------------
Model dosyalari eksikse ekran bunu YAZAR ve hicbir sekilde basari
uretmez. Eski davranisa (algilayinca ac) sessizce donmek, en
tehlikeli ariza turu olurdu: sistem calisiyor gorunur, aslinda
herkesi iceri alir.
"""
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from app.config import DOGRULAMA_TIMEOUT, YUZ_KARE_ONAY
from app import dogrula, store, yuz
from app.hardware.camera import CameraWorker
from app.screens.widgets import Header

try:
    import cv2
except ImportError:
    cv2 = None


class FaceScreen(QWidget):
    back_requested = pyqtSignal()
    auth_result = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera = None
        self._face_streak = 0
        self._done = False          # karar verildi, yeni kare islenmesin
        self._dogrulama = None
        self._yoksay = False
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 24, 40, 24)
        root.setSpacing(12)

        header = Header("Yuz Tanima ile Giris")
        header.back_clicked.connect(self.back_requested.emit)
        root.addWidget(header)

        self.view = QLabel("Kamera baslatiliyor...")
        self.view.setObjectName("cameraView")
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setFixedSize(640, 400)
        root.addWidget(self.view, alignment=Qt.AlignCenter)

        self.status = QLabel("Lutfen yuzunuzu kameraya gosterin")
        self.status.setObjectName("hint")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        # Sunucu beklenirken tek cikis yolu (bkz. RFIDScreen).
        self.vazgec_btn = QPushButton("Vazgec")
        self.vazgec_btn.setObjectName("simButton")
        self.vazgec_btn.setCursor(Qt.PointingHandCursor)
        self.vazgec_btn.clicked.connect(self._vazgec)
        self.vazgec_btn.setFixedWidth(220)
        self.vazgec_btn.hide()
        root.addWidget(self.vazgec_btn, alignment=Qt.AlignCenter)

    # --- Yasam dongusu ---
    def start(self):
        self._face_streak = 0
        self._done = False
        self._yoksay = False
        self.vazgec_btn.hide()

        hazir, aciklama = yuz.hazir_mi()
        if not hazir:
            # Modeller yoksa kamerayi hic acma: kullaniciya bos bir
            # onizleme gostermek "calisiyor ama beni tanimiyor"
            # izlenimi verir, oysa sorun kurulumdadir.
            self._done = True
            self.status.setText(f"Yuz tanima kullanilamiyor: {aciklama}")
            self.view.setText("Kart veya sifre ile giris yapin")
            return

        self.status.setText("Lutfen yuzunuzu kameraya gosterin")
        self.camera = CameraWorker(camera_index=0)
        self.camera.frame_ready.connect(self._on_frame)
        self.camera.error.connect(self._on_error)
        self.camera.start()

    def stop(self):
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
        self.vazgec_btn.hide()
        if self._dogrulama is not None and self._dogrulama.isRunning():
            self._yoksay = True
            self._dogrulama.iptal()

    def kapan(self):
        """Uygulama kapaniyor: is parcacigini iptal et ve BEKLE
        (bkz. RFIDScreen.kapan() - beklenmezse Qt abort ediyor)."""
        if self._dogrulama is not None and self._dogrulama.isRunning():
            self._yoksay = True
            self._dogrulama.iptal()
            self._dogrulama.wait(int((DOGRULAMA_TIMEOUT + 1) * 1000))

    # --- Kare isleme ---
    def _on_frame(self, frame: np.ndarray):
        if self._done or cv2 is None:
            return

        bulunan = yuz.yuz_bul(frame)

        if bulunan is not None:
            x, y, w, h = (int(v) for v in bulunan[:4])
            cv2.rectangle(frame, (x, y), (x + w, y + h), (34, 197, 94), 2)
            self._face_streak += 1
            self.status.setText("Yuz algilandi, sabit durun...")
            if self._face_streak >= YUZ_KARE_ONAY:
                self._karar_ver(frame, bulunan)
                return
        else:
            self._face_streak = 0
            self.status.setText("Lutfen yuzunuzu kameraya gosterin")

        self._show(frame)

    def _karar_ver(self, frame, bulunan):
        """
        Yuz kararli goruldu: vektoru cikar ve dogrulamaya gonder.

        _done burada TRUE yapilir; yoksa kamera akmaya devam ederken
        her karede yeni bir dogrulama is parcacigi baslatilirdi.
        """
        self._done = True
        self.status.setText("Yuz alindi, kontrol ediliyor...")
        self.vazgec_btn.show()

        # Kare kopyalanir: CameraWorker ayni tamponu bir sonraki karede
        # uzerine yazabilir, is parcacigi ise onu birkac yuz milisaniye
        # sonra okuyacak. Kopyalanmazsa yanlis goruntuden vektor cikar.
        kare = frame.copy()
        # Deger FONKSIYON olarak veriliyor: vektor cikarma (~50 ms)
        # boylece arka planda, run() icinde calisir. Burada
        # hesaplansaydi kamera onizlemesi her denemede takilirdi.
        self._dogrulama = dogrula.DogrulamaWorker(
            "yuz", lambda: yuz.vektor_cikar(kare, bulunan), parent=self)
        self._dogrulama.tamamlandi.connect(self._sonuc)
        self._dogrulama.bekliyor.connect(self._bekleniyor)
        self._dogrulama.start()

    def _bekleniyor(self, saniye: int):
        self.status.setText(f"Sunucu bekleniyor... {saniye} sn")

    def _vazgec(self):
        if self._dogrulama is not None and self._dogrulama.isRunning():
            self._dogrulama.iptal()
        self.status.setText("Iptal ediliyor...")

    def _sonuc(self, sonuc: dict):
        self.vazgec_btn.hide()

        if self._yoksay:
            self._yoksay = False
            return

        if sonuc.get("kaynak") == "iptal":
            self.back_requested.emit()
            return

        ok = bool(sonuc.get("ok"))
        kisi = sonuc.get("kisi")
        mesaj = sonuc.get("mesaj") or ""

        if ok:
            store.log_access(True, "yuz", user=kisi,
                             detail=dogrula.detay("", sonuc))
        elif dogrula.karar_verildi(sonuc):
            # Taninmayan yuz. Kayda GORUNTU veya VEKTOR yazilmaz:
            # kayitlar web arayuzunde gorunuyor ve biyometrik veri
            # oraya dusmemeli.
            store.log_access(False, "yuz", detail="taninmayan yuz")
            if not mesaj:
                mesaj = "Yuz taninmadi"
        else:
            store.log_access(False, "yuz", detail="karar yok (sunucu yok)")
            if not mesaj:
                mesaj = "Sunucuya ulasilamiyor"

        self.auth_result.emit(ok, mesaj)

    def _show(self, frame: np.ndarray):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(
            self.view.width(), self.view.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.view.setPixmap(pix)

    def _on_error(self, msg: str):
        self.status.setText(msg)
