#!/bin/bash
# =====================================================================
#  Kapi uygulamasini baslatir ve cokerse yeniden ayaga kaldirir.
#  Calistirilacak yer: Raspberry Pi
#
#  Elle:        ~/kapi/pi/kapi-baslat.sh
#  Acilista:    ~/.config/autostart/kapi.desktop bu dosyayi cagirir
# =====================================================================
#
#  NEDEN GOZETIM DONGUSU VAR
#  -------------------------
#  Bu bir kapi. Uygulama coktugunde kimse basinda olmayabilir ve kapi
#  bir sonraki elle mudahaleye kadar calismaz. Dongu, cokusu 3 saniye
#  icinde kapatir.
#
#  NEDEN ARTAN BEKLEME
#  -------------------
#  Hata kalici ise (model dosyasi silinmis, ekran yok) sabit 3 saniyeyle
#  yeniden baslatmak saniyede bir surec dogurup gunlukleri sisirir ve
#  SD kart yazma omrunu tuketir. Ust uste basarisizlikta bekleme
#  60 saniyeye kadar buyur, basarili bir calisma sonrasi sifirlanir.

KOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$HOME/rfidtest"
GUNLUK="$KOK/data/kapi.log"

cd "$KOK" || exit 1

# Sanal ortam: PyQt5, OpenCV ve mysql-connector orada.
if [ -f "$VENV/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
else
    echo "UYARI: sanal ortam yok ($VENV), sistem python'u kullanilacak" \
        | tee -a "$GUNLUK"
fi

bekleme=3
while true; do
    baslangic=$(date +%s)
    echo "--- $(date '+%F %T') uygulama basliyor ---" >> "$GUNLUK"

    python main.py >> "$GUNLUK" 2>&1
    cikis=$?

    sure=$(( $(date +%s) - baslangic ))
    echo "--- $(date '+%F %T') cikti (kod $cikis, $sure sn calisti) ---" \
        >> "$GUNLUK"

    # Duzgun kapanis (kullanici uygulamadan cikti): yeniden baslatma.
    if [ "$cikis" -eq 0 ]; then
        break
    fi

    # 60 saniyeden uzun calistiysa bu gecici bir cokus sayilir;
    # bekleme sifirlanir.
    if [ "$sure" -gt 60 ]; then
        bekleme=3
    fi

    echo "$bekleme saniye sonra yeniden denenecek" >> "$GUNLUK"
    sleep "$bekleme"

    bekleme=$(( bekleme * 2 ))
    [ "$bekleme" -gt 60 ] && bekleme=60
done
