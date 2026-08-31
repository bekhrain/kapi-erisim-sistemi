"""
Sifre giris ekrani: dokunmatik numpad.
Sifre uzunluguna ulasinca veya OK'a basinca dogrulama yapilir.
"""
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from app.config import DEMO_PASSWORD, DOGRULAMA_TIMEOUT, PASSWORD_LENGTH
from app import dogrula, guvenlik, store
from app.screens.widgets import Header

# Dogrulama artik dogrudan store.find_by_pin ile YAPILMAZ; app/dogrula.py
# uzerinden gecer. Sebep: ag varken karari sunucu vermeli, yoksa web'den
# iptal edilen bir PIN senkronizasyon turu gelene kadar gecerli kalir.
# Buradaki eski PinDogrulayici sinifinin isini dogrula.DogrulamaWorker
# devraldi; o sinif hem ag beklemesini hem PBKDF2 hesabini arka plana
# aliyor, dolayisiyla arayuzun donmamasi garantisi korunuyor.


class PasswordScreen(QWidget):
    back_requested = pyqtSignal()
    # dogrulama sonucu: (basarili_mi, mesaj)
    auth_result = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pin = ""
        self._dogrulayici = None
        self._mesgul = False
        # Ekrandan cikilirken suren dogrulamanin gec gelen cevabi
        # yok sayilir; yoksa menuye dondukten sonra sonuc ekrani acilirdi.
        self._yoksay = False
        self._kilit_sayaci = QTimer(self)
        self._kilit_sayaci.setInterval(1000)
        self._kilit_sayaci.timeout.connect(self._kilit_tik)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 24, 40, 30)
        root.setSpacing(16)

        header = Header("Sifre ile Giris")
        header.back_clicked.connect(self.back_requested.emit)
        root.addWidget(header)

        # PIN gostergesi (maskeli)
        self.display = QLabel("")
        self.display.setObjectName("pinDisplay")
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setFixedHeight(80)
        root.addWidget(self.display)

        self.status = QLabel("PIN kodunuzu girin")
        self.status.setObjectName("hint")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        # Numpad
        grid = QGridLayout()
        grid.setSpacing(12)
        keys = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
        ]
        for text, r, c in keys:
            grid.addWidget(self._digit(text), r, c)

        # Alt sira: Sil / 0 / OK
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

    # --- Mantik ---
    def _add(self, digit: str):
        if self._mesgul or guvenlik.kapi_pin.kilitli_mi():
            return
        if len(self._pin) >= PASSWORD_LENGTH:
            return
        self._pin += digit
        self._refresh()
        if len(self._pin) == PASSWORD_LENGTH:
            self._submit()

    def _backspace(self):
        if self._mesgul:
            return
        self._pin = self._pin[:-1]
        self._refresh()

    def _refresh(self):
        self.display.setText("●" * len(self._pin))

    def _submit(self):
        if self._mesgul or not self._pin:
            return
        if guvenlik.kapi_pin.kilitli_mi():
            self._kilit_goster()
            return

        # Dogrulama arka planda; arayuz bu sirada donmaz. Sunucuya
        # ulasilamazsa DogrulamaWorker sessizce yerele duser, yani
        # "Kontrol ediliyor..." mesaji en fazla birkac saniye kalir.
        self._mesgul = True
        # Onceki turdan kalmis olabilecek "yok say" bayragini temizle
        # (biten worker'in isRunning() bir an daha True donebilir).
        self._yoksay = False
        self.status.setText("Kontrol ediliyor...")
        self._dogrulayici = dogrula.DogrulamaWorker("pin", self._pin, self)
        self._dogrulayici.tamamlandi.connect(self._sonuc)
        self._dogrulayici.bekliyor.connect(self._bekleniyor)
        self._dogrulayici.start()

    def _bekleniyor(self, saniye: int):
        """
        Sunucu cevap vermedi, bekliyoruz. Yerel yedek kapali oldugu icin
        bekleme uzun surebilir; geri butonu (Header) her an calisir ve
        reset() beklemeyi iptal eder.
        """
        self.status.setText(f"Sunucu bekleniyor... {saniye} sn")

    def _sonuc(self, sonuc: dict):
        girilen, self._pin = self._pin, ""
        self._mesgul = False
        self._refresh()

        # Ekrandan cikildi / iptal edildi: sayaci ve kaydi kirletme.
        if self._yoksay or sonuc.get("kaynak") == "iptal":
            self._yoksay = False
            return

        # Karar VERILEMEDI. Bunu hatali sifir sayamayiz: sunucusu kapali
        # oldugu icin kimse 5 denemeden sonra kapiya kilitlenmemeli, ve
        # kayitlara "hatali PIN" yazmak yanlis olurdu.
        if not dogrula.karar_verildi(sonuc):
            store.log_access(False, "sifre", detail="karar yok (sunucu yok)")
            self.status.setText("Sunucuya ulasilamiyor")
            self.auth_result.emit(False, sonuc.get("mesaj")
                                  or "Sunucuya ulasilamiyor")
            return

        if sonuc.get("ok"):
            kisi = sonuc.get("kisi")
            guvenlik.kapi_pin.basarili()
            # Detaya kararin kaynagi yazilir ("[sunucu]" / "[yerel]").
            # Bir tartisma cikarsa, gecisin cevrimici mi cevrimdisi mi
            # onaylandigi kayittan okunabilir olmali.
            store.log_access(True, "sifre", user=kisi,
                             detail=dogrula.detay("", sonuc))
            self.auth_result.emit(
                True, sonuc.get("mesaj") or "Sifre dogru")
            return

        # Hic PIN'li kisi yoksa DEMO_PASSWORD'e dus (sadece gelistirme).
        # Karari SUNUCU verdiyse bu kapi kapalidir: sunucuya baglanabilen
        # bir cihaz artik gercek kurulumdadir ve orada demo sifresinin
        # gecerli olmasi acik demektir. Yerel karar (ag yok / ilk kurulum)
        # oldugunda eski davranis aynen surer.
        # DEMO_PASSWORD bos ise bu yol tamamen kapalidir; bos parola
        # "her girise izin ver" anlamina gelmemeli.
        if (DEMO_PASSWORD and dogrula.yerel_karar_mi(sonuc)
                and not store.has_any_pin() and girilen == DEMO_PASSWORD):
            guvenlik.kapi_pin.basarili()
            store.log_access(True, "sifre", detail="demo sifre")
            self.auth_result.emit(True, "Sifre dogru")
            return

        # Hatali PIN log'a yazilir ama PIN'IN KENDISI YAZILMAZ.
        # Kayitlar web arayuzunde herkese gorunuyor; denenen sifreyi
        # oraya dusurmek dogru PIN'i de er gec sizdirirdi.
        kilitlendi = guvenlik.kapi_pin.basarisiz()
        store.log_access(False, "sifre", detail="hatali PIN")
        if kilitlendi:
            self._kilit_goster()
            self.auth_result.emit(False, "Cok fazla hatali deneme")
        else:
            kalan = guvenlik.kapi_pin.kalan_hak()
            self.status.setText(f"Hatali sifre ({kalan} deneme hakki kaldi)")
            self.auth_result.emit(False, "Hatali sifre")

    def kapan(self):
        """
        Uygulama kapaniyor: suren dogrulamayi iptal et ve BEKLE.
        Beklenmezse QThread calisirken silinir ve Qt surece abort
        ettirir (cikista cokme). Bkz. RFIDScreen.kapan().
        """
        if self._dogrulayici is not None and self._dogrulayici.isRunning():
            self._yoksay = True
            self._dogrulayici.iptal()
            self._dogrulayici.wait(int((DOGRULAMA_TIMEOUT + 1) * 1000))

    # --- Kilit ---
    def _kilit_goster(self):
        self.status.setText(
            f"Cok fazla hatali deneme. "
            f"{guvenlik.kapi_pin.kalan_saniye()} saniye bekleyin."
        )
        if not self._kilit_sayaci.isActive():
            self._kilit_sayaci.start()

    def _kilit_tik(self):
        if guvenlik.kapi_pin.kilitli_mi():
            self.status.setText(
                f"Cok fazla hatali deneme. "
                f"{guvenlik.kapi_pin.kalan_saniye()} saniye bekleyin."
            )
        else:
            self._kilit_sayaci.stop()
            self.status.setText("PIN kodunuzu girin")

    def reset(self):
        """
        Ekrana her girisde / cikista temiz baslamak icin.

        Suren bir sunucu beklemesi varsa iptal edilir: kullanici geri
        tusuna bastiginda arka planda saniyede bir MySQL denemesi
        surmemelidir.
        """
        if self._dogrulayici is not None and self._dogrulayici.isRunning():
            self._yoksay = True
            self._dogrulayici.iptal()
        self._pin = ""
        self._mesgul = False
        self._refresh()
        if guvenlik.kapi_pin.kilitli_mi():
            self._kilit_goster()
        else:
            self.status.setText("PIN kodunuzu girin")
