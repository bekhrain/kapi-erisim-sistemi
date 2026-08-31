"""
RFID kart ekrani.

DIKKAT: Bu ekran ARTIK KENDI OKUYUCUSUNU ACMAZ.
--------------------------------------------------
USB okuyucu aygiti EVIOCGRAB ile OZEL moda alinir; ozel mod ayni anda
tek surece verilir. Ikinci bir okuyucu is parcacigi ayni aygiti grab
etmeye kalkarsa EBUSY alir ve sessizce hic okumaz. Ana menu
de artik surekli dinlemede oldugu icin, okuyucunun sahipligi tek bir
yere - MainWindow'a - tasindi. Kart UID'si buraya MainWindow'un
'kart_geldi()' cagrisiyla ulasir.

Ekran, kart dogrulama ve loglama mantigini tek noktada tutmaya devam
eder: menuden okutulan kart da, bu ekranda okutulan kart da ayni
'kart_geldi()' yolundan gecer, boylece log davranisi tek yerde kalir.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from app.config import DOGRULAMA_TIMEOUT, IS_RASPBERRY_PI
from app import store
from app import dogrula
from app.screens.widgets import Header

# Hic kart kayitli degilse gecerli sayilan demo kart (gelistirme kolayligi)
DEMO_CARD = "0006523498711"


class RFIDScreen(QWidget):
    back_requested = pyqtSignal()
    auth_result = pyqtSignal(bool, str)
    # Mock modda paylasimli okuyucuya sahte kart okuttur (okuyucu
    # MainWindow'da oldugu icin dogrudan cagiramiyoruz).
    kart_simule_istendi = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dogrulama = None   # calisan DogrulamaWorker (referans tutulmali)
        self._uid = ""           # dogrulamasi suren kartin UID'si
        # Ekrandan cikilirken suren dogrulamanin sonucu YOK SAYILIR.
        # Yoksa kullanici menuye dondukten saniyeler sonra gec gelen
        # cevap ekrani kendiliginden degistirirdi.
        self._yoksay = False
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 24, 40, 30)
        root.setSpacing(16)

        header = Header("RFID Kart ile Giris")
        header.back_clicked.connect(self.back_requested.emit)
        root.addWidget(header)

        root.addStretch()

        self.status = QLabel("Kartınızı okuyucuya yaklaştırın…")
        self.status.setObjectName("hint")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        root.addSpacing(10)

        # Sunucu beklenirken cikan tek cikis yolu. YEREL_YEDEK kapali
        # oldugu icin bekleme sinirsiz olabilir; buton olmasaydi kart
        # okutan kisi sunucu geri gelene kadar ekranda mahsur kalirdi.
        self.vazgec_btn = QPushButton("Vazgec")
        self.vazgec_btn.setObjectName("simButton")
        self.vazgec_btn.setCursor(Qt.PointingHandCursor)
        self.vazgec_btn.clicked.connect(self._vazgec)
        self.vazgec_btn.setFixedWidth(220)
        self.vazgec_btn.hide()
        root.addWidget(self.vazgec_btn, alignment=Qt.AlignCenter)

        # Sadece mock modda: kart simulasyon butonu
        self.sim_btn = QPushButton("Kart Simule Et")
        self.sim_btn.setObjectName("simButton")
        self.sim_btn.setCursor(Qt.PointingHandCursor)
        self.sim_btn.clicked.connect(self.kart_simule_istendi.emit)
        self.sim_btn.setFixedWidth(220)
        if IS_RASPBERRY_PI:
            self.sim_btn.hide()
        root.addWidget(self.sim_btn, alignment=Qt.AlignCenter)

        root.addStretch()

    # --- Yasam dongusu (MainWindow tarafindan cagrilir) ---
    def start(self):
        """
        Ekran acildi. Donanim baslatilmaz - okuyucu zaten MainWindow'da
        calisiyor; burada sadece metin sifirlanir.
        """
        self.status.setText("Kartinizi okuyucuya yaklastirin...")
        self.vazgec_btn.hide()
        self._yoksay = False

    def stop(self):
        """
        Ekrandan cikildi. Okuyucu paylasimli oldugu icin BURADA
        DURDURULMAZ; durdurma kararini MainWindow verir. Yalnizca bu
        ekranin ic durumu temizlenir.

        Suren bir sunucu beklemesi varsa iptal edilir: aksi halde
        kullanici menuye dondukten sonra da arka planda saniyede bir
        MySQL denemesi surerdi.
        """
        self._uid = ""
        self.vazgec_btn.hide()
        if self._dogrulama is not None and self._dogrulama.isRunning():
            self._yoksay = True
            self._dogrulama.iptal()

    # --- Kart isleme ---
    def kart_geldi(self, uid: str):
        """
        Paylasimli okuyucudan gelen kart. Dogrulama sunucuya cikabilecegi
        icin ASLA senkron yapilmaz: arayuz donarsa dokunmatik ekran
        kilitlenmis gibi gorunur. Karari sunucu verdigi ve yerel yedek
        kapali oldugu icin bekleme uzun surebilir - bu yuzden hem sayac
        hem "Vazgec" butonu gosterilir.
        """
        self._uid = uid
        # Yeni deneme: onceki turdan kalmis olabilecek "yok say" bayragi
        # temizlenir. Bayrak, biten worker'in isRunning() bir an daha
        # True donmesi yuzunden yanlislikla kalabilir; kalirsa bu okumanin
        # sonucu sessizce yutulurdu.
        self._yoksay = False
        self.status.setText("Kart okundu, kontrol ediliyor...")
        self.sim_btn.hide()
        self.vazgec_btn.show()
        self._dogrulama = dogrula.DogrulamaWorker("kart", uid, parent=self)
        self._dogrulama.tamamlandi.connect(self._sonuc)
        self._dogrulama.bekliyor.connect(self._bekleniyor)
        self._dogrulama.start()

    def _bekleniyor(self, saniye: int):
        """Sunucu cevap vermedi, bekliyoruz. Kullanici sistemin calistigini
        gorebilmeli; sabit metin 'dondu mu?' sorusunu dogurur."""
        self.status.setText(
            f"Sunucu bekleniyor... {saniye} sn\nKartinizi tekrar okutmayin")

    def _vazgec(self):
        if self._dogrulama is not None and self._dogrulama.isRunning():
            self._dogrulama.iptal()
        self.status.setText("Iptal ediliyor...")

    def _sonuc(self, sonuc: dict):
        uid = self._uid
        self.vazgec_btn.hide()
        if not IS_RASPBERRY_PI:
            self.sim_btn.show()

        # Ekran degistigi icin gec gelen cevap: hicbir sey yapma.
        if self._yoksay:
            self._yoksay = False
            return

        # Kullanici vazgecti: bu bir erisim denemesi degil, kayit tutmaya
        # deger bir olay da degil. Menuye don.
        if sonuc.get("kaynak") == "iptal":
            self.status.setText("Kartinizi okuyucuya yaklastirin...")
            self.back_requested.emit()
            return

        ok = bool(sonuc.get("ok"))
        kisi = sonuc.get("kisi")
        mesaj = sonuc.get("mesaj") or ""

        # Demo kart: hic kisi kayitli degilken cihazin denenebilmesi icin.
        # Sadece karari YEREL VERIDEN verilmis reddetmelerde devreye girer.
        # Sunucu "bu kart yetkisiz" dediyse karari ezilmez; sunucuya
        # ULASILAMADIYSA da acilmaz, cunku o durum bir karar degildir -
        # yoksa agi kesen biri demo kartla kapiyi acabilirdi.
        if (not ok and dogrula.yerel_karar_mi(sonuc)
                and uid == DEMO_CARD and not store.has_any_user()):
            store.log_access(True, "kart", detail=f"demo kart {uid}")
            self.auth_result.emit(True, "Kart kabul edildi")
            return

        if ok:
            # dogrula.detay(): basarili gecise kararin kaynagini ekler
            # ("0123 [sunucu]"), reddedilende ham UID'yi oldugu gibi
            # birakir. Sifre ekrani da ayni yardimciyi kullaniyor;
            # kayit bicimi iki yontemde ayni kalsin.
            store.log_access(True, "kart", user=kisi,
                             detail=dogrula.detay(uid, sonuc))
            if not mesaj:
                ad = (kisi or {}).get("name", "")
                mesaj = f"Hos geldiniz, {ad}".strip().rstrip(",")
        elif dogrula.karar_verildi(sonuc):
            # detay alanina SADECE UID yazilir: web arayuzundeki
            # "son okutulan karti getir" ozelligi bu alani okuyor.
            # Kartin reddedildigi bilgisi zaten sonuc=False'ta duruyor.
            store.log_access(False, "kart", detail=uid)
            if not mesaj:
                # Numara ekranda gosterilmez; kayitta duruyor.
                mesaj = "Yetkisiz kart"
        else:
            # KARAR VERILEMEDI (sunucu yok / iptal). Bu bir reddetme
            # DEGIL, bir ariza kaydidir. Ham UID yazilmaz: web arayuzu o
            # alani "kaydedilmeyi bekleyen yeni kart" havuzu sayiyor,
            # sunucu her koptugunda havuza sahte aday dusmemeli.
            store.log_access(False, "kart", detail=f"karar yok ({uid})")
            if not mesaj:
                mesaj = "Sunucuya ulasilamiyor"

        self.auth_result.emit(ok, mesaj)

    def kapan(self):
        """
        Uygulama kapaniyor: suren dogrulamayi iptal et ve BEKLE.

        Beklemek sart: QThread'in C++ nesnesi (ebeveyni bu widget) is
        parcacigi hala calisirken silinirse Qt "QThread: Destroyed while
        thread is still running" deyip surece abort ettirir - kullanici
        cikista cokme gorur. wait() sinirli: is parcacigi en gec
        DOGRULAMA_TIMEOUT icinde donguye geri doner ve iptali gorur.
        """
        if self._dogrulama is not None and self._dogrulama.isRunning():
            self._yoksay = True
            self._dogrulama.iptal()
            self._dogrulama.wait(int((DOGRULAMA_TIMEOUT + 1) * 1000))

    def hata(self, msg: str):
        """Okuyucu hatasini ekranda goster (MainWindow yonlendirir)."""
        self.status.setText(msg)
