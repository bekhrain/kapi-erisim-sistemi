"""
Kapi donanimi: selenoid kilit rolesi, buzzer, 3'lu trafik lambasi ve
MC38 manyetik kapi sensoru. Pin haritasi: config.py "KAPI DONANIMI".

Butun cikislar ve sensor tek KapiDonanim nesnesinde toplanir; ekranlar
donanima dokunmaz, main.py cagirir. Cikislar GUI thread'inden surulur,
zamanli isler QTimer ile (bloklama yok). Sensor arka plan QThread
dongusunde okunur ve durum degisince sinyal yayar.

gpiozero yoksa / cihaz Pi degilse mock: pin surulmez, eylemler terminale
yazilir, sensor simule_kapi() ile degistirilir.

Trafik lambasi:
  bosta()           KIRMIZI, kilit kapali
  beklemede()       SARI
  giris_onaylandi() YESIL + kisa bip + kilit KAPI_ACIK_SANIYE sn acik
  giris_reddedildi()KIRMIZI + uzun bip
  kapi acik kaldi   SARI yanip soner + araliklarla bip
  izinsiz acilma    KIRMIZI + alarm bip
"""
import time

from PyQt5.QtCore import QThread, QTimer, pyqtSignal

from app.config import (
    IS_RASPBERRY_PI,
    KAPI_ACIK_KALDI_SANIYE, KAPI_ACIK_SANIYE, KAPI_BUZZER_PIN,
    KAPI_LED_KIRMIZI_PIN, KAPI_LED_SARI_PIN, KAPI_LED_YESIL_PIN,
    KAPI_ONAY_PENCERE_SANIYE, KAPI_ROLE_AKTIF_DUSUK, KAPI_ROLE_PIN,
    KAPI_SENSOR_DEBOUNCE, KAPI_SENSOR_NC, KAPI_SENSOR_PIN,
)

try:
    if IS_RASPBERRY_PI:
        from gpiozero import LED, Button, OutputDevice
    else:
        LED = Button = OutputDevice = None
except ImportError:
    LED = Button = OutputDevice = None

_MOCK = LED is None


class KapiDonanim(QThread):
    """Kapi cevre birimlerinin tek yoneticisi."""

    kapi_durumu_degisti = pyqtSignal(bool)   # True = acik
    kapi_acik_kaldi = pyqtSignal()
    izinsiz_acilma = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

        # Lamba durumu: "bosta" | "beklemede" | "onay" | "red". Otomatik
        # kilitleme, arada baska ekrana gecilmisse LED rengini bozmasin.
        self._durum = "bosta"
        self._son_onay = 0.0   # time.monotonic; izinsiz acilma penceresi icin

        self._kilit_timer = QTimer(self)
        self._kilit_timer.setSingleShot(True)
        self._kilit_timer.timeout.connect(self._otomatik_kilitle)

        self._buzzer_timer = QTimer(self)
        self._buzzer_timer.timeout.connect(self._buzzer_tik)
        self._buzzer_plan = []   # [(sure_sn, ac_mi), ...]

        self._sari_blink = QTimer(self)
        self._sari_blink.timeout.connect(self._sari_tik)
        self._sari_durum = False

        # Sensor dongusu sadece sinyal yayar; buzzer/LED tepkisi buradan
        # GUI thread'inde verilir (QTimer'lar baska thread'den surulemez).
        self.izinsiz_acilma.connect(self._alarm_izinsiz)
        self.kapi_acik_kaldi.connect(self._uyari_acik_kaldi)
        self.kapi_durumu_degisti.connect(self._sensor_durum_tepki)

        self._kur()
        self.bosta()

    # --- GPIO kurulum / kapanis ---

    def _kur(self):
        if _MOCK:
            self._role = self._buzzer = self._sensor = None
            self._led = {"kirmizi": None, "sari": None, "yesil": None}
            print("[kapi] MOCK mod - GPIO surulmuyor")
            return

        # active_high = not AKTIF_DUSUK; initial_value=False -> role birakik
        self._role = OutputDevice(
            KAPI_ROLE_PIN, active_high=not KAPI_ROLE_AKTIF_DUSUK,
            initial_value=False)
        self._buzzer = LED(KAPI_BUZZER_PIN)
        self._led = {
            "kirmizi": LED(KAPI_LED_KIRMIZI_PIN),
            "sari": LED(KAPI_LED_SARI_PIN),
            "yesil": LED(KAPI_LED_YESIL_PIN),
        }
        self._sensor = Button(KAPI_SENSOR_PIN, pull_up=True,
                              bounce_time=KAPI_SENSOR_DEBOUNCE)

    def kapan(self):
        """Thread'i durdur, zamanlayicilari iptal et, pinleri birak."""
        self._running = False
        for t in (self._kilit_timer, self._buzzer_timer, self._sari_blink):
            t.stop()
        self.wait(1500)
        self.kilitle()
        self._buzzer_yaz(False)
        if not _MOCK:
            for d in (self._role, self._buzzer, *self._led.values(),
                      self._sensor):
                try:
                    d.close()
                except Exception:  # noqa: BLE001
                    pass

    # --- Yuksek seviye durumlar (main.py cagirir) ---

    def bosta(self):
        """Menu / bekleme: KIRMIZI, kilit kapali."""
        self._buzzer_plani_durdur()
        self._sari_blink.stop()
        # Onay kilidi hala acik: durumu "onay" birak, sure dolunca
        # _otomatik_kilitle kapatip kirmiziya alsin (erken kilitleme yok).
        if self._kilit_timer.isActive():
            return
        self._durum = "bosta"
        self.kilitle()
        self._sadece_led("kirmizi")

    def beklemede(self):
        """Dogrulama basladi: SARI."""
        self._durum = "beklemede"
        self._sari_blink.stop()
        self._sadece_led("sari")

    def giris_onaylandi(self):
        """Onay: YESIL, kisa bip, kilidi KAPI_ACIK_SANIYE sn ac."""
        self._durum = "onay"
        self._son_onay = time.monotonic()
        self._sari_blink.stop()
        self._buzzer_plani_durdur()
        self._sadece_led("yesil")
        self.kilit_ac()
        self._kilit_timer.start(int(KAPI_ACIK_SANIYE * 1000))
        self._bip_deseni([(0.12, True)])

    def giris_reddedildi(self):
        """Red: KIRMIZI, uzun bip. Kilit acilmaz."""
        self._durum = "red"
        self._sari_blink.stop()
        self._sadece_led("kirmizi")
        self._bip_deseni([(0.7, True)])

    # --- Kilit ---

    def kilit_ac(self):
        if _MOCK:
            print("[kapi] kilit ACILDI")
            return
        self._role.on()

    def kilitle(self):
        if _MOCK:
            print("[kapi] kilit kapali")
            return
        self._role.off()

    def _otomatik_kilitle(self):
        """KAPI_ACIK_SANIYE doldu: kilitle; hala 'onay' ise kirmiziya al."""
        self.kilitle()
        if self._durum == "onay" and not self._sari_blink.isActive():
            self._durum = "bosta"
            self._sadece_led("kirmizi")

    # --- LED ---

    def _sadece_led(self, renk: str):
        if _MOCK:
            print(f"[kapi] LED -> {renk.upper()}")
            return
        for ad, dev in self._led.items():
            dev.on() if ad == renk else dev.off()

    def _sari_tik(self):
        self._sari_durum = not self._sari_durum
        if _MOCK:
            return
        self._led["kirmizi"].off()
        self._led["yesil"].off()
        self._led["sari"].on() if self._sari_durum else self._led["sari"].off()

    # --- Buzzer (bloklamayan desen calici) ---

    def _buzzer_yaz(self, ac: bool):
        if _MOCK:
            if ac:
                print("[kapi] buzzer BIP")
            return
        self._buzzer.on() if ac else self._buzzer.off()

    def _bip_deseni(self, adimlar):
        """adimlar: [(sure_sn, ac_mi), ...] sirayla uygulanir."""
        self._buzzer_plan = list(adimlar)
        self._buzzer_timer.stop()
        self._buzzer_tik()

    def _buzzer_tik(self):
        if not self._buzzer_plan:
            self._buzzer_timer.stop()
            self._buzzer_yaz(False)
            return
        sure, ac = self._buzzer_plan.pop(0)
        self._buzzer_yaz(ac)
        self._buzzer_timer.start(int(sure * 1000))

    def _buzzer_plani_durdur(self):
        self._buzzer_plan = []
        self._buzzer_timer.stop()
        self._buzzer_yaz(False)

    # --- Uyari durumlari (sensor dongusunden) ---

    def _sensor_durum_tepki(self, acik: bool):
        """Kapi kapaninca uyari desenlerini sustur."""
        if not acik:
            self._sari_blink.stop()
            self._buzzer_plani_durdur()

    def _uyari_acik_kaldi(self):
        if not self._sari_blink.isActive():
            self._sari_durum = False
            self._sari_blink.start(500)
        self._bip_deseni([(0.1, True), (0.9, False)] * 5)

    def _alarm_izinsiz(self):
        self._sadece_led("kirmizi")
        self._bip_deseni([(0.15, True), (0.15, False)] * 12)

    # --- MC38 sensor okuma dongusu ---

    def _kapi_acik_mi(self) -> bool:
        if _MOCK:
            return getattr(self, "_mock_acik", False)
        # pull_up: kontak GND'de iken is_pressed True. NC sensorde bu
        # "kapi kapali" demek, o yuzden tersle.
        kontak_kapali = self._sensor.is_pressed
        return (not kontak_kapali) if KAPI_SENSOR_NC else kontak_kapali

    def simule_kapi(self, acik: bool):
        """Mock: sensoru elle ac/kapa."""
        self._mock_acik = bool(acik)

    def run(self):
        self._running = True
        onceki = self._kapi_acik_mi()
        acik_since = time.monotonic() if onceki else 0.0
        acik_kaldi_bildirildi = False

        while self._running:
            self.msleep(100)
            simdi = time.monotonic()
            acik = self._kapi_acik_mi()

            if acik != onceki:
                onceki = acik
                self.kapi_durumu_degisti.emit(acik)
                if acik:
                    acik_since = simdi
                    acik_kaldi_bildirildi = False
                    # Onay penceresi disinda acildi -> zorlama.
                    if (self._son_onay == 0.0
                            or simdi - self._son_onay > KAPI_ONAY_PENCERE_SANIYE):
                        self.izinsiz_acilma.emit()
                continue

            if (acik and not acik_kaldi_bildirildi
                    and simdi - acik_since > KAPI_ACIK_KALDI_SANIYE):
                acik_kaldi_bildirildi = True
                self.kapi_acik_kaldi.emit()
