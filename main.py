"""
Giris Sistemi - Ana Menu / Giris Ekrani
Raspberry Pi (7" 1024x600 dokunmatik) icin PyQt5 uygulamasi.

Calistirma:
    python main.py

- Raspberry Pi'de otomatik olarak gercek donanimi (USB RFID, kamera)
  kullanir.
- Windows/masaustunde "mock" modda calisir; RFID ve yuz tanima ekranlarindaki
  simulasyon butonlariyla akis test edilebilir.
- Tam ekrandan cikmak / girmek:  ESC = normal pencere, F11 = tam ekran.
"""
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QStackedWidget

from app.config import (
    FULLSCREEN, IS_RASPBERRY_PI, SCREEN_HEIGHT, SCREEN_WIDTH,
)
from app import dogrula
from app.style import QSS
from app.sync import SyncWorker
from app import store
from app.hardware.rfid import RFIDWorker
from app.hardware.kapi_donanim import KapiDonanim
from app.screens.main_menu import MainMenu
from app.screens.password_screen import PasswordScreen
from app.screens.rfid_screen import RFIDScreen
from app.screens.face_screen import FaceScreen
from app.screens.result_screen import ResultScreen
from app.screens.admin_login import AdminLogin
from app.screens.admin_screen import AdminScreen


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Giris Sistemi")
        self.setFixedSize(SCREEN_WIDTH, SCREEN_HEIGHT)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Ekranlar
        self.menu = MainMenu()
        self.password = PasswordScreen()
        self.rfid = RFIDScreen()
        self.face = FaceScreen()
        self.result = ResultScreen()
        self.admin_login = AdminLogin()
        self.admin = AdminScreen()

        for w in (self.menu, self.password, self.rfid, self.face,
                  self.result, self.admin_login, self.admin):
            self.stack.addWidget(w)

        # Navigasyon baglantilari
        self.menu.method_selected.connect(self._open_method)
        self.menu.admin_requested.connect(self._open_admin_login)

        self.admin_login.back_requested.connect(self._go_menu)
        self.admin_login.unlocked.connect(self._open_admin)
        self.admin.back_requested.connect(self._go_menu)
        # Cihazda personel degisikligi olunca beklemeden sunucuya git
        self.admin.veri_degisti.connect(self._veri_degisti)

        self.password.back_requested.connect(self._go_menu)
        self.password.auth_result.connect(self._show_result)

        self.rfid.back_requested.connect(self._go_menu)
        self.rfid.auth_result.connect(self._show_result)

        # Mock modda "Kart Simule Et" butonlari: okuyucu artik ekranlara
        # degil bu pencereye ait oldugu icin istek buraya gelir.
        self.menu.kart_simule_istendi.connect(self._kart_simule)
        self.rfid.kart_simule_istendi.connect(self._kart_simule)

        self.face.back_requested.connect(self._go_menu)
        self.face.auth_result.connect(self._show_result)

        self.result.finished.connect(self._go_menu)

        # Arka planda sunucu senkronizasyonu. Kart dogrulamayi hicbir
        # sekilde bloklamaz; basarisiz olursa uygulama calismaya devam eder.
        self.sync = SyncWorker(self)
        self.sync.finished_round.connect(self._on_sync)
        self.sync.start()

        # --- TEK PAYLASIMLI RFID OKUYUCU ---
        # USB okuyucu klavye taklidi yapiyor ve aygit EVIOCGRAB ile OZEL
        # moda aliniyor (yoksa kart numarasi odaktaki pencereye yazilir).
        # Ozel mod tek surece verilir: ikinci bir RFIDWorker ayni aygiti
        # grab etmeye kalkarsa EBUSY alir ve o okuyucu sessizce calismaz.
        # Bu yuzden okuyucunun tek sahibi bu penceredir; hangi ekranin
        # kart dinledigini de burasi karara baglar. Ekranlar kendi
        # worker'ini ACMAZ (admin ekrani haric - asagiya bakiniz).
        self._rfid = RFIDWorker(self)
        self._rfid.card_read.connect(self._kart_geldi)
        self._rfid.error.connect(self._rfid_hatasi)
        # Dogrulama suruyorken gelen ikinci okumayi yok saymak icin bayrak.
        # Kart okuyucuya yakin tutuldugunda ayni kart tekrar okunabilir;
        # bu bayrak olmadan ust uste iki sonuc ekrani acilirdi.
        self._kart_mesgul = False

        # --- KAPI DONANIMI ---
        # Selenoid kilit + buzzer + 3'lu trafik lambasi + MC38 manyetik
        # sensor. RFID okuyucuda oldugu gibi tek sahibi bu penceredir;
        # ekranlar donanima dokunmaz, sonuc/menu gecisleri buradan surer.
        # Masaustunde mock: eylemler terminale yazilir.
        self.kapi = KapiDonanim(self)
        self.kapi.kapi_durumu_degisti.connect(self._kapi_durumu_degisti)
        self.kapi.kapi_acik_kaldi.connect(self._kapi_acik_kaldi)
        self.kapi.izinsiz_acilma.connect(self._izinsiz_acilma)
        self.kapi.start()

        self._go_menu()

    def _on_sync(self, ok: bool, message: str):
        print(f"[sync] {'OK ' if ok else 'HATA'} {message}")

    def _veri_degisti(self):
        """Cihazda personel eklendi/silindi: sunucuya hemen bildir."""
        self.sync.tetikle()

    # --- Paylasimli RFID okuyucu yonetimi ---
    def _rfid_dinle(self, aktif: bool):
        """
        Okuyucu is parcacigini ac/kapa.

        Normalde okuyucu SUREKLI aciktir: menu, sifre, yuz ve sonuc
        ekranlarinda hep doner. Boylece her ekran gecisinde aygiti
        yeniden acip grab etmek (ve o sirada okumayi kacirmak)
        gerekmez. Hangi okumanin isleneceginin karari '_kart_geldi'
        icinde verilir.

        Tek istisna admin akisidir: admin ekrani yeni kisi eklerken
        KENDI RFIDWorker'ini aciyor. Aygit ayni anda tek surece grab
        edilebildigi icin ikisi birlikte calisamaz; bu yuzden admin
        akisina girerken paylasimli okuyucu tamamen durdurulur.
        """
        if aktif:
            if not self._rfid.isRunning():
                self._rfid.start()
        elif self._rfid.isRunning():
            self._rfid.stop()

    def _kart_geldi(self, uid: str):
        """Paylasimli okuyucudan kart geldi; hangi ekrandaysak ona goturur."""
        # Cift okuma korumasi: onceki kartin dogrulamasi bitmeden gelen
        # okumalar yok sayilir. Bayrak menuye donunce temizlenir.
        if self._kart_mesgul:
            return

        aktif = self.stack.currentWidget()
        if aktif is self.menu:
            # Kullanici ekrana hic dokunmadan kart okuttu. Karari sunucu
            # verdigi ve yerel yedek kapali oldugu icin bekleme uzun
            # surebilir; menude kalsaydik kullanici tek satirlik bir
            # metne bakar, iptal edemez ve "dondu" sanirdi. Kart ekranina
            # geciyoruz: orada saniye sayaci ve "Vazgec" butonu var.
            self.menu.kart_okundu()
            self.stack.setCurrentWidget(self.rfid)
            self.rfid.start()
            # Dogrulama basladi: trafik lambasi SARI.
            self.kapi.beklemede()
        elif aktif is not self.rfid:
            # Sifre / yuz / admin ekranlarindayken kart okumasi yok sayilir;
            # kullanici bilincli olarak baska bir yontem secmis durumda.
            return

        self._kart_mesgul = True
        # Dogrulama ve loglama mantigi tek yerde (RFIDScreen) dursun diye
        # menuden gelen kart da ayni yoldan gecirilir.
        self.rfid.kart_geldi(uid)

    def _rfid_hatasi(self, msg: str):
        aktif = self.stack.currentWidget()
        if aktif is self.menu:
            self.menu.kart_hatasi(msg)
        elif aktif is self.rfid:
            self.rfid.hata(msg)

    def _kart_simule(self):
        """Mock modda sahte kart okutur (butonlar buraya bagli)."""
        self._rfid.simulate_card()

    # --- Kapi donanimi olaylari ---
    # Bu olaylar telemetridir; kayit yazilamamasi (DB kilitli vb.) kapi
    # akisini DURDURMAMALI - bu yuzden hepsi sarilir.
    def _kapi_olayi_yaz(self, tur: str, detay: str = ""):
        try:
            store.kapi_olayi_kaydet(tur, detay)
        except Exception as e:  # noqa: BLE001
            print(f"[kapi] olay yazilamadi ({tur}): {e}")

    def _kapi_durumu_degisti(self, acik: bool):
        self._kapi_olayi_yaz("acildi" if acik else "kapandi")
        print(f"[kapi] sensor: {'ACIK' if acik else 'KAPALI'}")

    def _kapi_acik_kaldi(self):
        self._kapi_olayi_yaz("acik_kaldi", "kapi cok uzun acik kaldi")
        print("[kapi] UYARI: kapi acik kaldi")

    def _izinsiz_acilma(self):
        self._kapi_olayi_yaz("izinsiz_acilma", "gecerli giris olmadan acildi")
        print("[kapi] ALARM: izinsiz acilma")

    # --- Navigasyon ---
    def _open_method(self, method: str):
        self.kapi.beklemede()
        if method == "password":
            self.password.reset()
            self.stack.setCurrentWidget(self.password)
        elif method == "rfid":
            self.stack.setCurrentWidget(self.rfid)
            self.rfid.start()
        elif method == "face":
            self.stack.setCurrentWidget(self.face)
            self.face.start()

    def _open_admin_login(self):
        # Admin akisi basliyor: paylasimli okuyucu simdiden susturulur.
        # Admin ekrani kendi okuyucusunu aciyor; aygit tek surece grab
        # edilebildigi icin ikisi birlikte calisamaz.
        self._rfid_dinle(False)
        self.admin_login.reset()
        self.stack.setCurrentWidget(self.admin_login)

    def _open_admin(self):
        self.stack.setCurrentWidget(self.admin)
        self.admin.start()

    def _go_menu(self):
        # Aktif donanimlari durdur. reset()/stop() ayni zamanda suren
        # sunucu beklemelerini iptal eder; menuye donuldukten sonra arka
        # planda MySQL denemesi surmemeli.
        self.rfid.stop()
        self.password.reset()
        self.face.stop()
        # Admin kendi okuyucusunu ve kamerasini burada birakir; paylasimli
        # okuyucuyu ancak ondan SONRA geri acabiliriz.
        self.admin.stop()
        self.stack.setCurrentWidget(self.menu)
        # Sonuc ekranindan / admin panelinden donuldu: menude dinleme
        # kaldigi yerden devam etsin, kullanici tekrar kart okutabilsin.
        self._kart_mesgul = False
        self.menu.kart_bekleniyor()
        self._rfid_dinle(True)
        # Bosta: trafik lambasi KIRMIZI, kilit kapali. (Kilidi otomatik
        # kapatan zamanlayici zaten calisiyorsa dokunmaz - erken menuye
        # donmek kapiyi vaktinden once kilitlemesin.)
        self.kapi.bosta()

    def _show_result(self, success: bool, message: str):
        # Sonuca gecmeden once donanimi durdur
        self.rfid.stop()
        self.face.stop()
        self.stack.setCurrentWidget(self.result)
        self.result.show_result(success, message)
        # Fiziksel tepki: onayda YESIL + kisa bip + kilit KAPI_ACIK_SANIYE
        # sn acilir; redde KIRMIZI + uzun bip (kilit acilmaz).
        if success:
            self.kapi.giris_onaylandi()
        else:
            self.kapi.giris_reddedildi()
        # Kayit sunucuya hemen ciksin; kapi zaten karari verdi, bu cagri
        # sadece web arayuzunun guncel kalmasi icin.
        self.sync.tetikle()

    # --- Klavye kisayollari (gelistirme kolayligi) ---
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self.showFullScreen() if not self.isFullScreen() else self.showNormal()
        elif event.key() == Qt.Key_Escape:
            self.showNormal()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.rfid.stop()
        self.face.stop()
        # Suren dogrulama is parcaciklarini iptal edip BEKLE. Yalnizca
        # iptal etmek yetmez: QThread hala calisirken silinirse Qt
        # surece abort ettirir ve uygulama cikista coker.
        self.rfid.kapan()
        self.password.kapan()
        self.face.kapan()
        # Saklanan MySQL oturumunu birak.
        dogrula.kapat()
        self.admin.stop()
        # Paylasimli okuyucu is parcacigini birakmadan cikma: aksi halde
        # aygit grab edilmis halde kalir ve okuyucu bir dahaki acilisa
        # kadar hicbir seye yanit vermez.
        self._rfid_dinle(False)
        self.sync.stop()
        # Kapi donanimi: sensor is parcacigini durdur, kilidi kapat,
        # GPIO pinlerini birak (aksi halde bir sonraki calistirmada
        # "pin already in use" alinir).
        self.kapi.kapan()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)

    win = MainWindow()
    if FULLSCREEN or IS_RASPBERRY_PI:
        win.showFullScreen()
    else:
        win.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
