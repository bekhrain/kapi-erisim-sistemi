"""
USB RFID okuyucu testi.

    python kart_test.py

Uretimdeki RFIDWorker'i oldugu gibi kullanir - ayri bir okuma yolu
degil, ayni sinif. Boylece burada calisan sey uygulamada da calisir.

Cikmak icin Ctrl+C.
"""
import signal
import sys

from PyQt5.QtCore import QCoreApplication, QTimer

from app.config import IS_RASPBERRY_PI, RFID_AYGIT_DESENI
from app.hardware import rfid


def main():
    print(f"Raspberry Pi modu : {IS_RASPBERRY_PI}")
    print(f"Aygit deseni      : {RFID_AYGIT_DESENI}")
    yol = rfid.aygit_bul()
    print(f"Bulunan aygit     : {yol or '(YOK)'}")
    if IS_RASPBERRY_PI and not yol:
        print("Okuyucu bulunamadi. USB'ye takili mi?")
        return 1

    uygulama = QCoreApplication(sys.argv)
    isci = rfid.RFIDWorker()
    sayac = {"n": 0}

    def kart(uid):
        sayac["n"] += 1
        print(f"  [{sayac['n']}] KART: {uid}   ({len(uid)} karakter)")

    def hata(mesaj):
        print(f"  HATA: {mesaj}")

    isci.card_read.connect(kart)
    isci.error.connect(hata)
    isci.start()

    print("\nKarti okuyucuya yaklastir. Cikmak icin Ctrl+C.\n")

    # Ctrl+C'nin Qt dongusunde islenebilmesi icin: Python sinyal
    # isleyicileri yorumlayici calisirken tetiklenir, Qt dongusu C
    # tarafinda bekledigi icin bos bir zamanlayici gerekiyor.
    signal.signal(signal.SIGINT, lambda *_: uygulama.quit())
    bosta = QTimer()
    bosta.start(200)
    bosta.timeout.connect(lambda: None)

    kod = uygulama.exec_()
    isci.stop()
    print(f"\nToplam {sayac['n']} kart okundu.")
    return kod


if __name__ == "__main__":
    sys.exit(main())
