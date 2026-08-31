"""
Erisim karari: KARARI SUNUCU VERIR.

NEDEN BOYLE BIR KATMAN
----------------------
Eski surumde kararin tek sahibi yerel SQLite'ti. Bu, ag koptugunda
kapinin calismaya devam etmesini garanti ediyordu ama bir bedeli vardi:
web arayuzunden iptal edilen bir kart, senkronizasyon turu gelene kadar
(en kotu ihtimalle saatlerce) gecmeye devam ediyordu. Erisim
sistemlerinde YETKI IPTALININ GECIKMESI en ciddi acik turudur.

Artik erisim listesinin tek gecerli kopyasi sunucudadir; cihazdaki
kopya tanim geregi gecmise aittir ve KARAR VERMEK ICIN KULLANILMAZ.

    config.YEREL_YEDEK = False (varsayilan)
        Sunucu cevap verene kadar beklenir. Zaman asimi "pes etme"
        degil "bir turu bitirme" suresidir; dolunca yeniden denenir.
        Bekleme yalnizca kullanici "Vazgec"e basinca biter.
        SONUC: ag koptugunda kapi kimseyi iceri almaz. Bu bilincli
        bir tercihtir - yanlis kisiyi iceri almaktansa kimseyi
        almamak yeglenmistir.

    config.YEREL_YEDEK = True
        Eski hibrit davranis: sunucuya ulasilamazsa karar yerel
        SQLite'tan verilir. Kod yolu duruyor, tek satirla acilir.

Bu dosyadaki fonksiyonlar ASLA istisna firlatmaz. Firlatsalardi tek bir
surucu hatasi ekranlarin "mesgul" bayragini temizleyemez ve arayuzu
kilitli birakirdi.

ZAMAN ASIMI KENDIMIZ SAYIYORUZ
------------------------------
mysql.connector'un connection_timeout parametresi SADECE baglanti
kurulumunu sinirlar. Baglanti kurulmus ama sunucu (ornegin disk
kilitlenmesi yuzunden) SELECT'e cevap vermiyorsa o parametre devreye
girmez ve cagri suresiz asilir. Bu yuzden sunucu sorgusu ayri bir
is parcaciginda calistirilip join(DOGRULAMA_TIMEOUT) ile beklenir:
sure dolarsa is parcacigi kaderine birakilir (daemon). Ust sinir
boylece surucunun degil bizim kontrolumuzdedir.

BEKLERKEN NE OLUYOR
-------------------
Bekleme QThread icinde surdugu icin arayuz donmaz; ekran her saniye
"Sunucu bekleniyor... 7 sn" seklinde guncellenir ve "Vazgec" butonu
her an calisir. Iptal, bekleyen sorguyu oldurmez (MySQL surucusunde
guvenli iptal yok) - sadece dongu bir sonraki kontrolde durur ve
sonuc "iptal" olarak yayilir; asili kalan sorgu daemon oldugu icin
uygulamayi tutmaz.

BAGLANTI YENIDEN KULLANIMI
--------------------------
Her kart okumasinda yeni bir MySQL oturumu acmak TCP el sikismasi +
kimlik dogrulama demektir; yerel agda bile 100-300 ms ekler. Acik
baglanti saklanip yeniden kullanilir, koptuysa bir kez yeniden
baglanilir. Sync is parcacigi ile ayni anda cagrilabildigi icin
baglanti bir Lock ile korunur.

LOG DETAYI
----------
Kararin nereden geldigi log'a da yazilir (detay() yardimcisi). Ancak
REDDEDILEN kart kayitlarinda detay alanina SADECE UID yazilir: web
arayuzundeki "son okutulan karti getir" ozelligi bu alani ham UID
olarak okuyor, sonuna "[sunucu]" eklenirse o ozellik bozulur.
"""
import threading
import time

from PyQt5.QtCore import QThread, pyqtSignal

from app import store
from app.config import (DOGRULAMA_BEKLEME_SINIRI, DOGRULAMA_HATA_BEKLEME,
                        DOGRULAMA_TIMEOUT, DOGRULAMA_YENIDEN_DENEME, SERVER_DB,
                        SERVER_HOST, SERVER_PASSWORD, SERVER_PORT, SERVER_USER,
                        YEREL_YEDEK)

try:
    import mysql.connector
except ImportError:  # masaustu gelistirmede kurulu olmayabilir
    mysql = None


# Saklanan sunucu baglantisi ve onu koruyan kilit. Kilit RLock degil
# duz Lock: ic ice kullanim yok, olsaydi da fark edilmesi gerekir.
_kilit = threading.Lock()
_baglanti = None

# Sunucuya en son ne zaman ulasilamadi (monotonic). Bkz.
# DOGRULAMA_HATA_BEKLEME - ard arda gelen kartlarin her birinin
# 2 saniye beklemesini onler.
_son_hata = 0.0


# ---------------- Sonuc sozlugu ----------------

def _sonuc(ok: bool, kisi, kaynak: str, mesaj: str, sebep: str = None) -> dict:
    """
    Ekranlarin bekledigi tek bicimli cevap.

    'kaynak' karari KIMIN verdigini soyler (sunucu / yerel).
    'sebep'  ise ozel bir RET nedenini isaretler (kapali kapi, rol
             yetkisi). Ikisi ayri tutuluyor cunku bir ret hem "yerel
             listeden verildi" hem "rol yetkisi yok" olabilir; tek
             alana sigdirilsaydi biri digerini silerdi.
    """
    return {"ok": ok, "kisi": kisi, "kaynak": kaynak,
            "mesaj": mesaj, "sebep": sebep}


def _kaynak_adi(durum: str) -> str:
    """
    Yerele dusuldugunde kaynak metni (sadece YEREL_YEDEK aciksa).

    Zaman asimi ile "sunucu yok" ayri yazilir: birincisi sunucunun ayakta
    ama yavas oldugunu gosterir, ikincisi agin kopuk oldugunu. Admin
    ekraninda ayni satiri gorseydik arizanin yeri anlasilmazdi.
    """
    if durum == "zaman_asimi":
        return "yerel (sunucu yanit vermedi)"
    return "yerel"


ROL_ADLARI = {"personel": "Personel", "ogrenci": "Ogrenci",
              "misafir": "Misafir"}


def _rol_engeli(rol: str, kaynak: str) -> dict:
    """
    Kisi taninDI ama bu kapiya rolu girmiyorsa reddeden sonuc, degilse None.

    KONTROL NEDEN SORGUNUN ICINDE DEGIL
    -----------------------------------
    "AND rol IN (...)" yazmak daha kisa olurdu ama yanlis kapiya kart
    okutan bir ogrenci o zaman TANINMAMIS gorunurdu. Iki sonucu
    dogururdu:

      1) Kapida "Yetkisiz kart" yazardi - oysa kart gecerli, sadece
         kapi yanlis. Kisi kartinin bozuldugunu sanip idariye giderdi.
      2) Reddedilen kart kayitlarina ham UID yazildigi icin, gecerli
         ogrenci kartlari panelin "Tanimsiz Kartlar" havuzuna aday
         olarak duserdi.

    Once kisi bulunur, sonra rolu bakilir; boylece hem dogru mesaj
    verilir hem kayit dogru etiketlenir.
    """
    try:
        from app import sync as _sync
        izinli = _sync.izinli_roller()
    except Exception as e:  # noqa: BLE001
        # KURAL UYGULANAMADI. Kapi acik birakiliyor cunku alternatifi,
        # kural okunamadigi icin herkesi disarida birakmak olurdu -
        # bir yazilim hatasi yuzunden kapiyi kilitlemek.
        #
        # Ama bu SESSIZ OLMAMALI: bir erisim kuralinin uygulanmamasi,
        # log'a hic iz birakmadan gecerse kimse fark etmez ve kapi
        # aylarca yetkisiz kisileri alir. Onceki surumde 'except:
        # return None' yaziyordu; tam da bu sessizlik.
        print(f"[dogrula] UYARI: rol kurali okunamadi, kural "
              f"UYGULANMADI: {e}")
        return None
    rol = (rol or "personel").strip() or "personel"
    if rol in izinli:
        return None
    return _sonuc(False, None, kaynak,
                  f"{ROL_ADLARI.get(rol, rol)} bu kapidan giremez",
                  sebep="rol")


def _kapi_kapali() -> dict:
    """
    Kapi tamamen kapatildiysa reddeden sonucu uretir, degilse None.

    Kontrol dogrulamanin EN BASINDA yapiliyor: sunucuya sorulmadan,
    yerel listeye bakilmadan. Sonraya birakilsaydi kapali bir kapi
    ag koptugunda yerel listeden karar vermeye devam ederdi.

    sync ICE AKTARIMI FONKSIYON ICINDE: bu modul sync'ten once
    yuklenir (sync -> store -> ... zinciri), tepede yazilsaydi
    dairesel ice aktarim olurdu.
    """
    try:
        from app import sync as _sync
        if not _sync.kapi_kapali_mi():
            return None
    except Exception as e:  # noqa: BLE001
        # Durum okunamadi: kapi calismaya devam eder (bkz. _rol_engeli
        # icindeki ayni gerekce), ama iz birakmadan degil.
        print(f"[dogrula] UYARI: cihaz durumu okunamadi: {e}")
        return None
    return _sonuc(False, None, "kapali", "Kapi kapali", sebep="kapali")


def karar_verildi(sonuc: dict) -> bool:
    """
    Bu sonuc bir KARAR mi, yoksa karar verilememis mi?

    Ayrim log icin sarttir. "Sunucuya ulasilamadi" bir reddetme DEGILDIR;
    o karti yetkisiz kart gibi kaydedersek hem istatistik bozulur hem de
    web arayuzundeki "son okutulan kart" havuzuna sahte bir aday duser.
    """
    return str(sonuc.get("kaynak") or "") not in ("sunucu-yok", "iptal")


def yerel_karar_mi(sonuc: dict) -> bool:
    """Karari yerel veri mi verdi? (demo kart/sifre kacamaklari icin)"""
    return str(sonuc.get("kaynak") or "").startswith("yerel")


def detay(temel: str, sonuc: dict) -> str:
    """
    log_access(detail=...) icin metin uretir.

    BASARILI gecislerde kaynak eklenir ("0123 [sunucu]"), boylece
    kayitlara bakan biri kararin cevrimici mi cevrimdisi mi alindigini
    gorur. BASARISIZ kart denemelerinde metin OLDUGU GIBI birakilir:
    web arayuzu "son okutulan kart" alanini bu ham UID'den okuyor
    (bkz. app/screens/rfid_screen.py).
    """
    kaynak = sonuc.get("kaynak") or "yerel"
    if not sonuc.get("ok"):
        # KAPALI KAPI ISTISNASI. Reddedilen kart kayitlarinda detay
        # ham UID'dir ve web arayuzu bu havuzdan "tanimsiz kartlar"
        # listesini uretiyor. Kapi kapaliyken okutulan kart oraya ham
        # UID olarak yazilsaydi, kapatilmis bir kapida kart okutan
        # herkes "kisiye tanimla" listesine aday olarak duserdi.
        # Onune metin konunca kart deseni tutmaz ve havuza girmez;
        # kayit yine de tutulur, "kapi kapaliyken kac deneme oldu"
        # sorusunun cevabi budur.
        onek = {"kapali": "kapi kapali", "rol": "kapi yetkisi yok"}.get(
            sonuc.get("sebep") or "")
        if onek:
            return f"{onek} ({temel})" if temel else onek
        return temel
    return f"{temel} [{kaynak}]" if temel else f"[{kaynak}]"


# ---------------- Sunucu baglantisi ----------------

def _yeni_baglanti():
    if mysql is None:
        raise RuntimeError("mysql-connector-python kurulu degil")
    return mysql.connector.connect(
        host=SERVER_HOST,
        port=SERVER_PORT,
        user=SERVER_USER,
        password=SERVER_PASSWORD,
        database=SERVER_DB,
        connection_timeout=DOGRULAMA_TIMEOUT,
        # autocommit ZORUNLU. Kapali olsaydi uzun omurlu bu baglanti
        # tek bir REPEATABLE READ islemi icinde kalir ve ilk sorgunun
        # anlik goruntusunu saatlerce tekrar tekrar okurdu: web'den
        # iptal edilen kart cihazda hala gecerli gorunurdu. Tam da
        # onlemek icin ugrastigimiz hata.
        autocommit=True,
    )


def _baglantiyi_at():
    """Bozuk baglantiyi kapatir (kilit CAGIRAN tarafta tutulur)."""
    global _baglanti
    if _baglanti is not None:
        try:
            _baglanti.close()
        except Exception:  # noqa: BLE001 - kapatma hatasi onemsiz
            pass
        _baglanti = None


def _baglanti_al():
    """
    Saklanan baglantiyi doner; kopmussa bir kez yeniden baglanir.

    ping(reconnect=True) tek basina yeterli degil: surucu bazi hata
    turlerinde nesneyi kullanilamaz halde birakir. Ping patlarsa nesne
    tamamen atilip sifirdan baglanilir.
    """
    global _baglanti
    if _baglanti is not None:
        try:
            _baglanti.ping(reconnect=True, attempts=1, delay=0)
            return _baglanti
        except Exception:  # noqa: BLE001
            _baglantiyi_at()
    _baglanti = _yeni_baglanti()
    return _baglanti


def _sunucuya_sor(sorgu):
    """
    sorgu(con) fonksiyonunu sunucuda calistirir.

    Doner: (durum, sonuc)
        "ok"           -> sorgu bitti, sonuc gecerli
        "yok"          -> sunucuya ulasilamadi / hata / surucu yok
        "zaman_asimi"  -> DOGRULAMA_TIMEOUT icinde cevap gelmedi

    Sorgu AYRI bir is parcaciginda kosturulur ve join ile beklenir; boylece
    sure sinirini surucuye degil kendimize birakmis oluruz (bkz. dosya
    basligi). Zaman asiminda is parcacigi arkada calismaya devam edebilir,
    daemon oldugu icin uygulamanin kapanmasini engellemez.
    """
    global _son_hata

    if mysql is None:
        return "yok", None

    # Cihaz panelden HIZMET DISI birakilmissa sunucuya hic sorulmaz.
    # "Hizmet disi" bu projede kapiyi sunucudan koparmak demektir:
    # karar yerel listeden verilir, kayitlar cihazda birikir. Ayni
    # kod yolu ag koptugunda da isliyor; yani panelden bir dugmeyle
    # kablo cekmeden ayni senaryo denenebiliyor.
    #
    # Import BURADA: sync modulu store'u ve PyQt'yi yukluyor, dosya
    # basinda ice aktarilsa iki modul birbirini beklerdi.
    try:
        from app import sync as _sync
        if _sync.cihaz_pasif_mi():
            return "yok", None
    except Exception:  # noqa: BLE001 - bayrak okunamazsa normal akis
        pass

    # Yakin zamanda ulasilamadiysa hic deneme; dogrudan yerele dus.
    # SADECE yedek aciksa: yedek kapaliyken beklemekten baska secenek
    # olmadigi icin denemeyi atlamak sadece bos yere gecikme olurdu.
    if (YEREL_YEDEK and _son_hata
            and (time.monotonic() - _son_hata) < DOGRULAMA_HATA_BEKLEME):
        return "yok", None

    kutu = {}

    def _calis():
        # Kilit zaman asimli alinir: onceki bir sorgu zaman asimina
        # ugrayip kilidi hala tutuyor olabilir. Suresiz beklenirse bu
        # is parcacigi da asilir ve sorun cig gibi buyur.
        if not _kilit.acquire(timeout=DOGRULAMA_TIMEOUT):
            kutu["hata"] = "baglanti mesgul"
            return
        try:
            kutu["sonuc"] = sorgu(_baglanti_al())
        except Exception as e:  # noqa: BLE001 - ag hatasi cesitli olabilir
            kutu["hata"] = e
            _baglantiyi_at()
        finally:
            _kilit.release()

    is_parcacigi = threading.Thread(target=_calis, daemon=True)
    is_parcacigi.start()
    is_parcacigi.join(DOGRULAMA_TIMEOUT)

    if is_parcacigi.is_alive():
        _son_hata = time.monotonic()
        return "zaman_asimi", None
    if "hata" in kutu:
        _son_hata = time.monotonic()
        return "yok", None

    # Basarili tur: bekleme cezasi varsa kaldirilir.
    _son_hata = 0.0
    return "ok", kutu.get("sonuc")


def _bekle(saniye: float, dur) -> bool:
    """
    Kucuk dilimler halinde uyur, arada iptal edilmis mi diye bakar.

    Tek bir time.sleep(1) da yazilabilirdi ama o zaman "Vazgec"e basan
    kullanici bir saniyeye kadar bekletilirdi; dokunmatik ekranda
    tepki vermeyen buton "bozuldu" demektir. Doner: iptal edildi mi.
    """
    bitis = time.monotonic() + saniye
    while time.monotonic() < bitis:
        if dur is not None and dur():
            return True
        time.sleep(0.05)
    return bool(dur is not None and dur())


def _sunucudan_iste(sorgu, dur=None, ilerleme=None):
    """
    Sunucuya sorar; YEREL_YEDEK kapaliyken CEVAP GELENE KADAR yeniden dener.

    Doner: (durum, sonuc)
        "ok"         -> sunucu cevapladi
        "yok"/"zaman_asimi" -> sadece YEREL_YEDEK aciksa doner (yerele dus)
        "iptal"      -> kullanici vazgecti
        "vazgecildi" -> DOGRULAMA_BEKLEME_SINIRI doldu (0 ise hic olmaz)

    dur():      True donerse bekleme birakilir (iptal)
    ilerleme(n): her turda gecen saniye; ekran metnini gunceller
    """
    baslangic = time.monotonic()
    while True:
        durum, veri = _sunucuya_sor(sorgu)
        if durum == "ok":
            return "ok", veri

        # Yedek aciksa ilk basarisizlikta yerele dusulur; beklemeyiz.
        if YEREL_YEDEK:
            return durum, None

        if dur is not None and dur():
            return "iptal", None

        gecen = time.monotonic() - baslangic
        if DOGRULAMA_BEKLEME_SINIRI and gecen >= DOGRULAMA_BEKLEME_SINIRI:
            return "vazgecildi", None

        if ilerleme is not None:
            try:
                ilerleme(int(gecen))
            except Exception:  # noqa: BLE001 - ekran guncellemesi karari bozmasin
                pass

        if _bekle(DOGRULAMA_YENIDEN_DENEME, dur):
            return "iptal", None


def _sunucu_yok(mesaj: str) -> dict:
    """Karar VERILEMEDI. Kapi acilmaz ama bu bir reddetme de degildir."""
    return _sonuc(False, None, "sunucu-yok", mesaj)


# ---------------- Sunucu sorgulari ----------------
#
# Her ikisinde de "yetkili_mi = TRUE" kosulu SQL'in icindedir. Kosulu
# cihaz tarafinda filtrelemek yanlis olurdu: yetkisi kaldirilmis birinin
# kaydinin cihaza kadar gelmesine hic gerek yok.

def _kart_sorgusu(con, uid: str):
    imlec = con.cursor()
    try:
        imlec.execute(
            "SELECT id, ad_soyad, rol FROM personel"
            " WHERE kart_uid = %s AND yetkili_mi = TRUE LIMIT 1",
            (uid,),
        )
        satir = imlec.fetchone()
    finally:
        imlec.close()
    return ({"sunucu_id": satir[0], "ad": satir[1] or "-",
             "rol": satir[2] or "personel"} if satir else None)


def _yuz_listesi_sorgusu(con):
    """
    Yetkili personelin yuz vektorlerini ceker.

    KARSILASTIRMA NEDEN CIHAZDA (PIN ile ayni gerekce, bir fark ile)
    ----------------------------------------------------------------
    Kosinus benzerligini SQL icinde hesaplamak mumkun degil; 128
    carpma-toplama gerekiyor. Ayrica kameradan gelen yuzun vektoru
    (probe) CIHAZDAN CIKMAMALI - disari cikan tek sey karsilastirmanin
    sonucu olsun.

    Kararin sunucu kaynakli olma amaci bozulmaz: yetkisi kaldirilan
    kisinin vektoru bu listeye hic girmez, dolayisiyla eslesemez.

    Kayit basina ~700 bayt; 50 kisilik listede ~35 KB. Kart sorgusundan
    agir ama yuz girisi kartla kiyaslanmayacak kadar seyrek kullanilir.
    """
    imlec = con.cursor()
    try:
        imlec.execute(
            "SELECT id, ad_soyad, yuz_vektor, rol FROM personel"
            " WHERE yetkili_mi = TRUE AND yuz_vektor IS NOT NULL"
            " AND yuz_vektor <> ''"
        )
        return [(r[0], r[1] or "-", r[2], r[3] or "personel")
                for r in imlec.fetchall()]
    finally:
        imlec.close()


def _pin_listesi_sorgusu(con):
    """
    Yetkili personelin PIN ozetlerini ceker.

    KARSILASTIRMA NEDEN CIHAZDA
    ---------------------------
    pin_ozeti sutunu "pbkdf2$tur$salt$hash" bicimindedir ve her kisinin
    salt'i farklidir. SQL icinde PBKDF2 hesaplanamayacagi icin sunucuya
    "bu PIN kimin?" diye sorulamaz. Duz PIN'i sunucuya gondermek ise
    zaten kabul edilemez - hash'lemenin butun amaci PIN'in cihazdan
    disari cikmamasidir.

    Bu, kararin "sunucu kaynakli" olma amacini bozmaz: GECERLI KISILER
    LISTESI sunucudan geliyor, yani web'den iptal edilen bir PIN bu
    listeye hic girmiyor. Cihazda yapilan sey sadece aritmetik.
    """
    imlec = con.cursor()
    try:
        imlec.execute(
            "SELECT id, ad_soyad, pin_ozeti, rol FROM personel"
            " WHERE yetkili_mi = TRUE AND pin_ozeti IS NOT NULL"
            " AND pin_ozeti <> ''"
        )
        return [(r[0], r[1] or "-", r[2], r[3] or "personel")
                for r in imlec.fetchall()]
    finally:
        imlec.close()


# ---------------- Sunucu kisisini yerel kisiye baglama ----------------

def _yerel_kisi(sunucu_id: int, ad: str) -> dict:
    """
    Sunucudaki personel.id icin yerel users.id bulur.

    Gerekce: log_access yerel bir user_id bekliyor (logs.user_id ->
    users.id). Sunucudan gelen id baska bir sayi uzayidir. Eslesme
    users.sunucu_id sutunuyla kurulur.

    Eslesme YOKSA (kisi sunucuda yeni eklenmis, cihaz henuz
    senkronize olmamis) id None birakilir. Kapi yine acilir ve kayit
    yine tutulur - sadece kimlik bagi eksik kalir. Alternatif, tanimadigi
    icin kisiyi geri cevirmek olurdu; hibrit tasarimin amaci tam olarak
    bunu yapmamak.
    """
    try:
        with store._db() as con:
            satir = con.execute(
                "SELECT id, name FROM users"
                " WHERE sunucu_id = ? AND silindi = 0 LIMIT 1",
                (int(sunucu_id),),
            ).fetchone()
        if satir is not None:
            return {"id": satir["id"], "name": satir["name"] or ad}
    except Exception:  # noqa: BLE001 - yerel okuma hatasi kapiyi kapatmasin
        pass
    return {"id": None, "name": ad}


# ---------------- Disari acilan arayuz ----------------

def kart_dogrula(uid: str, dur=None, ilerleme=None) -> dict:
    """Kart UID'sine gore erisim karari. ASLA istisna firlatmaz."""
    try:
        kapali = _kapi_kapali()
        if kapali is not None:
            return kapali
        uid = (uid or "").strip()
        if not uid:
            return _sonuc(False, None, "sunucu", "Kart okunamadi")

        durum, satir = _sunucudan_iste(
            lambda con: _kart_sorgusu(con, uid), dur, ilerleme)

        if durum == "ok":
            # Sunucu konustu: cevabi -olumsuz olsa bile- baglayicidir.
            # Yerele ikinci bir sans icin bakilmaz; bakilsaydi web'den
            # iptal edilen kart, henuz senkronize olmamis yerel kayit
            # sayesinde gecmeye devam ederdi.
            if satir is None:
                # KART NUMARASI EKRANDA YAZILMAZ. Kapi ekrani herkese
                # acik bir yerde duruyor; reddedilen her kartin numarasi
                # ekranda gorunuyorsa, yanindan gecen biri baskasinin
                # kart numarasini okuyabilir. Numara KAYDA yaziliyor
                # (bkz. rfid_screen), panelde tanimlama icin oradan
                # okunuyor - yani islevden bir sey kaybedilmiyor.
                return _sonuc(False, None, "sunucu", "Yetkisiz kart")
            engel = _rol_engeli(satir.get("rol"), "sunucu")
            if engel is not None:
                return engel
            kisi = _yerel_kisi(satir["sunucu_id"], satir["ad"])
            return _sonuc(True, kisi, "sunucu",
                          f"Hos geldiniz, {kisi['name']}")

        if durum == "iptal":
            return _sonuc(False, None, "iptal", "Islem iptal edildi")
        if not YEREL_YEDEK:
            return _sunucu_yok("Sunucuya ulasilamiyor, tekrar deneyin")
        return _yerel_kart(uid, _kaynak_adi(durum))
    except Exception:  # noqa: BLE001 - dogrulama hicbir sekilde patlamamali
        if not YEREL_YEDEK:
            return _sunucu_yok("Dogrulama yapilamadi")
        return _yerel_kart((uid or "").strip(), "yerel")


def _yerel_kart(uid: str, kaynak: str) -> dict:
    try:
        kisi = store.find_by_card(uid)
    except Exception:  # noqa: BLE001
        kisi = None
    if kisi is not None:
        engel = _rol_engeli(kisi.get("rol"), kaynak)
        if engel is not None:
            return engel
        return _sonuc(True, kisi, kaynak, f"Hos geldiniz, {kisi['name']}")
    return _sonuc(False, None, kaynak, "Yetkisiz kart")


def pin_dogrula(pin: str, dur=None, ilerleme=None) -> dict:
    """PIN'e gore erisim karari. ASLA istisna firlatmaz."""
    try:
        kapali = _kapi_kapali()
        if kapali is not None:
            return kapali
        pin = (pin or "").strip()
        if not pin:
            return _sonuc(False, None, "sunucu", "PIN girilmedi")

        durum, kayitlar = _sunucudan_iste(
            _pin_listesi_sorgusu, dur, ilerleme)

        if durum == "ok":
            eslesen = _pin_esle(pin, kayitlar or [])
            if eslesen is None:
                return _sonuc(False, None, "sunucu", "Hatali sifre")
            engel = _rol_engeli(eslesen[2], "sunucu")
            if engel is not None:
                return engel
            kisi = _yerel_kisi(eslesen[0], eslesen[1])
            return _sonuc(True, kisi, "sunucu",
                          f"Hos geldiniz, {kisi['name']}")

        if durum == "iptal":
            return _sonuc(False, None, "iptal", "Islem iptal edildi")
        if not YEREL_YEDEK:
            return _sunucu_yok("Sunucuya ulasilamiyor, tekrar deneyin")
        return _yerel_pin(pin, _kaynak_adi(durum))
    except Exception:  # noqa: BLE001
        if not YEREL_YEDEK:
            return _sunucu_yok("Dogrulama yapilamadi")
        return _yerel_pin((pin or "").strip(), "yerel")


def _pin_esle(pin: str, kayitlar: list):
    """
    Sunucudan gelen ozet listesinde PIN'i arar; (sunucu_id, ad, rol) doner.

    Her kayit icin ayri bir PBKDF2 hesabi (100.000 tur) gerekir; 50
    kisilik listede bu birkac saniye tutabilir. Bu yuzden bu fonksiyon
    ARAYUZ IS PARCACIGINDA CAGRILMAZ - DogrulamaWorker uzerinden
    calisir. Eslesme bulununca donulur, boylece ortalama maliyet
    listenin yarisidir.
    """
    # Kayitlar konum ile okunuyor, cozerek DEGIL: liste zamanla dorduncu
    # bir alan kazandi (rol) ve sabit sayida cozme her eklemede
    # patlardi.
    for kayit in kayitlar:
        sunucu_id, ad, ozet = kayit[0], kayit[1], kayit[2]
        pin_hash, salt, tur = store.decode_pin(ozet or "")
        if not pin_hash:
            continue
        if store._verify_pin(pin, pin_hash, salt, tur):
            return sunucu_id, ad, (kayit[3] if len(kayit) > 3 else "personel")
    return None


def _yerel_pin(pin: str, kaynak: str) -> dict:
    try:
        kisi = store.find_by_pin(pin)
    except Exception:  # noqa: BLE001
        kisi = None
    if kisi is not None:
        engel = _rol_engeli(kisi.get("rol"), kaynak)
        if engel is not None:
            return engel
        return _sonuc(True, kisi, kaynak, f"Hos geldiniz, {kisi['name']}")
    return _sonuc(False, None, kaynak, "Hatali sifre")


def yuz_dogrula(vektor_b64: str, dur=None, ilerleme=None) -> dict:
    """
    Kameradan cikarilan yuz vektorune gore erisim karari.

    Girdi ZATEN VEKTORDUR: kare ve model islemleri ekran tarafinda
    (FaceScreen) bitirilmis olur. Boylece bu dosya OpenCV'ye hic
    bagimli olmaz ve kart/PIN yolu, modeller eksik olsa bile calisir.

    ASLA istisna firlatmaz.
    """
    try:
        kapali = _kapi_kapali()
        if kapali is not None:
            return kapali
        vektor_b64 = (vektor_b64 or "").strip()
        if not vektor_b64:
            return _sonuc(False, None, "sunucu", "Yuz okunamadi")

        durum, kayitlar = _sunucudan_iste(
            _yuz_listesi_sorgusu, dur, ilerleme)

        if durum == "ok":
            return _yuz_esle(vektor_b64, kayitlar or [], "sunucu")

        if durum == "iptal":
            return _sonuc(False, None, "iptal", "Islem iptal edildi")
        if not YEREL_YEDEK:
            return _sunucu_yok("Sunucuya ulasilamiyor, tekrar deneyin")
        return _yerel_yuz(vektor_b64, _kaynak_adi(durum))
    except Exception:  # noqa: BLE001
        if not YEREL_YEDEK:
            return _sunucu_yok("Dogrulama yapilamadi")
        return _yerel_yuz((vektor_b64 or "").strip(), "yerel")


def _yuz_esle(vektor_b64: str, kayitlar: list, kaynak: str) -> dict:
    """
    Vektoru listeyle karsilastirir.

    yuz modulu burada GEC import edilir: OpenCV kurulu olmayan bir
    ortamda dogrula.py'nin tamami import edilemez hale gelirdi ve
    kartla giris de calismazdi.
    """
    from app import yuz as yuz_modulu

    eslesen, skor, sebep = yuz_modulu.en_yakin(vektor_b64, kayitlar)
    if eslesen is None:
        # Skor MESAJA YAZILMAZ. Kapidaki ekranda "0.38 benzerlik"
        # gostermek, deneyerek esigi arayan birine geri bildirim
        # vermek olur; skor sadece log'a girer.
        return _sonuc(False, None, kaynak, "Yuz taninmadi")

    # Rol kontrolu HEM sunucu HEM yerel yolu kapsiyor: ikisi de bu
    # fonksiyondan geciyor, ayri ayri yazilsaydi biri unutulurdu.
    engel = _rol_engeli(eslesen[3] if len(eslesen) > 3 else "personel", kaynak)
    if engel is not None:
        return engel

    if kaynak == "sunucu":
        kisi = _yerel_kisi(eslesen[0], eslesen[1])
    else:
        kisi = {"id": eslesen[0], "name": eslesen[1]}
    return _sonuc(True, kisi, kaynak, f"Hos geldiniz, {kisi['name']}")


def _yerel_yuz(vektor_b64: str, kaynak: str) -> dict:
    try:
        kayitlar = store.yuz_kayitlari()
    except Exception:  # noqa: BLE001
        kayitlar = []
    return _yuz_esle(vektor_b64, kayitlar, kaynak)


def kapat():
    """
    Saklanan sunucu baglantisini kapatir (uygulama kapanirken).

    Cagrilmasa da olur - surec bitince soket zaten kapanir - ama
    sunucudaki 'wait_timeout' suresince asili kalan oturum birakmamak
    daha temiz.
    """
    with _kilit:
        _baglantiyi_at()


# ---------------- Arayuz is parcacigi ----------------

class DogrulamaWorker(QThread):
    """
    Dogrulamayi arka planda yapar.

    Uc ayri sebeple zorunludur:
      1) AG islemi. YEREL_YEDEK kapaliyken bekleme SINIRSIZ olabilir;
         arayuz is parcaciginda calistirilsaydi ekran tamamen donar,
         "Vazgec" butonu bile isleyemezdi.
      2) PBKDF2 hesabi. Kayitli her PIN icin 100.000 turluk hesap
         yapilir; 50 kisilik listede saniyeler surer.
      3) Iptal edilebilirlik. Bekleme bir dongude oldugu icin
         iptal() ile disaridan durdurulabilir.

    Sinyal her kosulda TAM OLARAK BIR KEZ yayilir; ekranlar "mesgul"
    bayragini bu sinyalle temizledigi icin yayilmazsa arayuz kilitli
    kalirdi. Iptal edildiginde bile sonuc yayilir ("iptal" kaynagiyla).
    """
    tamamlandi = pyqtSignal(dict)   # kart_dogrula/pin_dogrula sozlugu
    bekliyor = pyqtSignal(int)      # sunucu beklenirken gecen saniye

    def __init__(self, yontem: str, deger, parent=None):
        super().__init__(parent)
        self._yontem = yontem
        self._deger = deger
        # threading.Event, QThread'in kendi bayraklarindan daha guvenli:
        # sorgu dongusu Qt'nin haberi olmadan calisan duz Python kodu.
        self._iptal = threading.Event()

    def iptal(self):
        """
        Beklemeyi birak. Devam eden MySQL sorgusunu OLDURMEZ (surucude
        guvenli iptal yok); dongu bir sonraki kontrolde durur, asili
        sorgu daemon is parcaciginda kaderine birakilir.
        """
        self._iptal.set()

    def run(self):
        dur = self._iptal.is_set
        try:
            # Deger bir FONKSIYON olabilir; oyleyse burada, yani arka
            # planda hesaplanir. Yuz tanimada vektor cikarmak ~50 ms
            # surer ve arayuz is parcaciginda yapilirsa kamera
            # onizlemesi her denemede takilir.
            if callable(self._deger):
                self._deger = self._deger()
            if self._yontem == "kart":
                sonuc = kart_dogrula(self._deger, dur, self.bekliyor.emit)
            elif self._yontem == "pin":
                sonuc = pin_dogrula(self._deger, dur, self.bekliyor.emit)
            elif self._yontem == "yuz":
                sonuc = yuz_dogrula(self._deger, dur, self.bekliyor.emit)
            else:
                sonuc = _sunucu_yok(f"Bilinmeyen yontem: {self._yontem}")
        except Exception:  # noqa: BLE001 - sinyal mutlaka yayilmali
            sonuc = _sunucu_yok("Dogrulama yapilamadi")
        self.tamamlandi.emit(sonuc)
