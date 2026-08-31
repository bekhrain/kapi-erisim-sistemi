"""
RFID okuyucu soyutlamasi (13,56 MHz USB okuyucu).

- Raspberry Pi'de: USB okuyucu KLAVYE taklidi yapar. Kart okununca UID'yi
  tus vurusu olarak yazar ve Enter'a basar. Biz /dev/input altindaki olay
  aygitini dogrudan okuyoruz.
- Windows/masaustunde: mock reader kullanilir; "Kart Simule Et" butonu
  tetikleyene kadar kart okunmaz.

Okuma islemi arka planda bir QThread icinde yapilir; kart bulununca
'card_read(str)' sinyali yayilir. Ekranlar acisindan hicbir sey degismedi.


NEDEN MFRC522 BIRAKILDI
-----------------------
Onceki surum SPI uzerinden MFRC522 modulunu okuyordu. USB okuyucuya
gecince SPI kablolamasi, mfrc522 / RPi.GPIO / spidev bagimliliklari ve
"bir okuyor bir okumuyor" davranisina yol acan Auth adimi tamamen
ortadan kalkti.

DIKKAT - KART NUMARALARI DEGISTI: MFRC522 surumu anticoll'un dondurdugu
5 bayti (BCC dahil) buyuk-endian birlestirip ONDALIK yaziyordu. USB
okuyucu 4 bayti ONALTILIK yaziyor (ornek: e0159808). Ayni kart iki
okuyucuda farkli metin uretir; eski kayitlar eslesmez, kartlar yeniden
okutulup kaydedilmelidir.


NEDEN evdev PAKETI KULLANILMIYOR
--------------------------------
Pi'de internet yok. input_event yapisi sabit ve kucuk; struct ile
cozmek ek bagimliliktan daha az is.


NEDEN AYGIT OZEL MODA ALINIYOR (EVIOCGRAB)
------------------------------------------
Okuyucu isletim sistemi icin siradan bir klavyedir. Grab edilmezse
yazdigi haneler odaktaki pencereye de duser: sifre ekrani acikken kart
okutulunca kart numarasi PIN kutusuna yazilir, admin ekraninda bir
forma dolar, terminal acilsa oraya akar. Grab, o aygitin olaylarini
SADECE bize verir.
"""
import errno
import glob
import os
import struct

from PyQt5.QtCore import QDateTime, QThread, pyqtSignal

from app.config import (IS_RASPBERRY_PI, RFID_AYGIT_DESENI,
                        RFID_HANE_ZAMAN_ASIMI)

# fcntl ve select yalnizca Linux'ta var. Masaustunde (Windows) modul
# yine de ICE AKTARILABILMELI - yoksa gelistirme makinesinde mock kart
# yolu da colur ve hicbir ekran acilmaz.
try:
    import fcntl
    import select
except ImportError:
    fcntl = select = None

# input_event: struct timeval (2 x long) + u16 type + u16 code + s32 value
# Boyut CALISMA ANINDA hesaplaniyor: 'long' 64-bit Linux'ta 8, Windows'ta
# 4 bayt. Sabit yazmak Pi'de her olayi bir kaydirmali okuturdu.
_OLAY_BICIMI = "@llHHi"
_OLAY_BOYU = struct.calcsize(_OLAY_BICIMI)

_EV_KEY = 0x01          # tus olayi
_BASILDI = 1            # value: 0 birakildi, 1 basildi, 2 tekrar

# _IOW('E', 0x90, int) -> aygiti bu surece ozel kilar
_EVIOCGRAB = 0x40044590

# Tarama kodu -> karakter. Okuyucu modeline gore ust sira rakamlarini
# veya sayisal tus takimini kullanabilir; ikisi de karsilaniyor.
_TUSLAR = {
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5",
    7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    30: "a", 48: "b", 46: "c", 32: "d", 18: "e", 33: "f",
    79: "1", 80: "2", 81: "3", 75: "4", 76: "5",
    77: "6", 71: "7", 72: "8", 73: "9", 82: "0",
}
_ENTER = (28, 96)       # KEY_ENTER, KEY_KPENTER


def aygit_bul() -> str:
    """
    Okuyucunun olay aygitini doner, bulunamazsa "".

    Desen by-id altinda arar; oradaki ad cihazin seri numarasindan
    uretildigi icin acilislar arasinda degismez.
    """
    for yol in sorted(glob.glob(RFID_AYGIT_DESENI)):
        return yol
    return ""


class RFIDWorker(QThread):
    """Arka planda karti bekleyen is parcacigi."""
    card_read = pyqtSignal(str)   # okunan kart UID'si
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        # Mock kart kuyrugu burada tanimlanir: start() cagrildiktan hemen
        # sonra simulate_card() cagrilirsa (admin ekrani boyle yapiyor)
        # is parcacigi henuz calismaya baslamamis olabilir; degiskeni
        # run() icinde sifirlarsak o kart kaybolurdu.
        self._mock_uid = None

    def run(self):
        self._running = True
        if IS_RASPBERRY_PI:
            self._run_real()
        else:
            self._run_mock()

    # ---------------- Gercek okuyucu ----------------

    def _run_real(self):
        yol = aygit_bul()
        if not yol:
            self.error.emit(
                "RFID okuyucu bulunamadi (USB'ye takili mi?)")
            return
        try:
            fd = os.open(yol, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            self.error.emit(f"RFID aygiti acilamadi: {e}")
            return

        try:
            fcntl.ioctl(fd, _EVIOCGRAB, 1)
        except OSError as e:
            # Grab basarisizsa okumaya devam ediyoruz ama bu SESSIZ
            # gecilmemeli: haneler odaktaki pencereye de dusecek.
            self.error.emit(f"RFID aygiti ozel moda alinamadi: {e}")

        try:
            self._okuma_dongusu(fd)
        finally:
            try:
                fcntl.ioctl(fd, _EVIOCGRAB, 0)
            except OSError:
                pass
            os.close(fd)

    def _okuma_dongusu(self, fd: int):
        biriken = ""
        son_hane = 0.0
        son_uid = None
        son_zaman = 0

        while self._running:
            # 200 ms'lik dilimler: stop() cagrilinca en gec bu kadar
            # sonra cikilir, aygit kapanmayi beklemez.
            try:
                hazir, _, _ = select.select([fd], [], [], 0.2)
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                self.error.emit(f"RFID okuma hatasi: {e}")
                return
            simdi = QDateTime.currentMSecsSinceEpoch()

            # Yarim kalmis okuma bir sonraki kartin basina yapismasin.
            if biriken and (simdi - son_hane) > RFID_HANE_ZAMAN_ASIMI * 1000:
                biriken = ""

            if not hazir:
                continue

            try:
                veri = os.read(fd, _OLAY_BOYU * 64)
            except OSError as e:
                # Okuyucu cikarildi: dongu biter, kullaniciya soylenir.
                self.error.emit(f"RFID okuyucu koptu: {e}")
                return

            for i in range(0, len(veri) - _OLAY_BOYU + 1, _OLAY_BOYU):
                _, _, tur, kod, deger = struct.unpack(
                    _OLAY_BICIMI, veri[i:i + _OLAY_BOYU])
                if tur != _EV_KEY or deger != _BASILDI:
                    continue
                if kod in _ENTER:
                    uid, biriken = biriken, ""
                    if not uid:
                        continue
                    # Ayni kart elde tutulurken okuyucu tekrar tekrar
                    # yazabilir. 2 saniye icinde ayni UID yok sayilir.
                    if uid != son_uid or simdi - son_zaman > 2000:
                        son_uid, son_zaman = uid, simdi
                        self.card_read.emit(uid)
                    continue
                harf = _TUSLAR.get(kod)
                if harf is None:
                    continue
                son_hane = simdi
                # Cok uzun dizi: bozuk okuma veya baska bir klavye.
                # Sinirsiz biriktirmek bellegi degil ama mantigi bozar.
                if len(biriken) < 32:
                    biriken += harf

    # ---------------- Masaustu (mock) ----------------

    def _run_mock(self):
        """
        Masaustu modu: sinyal disaridan (simulate_card) tetiklenir.

        DONGU KIRILMAZ. Okuyucu ana menude de surekli dinlemede oldugu
        icin, mock surumun de ilk simulasyondan sonra olmemesi gerekiyor;
        aksi halde masaustunde arka arkaya kart okutma senaryosu (ve
        menuye donunce tekrar okutma) test edilemez.
        """
        while self._running:
            if self._mock_uid is not None:
                uid, self._mock_uid = self._mock_uid, None
                self.card_read.emit(uid)
            self.msleep(100)

    def simulate_card(self, uid: str = "e0159808"):
        """Sadece mock modda: sahte bir kart okutuldugunu bildirir."""
        self._mock_uid = uid

    def stop(self):
        self._running = False
        self.wait(1000)
