"""
Admin paneli: kayitli kisileri listeler, yeni kisi tanitir veya cikarir,
giris kayitlarini gosterir.
Tamamen yerel SQLite veritabani (data/access.db) uzerinden calisir; internet gerekmez.

Uc sayfa vardir (dahili QStackedLayout):
    0) Liste sayfasi   -> kisiler + "Sil" butonlari + "Yeni Kisi Ekle" + "Kayitlar"
    1) Ekleme sayfasi  -> isim, PIN, kart okutma, yuz yakalama, Kaydet/Iptal
    2) Kayitlar sayfasi-> son giris denemeleri (kim, yontem, sonuc)
"""
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QStackedLayout, QVBoxLayout, QWidget,
)

from app.config import IS_RASPBERRY_PI, YUZ_SFACE_DOSYA
from app import store, sync, yuz
from app.hardware.camera import CameraWorker
from app.hardware.rfid import RFIDWorker
from app.screens.widgets import Header

try:
    import cv2
except ImportError:
    cv2 = None


class AdminScreen(QWidget):
    back_requested = pyqtSignal()
    # Cihazda personel eklendi / silindi / duzenlendi. MainWindow bunu
    # senkronizasyona baglar: degisiklik sunucuya beklemeden gider.
    veri_degisti = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rfid = None
        self.camera = None
        self._captured_card = ""
        self._last_frame = None
        self._face_captured = False
        self._face_vector = ""     # SFace 128-d vektor (base64)
        self._face_frame = None    # vektorun cikarildigi kare
        self._build()

    def _build(self):
        self._stack = QStackedLayout(self)
        self._stack.addWidget(self._build_list_page())
        self._stack.addWidget(self._build_add_page())
        self._stack.addWidget(self._build_logs_page())

    # ---------------- Liste sayfasi ----------------
    def _build_list_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(14)

        header = Header("Admin - Kisiler")
        header.back_clicked.connect(self.back_requested.emit)
        root.addWidget(header)

        # SENKRONIZASYON DURUMU
        # ---------------------
        # Bu ekrandaki liste, sunucudan en son ne zaman alindiysa o
        # halidir. Sunucuya bir suredir ulasilamiyorsa listede
        # gorunen kisiler guncel OLMAYABILIR: web'den yetkisi
        # kaldirilan biri burada hala yetkili gorunur.
        #
        # Uyari yoksa admin, gordugu listenin canli oldugunu varsayar
        # ve yanlis bilgiyle karar verir. Bu yuzden liste bayatladiginda
        # (sync.is_stale) satir kirmiziya doner.
        self.sync_label = QLabel("")
        self.sync_label.setWordWrap(True)
        self.sync_label.setObjectName("syncDurum")
        root.addWidget(self.sync_label)

        # Kaydirilabilir kisi listesi
        self._list_container = QVBoxLayout()
        self._list_container.setSpacing(10)
        self._list_container.addStretch()

        holder = QWidget()
        holder.setLayout(self._list_container)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setObjectName("adminScroll")
        root.addWidget(scroll, stretch=1)

        add_btn = QPushButton("Yeni Kişi Ekle")
        add_btn.setObjectName("simButton")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setMinimumHeight(56)
        add_btn.clicked.connect(self._open_add_page)
        root.addWidget(add_btn)

        logs_btn = QPushButton("Giriş Kayıtları")
        logs_btn.setObjectName("simButton")
        logs_btn.setCursor(Qt.PointingHandCursor)
        logs_btn.setMinimumHeight(56)
        logs_btn.clicked.connect(self._open_logs_page)
        root.addWidget(logs_btn)

        return page

    def _refresh_list(self):
        # Onceki kartlari temizle (son eleman stretch)
        while self._list_container.count() > 1:
            item = self._list_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        users = store.load_users()
        if not users:
            empty = QLabel("Henuz kayitli kisi yok.\n\"Yeni Kisi Ekle\" ile baslayin.")
            empty.setObjectName("hint")
            empty.setAlignment(Qt.AlignCenter)
            self._list_container.insertWidget(0, empty)
            return

        for u in users:
            self._list_container.insertWidget(
                self._list_container.count() - 1, self._user_row(u)
            )

    def _user_row(self, user: dict) -> QWidget:
        row = QWidget()
        row.setObjectName("userCard")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 12, 12, 12)

        # Hangi yontemler tanimli?
        methods = []
        if user.get("pin"):
            methods.append("Sifre")
        if user.get("card"):
            methods.append("Kart")
        if user.get("face"):
            methods.append("Yuz")
        methods_txt = ", ".join(methods) if methods else "tanimli yontem yok"

        rol_txt = store.ROLLER.get(user.get("rol", "personel"), "Personel")
        info = QLabel(user.get("name", "-") + chr(10) + rol_txt + " - " + methods_txt)
        info.setObjectName("userInfo")
        lay.addWidget(info)
        lay.addStretch()

        del_btn = QPushButton("Sil")
        del_btn.setObjectName("keyDel")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFixedSize(110, 56)
        del_btn.clicked.connect(lambda _, uid=user.get("id"): self._delete(uid))
        lay.addWidget(del_btn)

        return row

    def _delete(self, user_id: int):
        store.remove_user(user_id)
        self._refresh_list()
        self.veri_degisti.emit()

    # ---------------- Ekleme sayfasi ----------------
    def _build_add_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(12)

        header = Header("Yeni Kisi Tanit")
        header.back_clicked.connect(self._open_list_page)
        root.addWidget(header)

        # Isim
        root.addWidget(self._field_label("Isim"))
        self.name_input = QLineEdit()
        self.name_input.setObjectName("adminInput")
        self.name_input.setPlaceholderText("Kisi adi")
        root.addWidget(self.name_input)

        # KISI TURU
        # ---------
        # Isim alanindan hemen sonra soruluyor. Formun sonuna konsaydi
        # gozden kacar ve varsayilanla gecilirdi - yani misafire
        # personel yetkisi verilirdi.
        #
        # Acilir liste yerine uc dugme: ekran dokunmatik ve 7 inc.
        # Acilir listede secenekler parmak ucundan kucuk cikiyor.
        root.addWidget(self._field_label("Kisi Turu"))
        rol_satiri = QHBoxLayout()
        rol_satiri.setSpacing(8)
        self.rol_grubu = QButtonGroup(self)
        self.rol_dugmeleri = {}
        for anahtar, etiket in store.ROLLER.items():
            d = QPushButton(etiket)
            d.setObjectName("rolButton")
            d.setCheckable(True)
            d.setCursor(Qt.PointingHandCursor)
            d.setMinimumHeight(52)
            self.rol_grubu.addButton(d)
            self.rol_dugmeleri[anahtar] = d
            rol_satiri.addWidget(d)
        self.rol_dugmeleri["personel"].setChecked(True)
        root.addLayout(rol_satiri)

        # PIN (opsiyonel)
        root.addWidget(self._field_label("Sifre / PIN (opsiyonel)"))
        self.pin_input = QLineEdit()
        self.pin_input.setObjectName("adminInput")
        self.pin_input.setPlaceholderText("Orn. 123456")
        self.pin_input.setEchoMode(QLineEdit.Password)
        root.addWidget(self.pin_input)

        # Kart okutma
        root.addWidget(self._field_label("RFID Kart (opsiyonel)"))
        card_row = QHBoxLayout()
        self.card_label = QLabel("Kart okutulmadi")
        self.card_label.setObjectName("hint")
        card_row.addWidget(self.card_label)
        card_row.addStretch()
        self.card_btn = QPushButton("Kart Oku")
        self.card_btn.setObjectName("simButton")
        self.card_btn.setCursor(Qt.PointingHandCursor)
        self.card_btn.clicked.connect(self._read_card)
        card_row.addWidget(self.card_btn)
        root.addLayout(card_row)

        # Yuz yakalama
        root.addWidget(self._field_label("Yuz (opsiyonel)"))
        self.view = QLabel("Kamera kapali")
        self.view.setObjectName("cameraView")
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setFixedHeight(220)
        root.addWidget(self.view)

        face_row = QHBoxLayout()
        self.face_start_btn = QPushButton("Kamerayi Ac")
        self.face_start_btn.setObjectName("simButton")
        self.face_start_btn.setCursor(Qt.PointingHandCursor)
        self.face_start_btn.clicked.connect(self._toggle_camera)
        face_row.addWidget(self.face_start_btn)
        self.face_capture_btn = QPushButton("Yuzu Kaydet")
        self.face_capture_btn.setObjectName("simButton")
        self.face_capture_btn.setCursor(Qt.PointingHandCursor)
        self.face_capture_btn.clicked.connect(self._capture_face)
        self.face_capture_btn.setEnabled(False)
        face_row.addWidget(self.face_capture_btn)
        root.addLayout(face_row)

        root.addStretch()

        # Kaydet / Iptal
        actions = QHBoxLayout()
        cancel = QPushButton("Iptal")
        cancel.setObjectName("backButton")
        cancel.setMinimumHeight(56)
        cancel.clicked.connect(self._open_list_page)
        actions.addWidget(cancel)

        save = QPushButton("Kaydet")
        save.setObjectName("keyOk")
        save.setMinimumHeight(56)
        save.clicked.connect(self._save_user)
        actions.addWidget(save)
        root.addLayout(actions)

        return page

    def _secili_rol(self) -> str:
        """Formda isaretli kisi turu; hicbiri degilse 'personel'."""
        for anahtar, dugme in self.rol_dugmeleri.items():
            if dugme.isChecked():
                return anahtar
        return "personel"

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    # ---------------- Kayitlar (log) sayfasi ----------------
    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(14)

        header = Header("Giris Kayitlari")
        header.back_clicked.connect(self._open_list_page)
        root.addWidget(header)

        self._logs_container = QVBoxLayout()
        self._logs_container.setSpacing(8)
        self._logs_container.addStretch()

        holder = QWidget()
        holder.setLayout(self._logs_container)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setObjectName("adminScroll")
        root.addWidget(scroll, stretch=1)

        clear_btn = QPushButton("Kayıtları Temizle")
        clear_btn.setObjectName("keyDel")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setMinimumHeight(56)
        clear_btn.clicked.connect(self._clear_logs)
        root.addWidget(clear_btn)

        return page

    def _open_logs_page(self):
        self._refresh_logs()
        self._stack.setCurrentIndex(2)

    def _refresh_logs(self):
        while self._logs_container.count() > 1:
            item = self._logs_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        logs = store.get_logs(limit=100)
        if not logs:
            empty = QLabel("Henuz kayit yok.")
            empty.setObjectName("hint")
            empty.setAlignment(Qt.AlignCenter)
            self._logs_container.insertWidget(0, empty)
            return

        for log in logs:
            self._logs_container.insertWidget(
                self._logs_container.count() - 1, self._log_row(log)
            )

    def _log_row(self, log: dict) -> QWidget:
        row = QWidget()
        row.setObjectName("userCard")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 10, 14, 10)

        name = log.get("user_name") or "Bilinmiyor"
        method = {"sifre": "Sifre", "kart": "Kart", "yuz": "Yuz"}.get(
            log.get("method"), log.get("method", "-")
        )
        info = QLabel(f"{name}  ·  {method}\n{log.get('ts', '')}")
        info.setObjectName("logInfo")
        lay.addWidget(info)
        lay.addStretch()

        ok = bool(log.get("success"))
        status = QLabel("✔" if ok else "✖")
        status.setObjectName("logOk" if ok else "logFail")
        lay.addWidget(status)

        return row

    def _clear_logs(self):
        store.clear_logs()
        self._refresh_logs()

    # ---------------- Sayfa gecisleri ----------------
    def start(self):
        """Admin paneline girildiginde cagrilir."""
        self._open_list_page()

    def _sync_durumunu_yaz(self):
        """Sunucu baglantisinin tazeligini ekrana yazar."""
        metin = sync.status_text()
        bayat = sync.is_stale()
        self.sync_label.setText(metin)
        # Renk stil dosyasindan degil buradan veriliyor: ayni etiket
        # duruma gore iki farkli anlam tasiyor ve QSS'te durum secicisi
        # icin ayrica bir ozellik tanimlamak gerekirdi.
        self.sync_label.setStyleSheet(
            "color: #c0392b; font-weight: 600;" if bayat else "color: #7f8c9b;"
        )

    def _open_list_page(self):
        self._stop_hardware()
        self._refresh_list()
        self._sync_durumunu_yaz()
        self._stack.setCurrentIndex(0)

    def _open_add_page(self):
        self._reset_add_form()
        self._stack.setCurrentIndex(1)

    def _reset_add_form(self):
        self.name_input.clear()
        self.pin_input.clear()
        # Rol her yeni kayitta varsayilana doner. Onceki secim kalsaydi
        # arka arkaya kisi eklerken bir onceki kisinin turu sessizce
        # devralinirdi.
        self.rol_dugmeleri["personel"].setChecked(True)
        self._captured_card = ""
        self.card_label.setText("Kart okutulmadi")
        self._face_captured = False
        self._face_vector = ""
        self._face_frame = None
        self._last_frame = None
        self.view.setText("Kamera kapali")
        self.view.setPixmap(QPixmap())
        self.face_capture_btn.setEnabled(False)
        self.face_capture_btn.setText("Yuzu Kaydet")
        self.face_start_btn.setText("Kamerayi Ac")

    # ---------------- Kart okuma ----------------
    def _read_card(self):
        self.card_label.setText("Kart bekleniyor...")
        self.rfid = RFIDWorker()
        self.rfid.card_read.connect(self._on_card)
        self.rfid.error.connect(lambda m: self.card_label.setText(m))
        self.rfid.start()
        # Masaustu (mock) modda gercek kart yok -> otomatik ornek UID uret
        if not IS_RASPBERRY_PI:
            self.rfid.simulate_card()

    def _on_card(self, uid: str):
        self._captured_card = uid
        self.card_label.setText(f"Kart okundu: {uid}")
        if self.rfid is not None:
            self.rfid.stop()
            self.rfid = None

    # ---------------- Yuz yakalama ----------------
    def _toggle_camera(self):
        if self.camera is None:
            self.camera = CameraWorker(camera_index=0)
            self.camera.frame_ready.connect(self._on_frame)
            self.camera.error.connect(lambda m: self.view.setText(m))
            self.camera.start()
            self.face_start_btn.setText("Kamerayi Kapat")
            self.face_capture_btn.setEnabled(True)
        else:
            self._stop_camera()
            self.face_start_btn.setText("Kamerayi Ac")
            self.face_capture_btn.setEnabled(False)

    def _on_frame(self, frame: np.ndarray):
        self._last_frame = frame
        if cv2 is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(
            self.view.width(), self.view.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.view.setPixmap(pix)

    def _capture_face(self):
        """
        Yuzu KAYDET: fotograf degil, kimlik vektoru cikarilir.

        Eskiden burada sadece bir bayrak set ediliyordu ("yuz yakalandi")
        ve kapida karsilastirilacak hicbir sey uretilmiyordu. Kayit
        aninda vektor cikarilmazsa hata da alinmaz - kisi listede
        "Yuz" tanimli gorunur ama kapida asla taninmaz.
        """
        if self._last_frame is None:
            self.view.setText("Once kamerayi acin")
            return

        hazir, aciklama = yuz.hazir_mi()
        if not hazir:
            self.face_capture_btn.setText("✖ Model yok")
            self.view.setText(f"Yuz tanima kullanilamiyor: {aciklama}")
            return

        kare = self._last_frame.copy()
        bulunan = yuz.yuz_bul(kare)
        if bulunan is None:
            # Yuz bulunamadan kaydetmek, bos bir vektor uretip kisiyi
            # "yuzu var" diye isaretlemek olurdu.
            self.face_capture_btn.setText("✖ Yuz bulunamadi")
            self.view.setText("Yuz bulunamadi - kameraya bakip tekrar deneyin")
            return

        vektor = yuz.vektor_cikar(kare, bulunan)
        if not vektor:
            self.face_capture_btn.setText("✖ Vektor cikarilamadi")
            return

        self._face_vector = vektor
        self._face_captured = True
        self._face_frame = kare
        self.face_capture_btn.setText("✔ Yuz Kaydedildi")

    # ---------------- Kaydet ----------------
    def _save_user(self):
        name = self.name_input.text().strip()
        if not name:
            self.name_input.setPlaceholderText("Isim zorunlu!")
            self.name_input.setFocus()
            return

        try:
            user = store.add_user(
                name=name,
                pin=self.pin_input.text().strip(),
                card=self._captured_card,
                face=self._face_captured,
                yuz_vektor=self._face_vector,
                yuz_model=YUZ_SFACE_DOSYA if self._face_vector else "",
                rol=self._secili_rol(),
            )
        except ValueError as e:
            # Ayni PIN veya ayni kart baska kisiye tanimli. Kaydedilirse
            # gecisler yanlis kisi adina loglanirdi.
            self.pin_input.clear()
            self.pin_input.setPlaceholderText(str(e))
            self.pin_input.setFocus()
            return
        except Exception:  # noqa: BLE001 - kart benzersizlik ihlali vb.
            self.name_input.setPlaceholderText("Bu kart zaten kayitli!")
            return

        # Referans fotografi yerelde sakla. Karar vektorle verilir;
        # fotograf sadece admin listesinde kimi kaydettigimizi gormek
        # ve gerekirse vektoru yeni bir modelle yeniden uretmek icin
        # durur. Sunucuya GONDERILMEZ.
        if (self._face_captured and self._face_frame is not None
                and cv2 is not None):
            try:
                cv2.imwrite(store.face_image_path(user["id"]), self._face_frame)
            except Exception:  # noqa: BLE001
                pass

        self._open_list_page()
        self.veri_degisti.emit()

    # ---------------- Temizlik ----------------
    def _stop_camera(self):
        if self.camera is not None:
            self.camera.stop()
            self.camera = None

    def _stop_hardware(self):
        self._stop_camera()
        if self.rfid is not None:
            self.rfid.stop()
            self.rfid = None

    def stop(self):
        self._stop_hardware()
