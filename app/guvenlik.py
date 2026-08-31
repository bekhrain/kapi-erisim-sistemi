"""
Kaba kuvvet (brute force) korumasi.

NEDEN GEREKLI
-------------
6 haneli PIN = 1.000.000 ihtimal. Deneme sayisi sinirsiz birakilirsa
kapinin onune oturup numpad'e basan biri er gec dogru kombinasyonu
bulur; dokunmatik ekranda saniyede birkac deneme yapilabilir. Ust uste
hatali denemeden sonra kisa bir kilit koymak bu saldiriyi pratikte
imkansiz hale getirir (5 denemede 30 sn kilit => gunde ~14.000 deneme,
1.000.000'a ulasmak icin ~70 gun).

Kilit BELLEKTE tutulur, diskte degil. Uygulama yeniden baslatilirsa
sifirlanir; bu kabul edilen bir odundur, cunku alternatif her hatali
denemede SD karta yazmak olurdu.

Kilitli haldeyken de her deneme log'a yazilir: sunucudaki web
arayuzunden "bugun kac red" sayisi artar, saldiri gorunur olur.
"""
import time

from app.config import KILIT_SANIYE, MAX_HATALI_DENEME


class DenemeSayaci:
    """Tek bir giris noktasi (kapi PIN'i, admin PIN'i) icin sayac."""

    def __init__(self, ad: str, max_deneme: int = MAX_HATALI_DENEME,
                 kilit_saniye: int = KILIT_SANIYE):
        self.ad = ad
        self.max_deneme = max_deneme
        self.kilit_saniye = kilit_saniye
        self._hatali = 0
        self._kilit_bitis = 0.0

    # --- Sorgulama ---
    def kilitli_mi(self) -> bool:
        return time.monotonic() < self._kilit_bitis

    def kalan_saniye(self) -> int:
        kalan = self._kilit_bitis - time.monotonic()
        return max(0, int(kalan + 0.999))

    def kalan_hak(self) -> int:
        return max(0, self.max_deneme - self._hatali)

    # --- Bildirim ---
    def basarili(self):
        """Dogru giris: sayac sifirlanir."""
        self._hatali = 0
        self._kilit_bitis = 0.0

    def basarisiz(self) -> bool:
        """
        Hatali giris. Kilit devreye girdiyse True doner.
        """
        self._hatali += 1
        if self._hatali >= self.max_deneme:
            self._kilit_bitis = time.monotonic() + self.kilit_saniye
            self._hatali = 0
            return True
        return False


# Iki giris noktasi ayri sayilir: kapida PIN deneyen birinin admin
# panelini de kilitlemesi istenmez.
kapi_pin = DenemeSayaci("kapi")
admin_pin = DenemeSayaci("admin")
