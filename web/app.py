"""
Kapi Erisim Sistemi - Web Yonetim Arayuzu

SUNUCUDA calisir (Pi'de degil). Pi kapiya monte edilmis kucuk bir cihaz;
personel eklemek, kayitlari incelemek gibi isler klavye ve buyuk ekran
ister. Ayrica kontrol icin gelindiginde sunucuyu yanina goturmek yerine
tarayicidan bu sayfaya bakilir.

Bu arayuz dogrudan merkezi MySQL veritabanina yazar. Pi 60 saniyede bir
bu veritabanini okudugu icin buradan eklenen bir kart en gec bir dakika
sonra kapida gecerli olur.

Calistirma:
    pip install flask mysql-connector-python
    python app.py
    Tarayici: http://<sunucu-ip>:5000
"""
import hashlib
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

import mysql.connector
from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)

# Yuz tanima Pi ile AYNI modulu kullanir: ayni model, ayni hizalama,
# ayni vektor. Ayri bir kopya yazilsaydi iki taraf zamanla ayrisir ve
# sunucuda kaydedilen yuz kapida taninmaz hale gelirdi.
#
# Proje koku YUKARI DOGRU ARANIR. Sabit olarak "bir ust klasor" demek
# kuruluma bagimlilik yaratirdi: bu dosya sunucuda C:\kapi-web\app.py
# olarak da durabiliyor, o zaman bir ust klasor C:\ olur ve yuz modulu
# sessizce bulunamaz - web'den eklenen kimse yuzuyle giremez, hicbir
# hata da gorunmez. Aranan isaret: app/yuz.py dosyasinin varligi.
import sys


def _proje_koku() -> str:
    yol = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.exists(os.path.join(yol, "app", "yuz.py")):
            return yol
        ust = os.path.dirname(yol)
        if ust == yol:
            break
        yol = ust
    return ""


_KOK = _proje_koku()
if _KOK:
    sys.path.insert(0, _KOK)
try:
    from app import yuz as _yuz
    from app.config import YUZ_SFACE_DOSYA as YUZ_MODEL_ADI
except Exception:  # noqa: BLE001 - OpenCV/numpy sunucuda olmayabilir
    _yuz = None
    YUZ_MODEL_ADI = ""

# ---------------- Ayarlar ----------------
# Web arayuzu kendi veritabani kullanicisini kullanir. Cihazin
# (kapi_cihaz) yetkisi kasten dardir: SELECT/INSERT ve sadece cihaz
# tablosunda UPDATE. Silme ve guncelleme yetkisi web'e aittir.
DB = {
    "host": os.environ.get("KAPI_DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("KAPI_DB_PORT", "3306")),
    "user": os.environ.get("KAPI_WEB_USER", "kapi_web"),
    "password": os.environ.get("KAPI_WEB_PASSWORD", "web1234"),
    "database": os.environ.get("KAPI_DB_NAME", "kapi_sistemi"),
}

# Panele giris sifresi.
# Ortam degiskeninden okunur; yoksa gelistirme varsayilanina duser.
# Uretimde:  set KAPI_PANEL_PAROLA=...  (kaynak koda yazilmaz)
PANEL_PAROLA = os.environ.get("KAPI_PANEL_PAROLA", "admin1234")

# Panele giris denemesi sinirlama (IP basina).
# Web paneli personel ekleyip kart yetkilendirebiliyor; tek parolayla
# korunan bir sayfada deneme sinirsiz birakilirsa sozluk saldirisi
# dakikalar icinde sonuc verir.
GIRIS_MAX_DENEME = 5
GIRIS_KILIT_SANIYE = 60
_giris_denemeleri = {}   # {ip: [hatali_sayisi, kilit_bitis_zamani]}

# Pi ile bu kadar saat gorusulemezse cihaz "cevrimdisi" gosterilir
CIHAZ_UYARI_SAAT = 1

# ---- Calisma suresi / kesinti takibi ----
# Panel dakikada bir veritabanina "ayaktayim" damgasi atar. Damgalar
# arasindaki BOSLUK, panelin (dolayisiyla cogu zaman sunucunun) kapali
# kaldigi suredir. Sunucu kapaliyken kimse kayit tutamayacagi icin
# kesinti ancak bu dolayli yoldan olculebilir.
NABIZ_SANIYE = 60

# Genel Durum sayfasinin kendini tazeleme araligi (saniye).
# Panel, sunucu acikken surekli ekranda duran bir izleme sayfasi:
# kart okutulmasi ile ekrana dusmesi arasindaki gecikme goze
# carpmamali. Tek kullanicili ve yerel agda calisan bir sayfa icin
# saniyede bir istek onemsiz bir yuk; istek ust uste binmesin diye
# tarayici tarafinda bir "hala suruyor" bayragi tutuluyor
# (bkz. panel.html).
YENILEME_SANIYE = 1

# Cihaz bu kadar SANIYEDIR gorunmuyorsa KESINTI kaydi acilir.
#
# Pi 4 saniyede bir "hayattayim" damgasi atiyor (app/config.py,
# DAMGA_SANIYE). Esik iki bucuk damga: ard arda ucu birden kacarsa
# baglanti kopmus demektir.
#
# Neden damga araligina ESIT degil: o zaman tek bir gecikmis damga -
# ki agda kisa bir tikanma yeterli - kesinti sayilir ve tablo
# gercekte yasanmamis kayitlarla dolardi. 19 Agustos sabahi tam
# olarak bu temizlendi.
#
# 10 saniye, "olculebilir en kisa kesinti" olarak SECILDI. Daha da
# kisaltmak icin once Pi'deki damga araligi kisaltilmali; esigi tek
# basina dusurmek sadece sahte kayit uretir.
#
# CIHAZ_UYARI_SAAT'ten ayri tutuluyor: o panelde rozet rengini
# belirler (goze carpsin diye genis), bu ise gecmise yazilan kalici
# kaydi belirler (dogru olsun diye dar).
#
# 10 -> 6 (20.08.2026), Pi'deki damga araligi 4'ten 2'ye indirildigi
# icin. GUVENLIK PAYI KORUNDU, hatta arttirildi: eskiden 10/4 = 2,5
# kat, simdi 6/2 = 3 kat. Esik tek basina dusurulseydi pay daralir ve
# agdaki ufak bir takilma sahte kesinti uretirdi - 19.08.2026'da
# temizledigimiz uydurma kayitlarin sebebi buydu.
CIHAZ_KOPUK_SANIYE = 6

# Kesinti kontrolunun sikligi. Sunucunun kendi nabiz damgasi 60
# saniyede bir yaziliyor ama kontrol cok daha sik donuyor: kontrol
# de 60 saniyede olsaydi 10 saniyelik esigin hicbir anlami kalmaz,
# kopma yine en gec bir dakika sonra fark edilirdi.
KESINTI_KONTROL_SANIYE = 3

# Panel surecinin ayaga kalktigi an ve bu calisma diliminin
# sunucu_oturum tablosundaki satir numarasi.
PANEL_BASLANGIC = datetime.now()
_oturum_id = None

# PIN ozeti Pi'deki app/store.py ile AYNI formatta uretilmeli,
# yoksa web'den eklenen PIN kapida calismaz.
PBKDF2_ITERATIONS = 100_000

# Personel fotograflari. Yuz TANIMA icin degil; listede ve gecis
# kayitlarinda kimin gectigini gozle dogrulamak icin kullanilir.
FOTO_KLASOR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "static", "fotograf")
IZINLI_UZANTI = {".jpg", ".jpeg", ".png"}

# Kapida okutulan bilinmeyen bir kartin forma dusmesi icin taninan sure.
# Kisa tutuluyor: baskasinin kartini yanlislikla almamak icin.
KART_YAKALAMA_DAKIKA = 10

app = Flask(__name__)
# KAPI_SECRET verilmezse her baslatmada yeni anahtar uretilir; bu
# durumda sunucu yeniden baslatilinca acik oturumlar dusser (guvenli
# taraf). Kalici oturum isteniyorsa ortam degiskeni tanimlanmali.
app.secret_key = os.environ.get("KAPI_SECRET", secrets.token_hex(32))
# Yuklenen dosya boyut siniri (5 MB)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# Oturum cerezi sertlestirmesi
app.config.update(
    # JavaScript cerezi okuyamaz: XSS ile oturum calinamaz.
    SESSION_COOKIE_HTTPONLY=True,
    # Cerez baska sitelerden gelen isteklere eklenmez: CSRF'i buyuk
    # olcude engeller (form disi POST'lar oturumsuz kalir).
    SESSION_COOKIE_SAMESITE="Strict",
    # HTTPS'e gecilince True yapilmali; su an HTTP oldugu icin
    # ortam degiskeniyle acilabilir birakildi.
    SESSION_COOKIE_SECURE=os.environ.get("KAPI_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)


# ---------------- Yardimcilar ----------------

def baglan():
    return mysql.connector.connect(**DB)


def sorgula(sql, params=(), tek=False):
    """SELECT calistirir, sozluk listesi doner."""
    con = baglan()
    imlec = con.cursor(dictionary=True)
    imlec.execute(sql, params)
    sonuc = imlec.fetchone() if tek else imlec.fetchall()
    imlec.close()
    con.close()
    return sonuc


def calistir(sql, params=()):
    """INSERT/UPDATE/DELETE calistirir, etkilenen id'yi doner."""
    con = baglan()
    imlec = con.cursor()
    imlec.execute(sql, params)
    con.commit()
    yeni_id = imlec.lastrowid
    imlec.close()
    con.close()
    return yeni_id


def pin_ozetle(pin: str) -> str:
    """PIN'i Pi ile ayni formatta hash'ler: pbkdf2$iter$salt$hash"""
    if not pin.strip():
        return ""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.strip().encode("utf-8"), bytes.fromhex(salt),
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def foto_kaydet(dosya, kisi_id: int) -> str:
    """
    Yuklenen fotografi static/fotograf/<id>.<uzanti> olarak kaydeder ve
    veritabanina yazilacak goreli yolu doner. Dosya yoksa "" doner.

    Dosya adi kullanicidan alinmaz, kisi id'sinden uretilir; boylece
    ".." veya "C:\\..." gibi yol denemeleri bastan imkansiz olur.
    """
    if not dosya or not dosya.filename:
        return ""
    uzanti = os.path.splitext(dosya.filename)[1].lower()
    if uzanti not in IZINLI_UZANTI:
        raise ValueError("Sadece JPG ve PNG dosyasi yuklenebilir.")
    os.makedirs(FOTO_KLASOR, exist_ok=True)
    # Ayni kisinin eski fotografi baska uzantidaysa temizle
    for eski in IZINLI_UZANTI:
        yol = os.path.join(FOTO_KLASOR, f"{kisi_id}{eski}")
        if os.path.exists(yol):
            try:
                os.remove(yol)
            except OSError:
                pass
    dosya.save(os.path.join(FOTO_KLASOR, f"{kisi_id}{uzanti}"))
    return f"fotograf/{kisi_id}{uzanti}"


def yuz_vektoru_cikar(kisi_id: int) -> str:
    """
    Kaydedilen fotograftan SFace kimlik vektorunu cikarir; base64 doner.

    NEDEN SUNUCUDA DA HESAPLIYORUZ
    ------------------------------
    Personel cogunlukla buradan, fotograf yukleyerek ekleniyor. Vektor
    yalnizca Pi'nin admin ekraninda uretilseydi, web'den eklenen kisi
    kapida yuzuyle GIREMEZDI - ve bunu hicbir hata mesaji soylemezdi;
    listede "Yuz" yazar, kapida taninmaz.

    Model dosyalari HER IKI makinede AYNI olmali. Ayni model ayni
    vektoru urettigi icin, kayit sunucuda yapilip dogrulama Pi'de
    yapilabilir. Farkli surum modeller sessizce uyusmayan sayilar
    uretir; bu yuzden hangi modelle uretildigi yuz_model sutununa
    yazilir.

    OpenCV veya model dosyalari sunucuda yoksa "" doner: fotograf yine
    kaydedilir, sadece yuzle giris tanimlanmaz ve kullaniciya soylenir.
    """
    if _yuz is None:
        return ""
    yol = ""
    for uzanti in IZINLI_UZANTI:
        aday = os.path.join(FOTO_KLASOR, f"{kisi_id}{uzanti}")
        if os.path.exists(aday):
            yol = aday
            break
    if not yol:
        return ""
    try:
        import cv2
        goruntu = cv2.imread(yol)
        if goruntu is None:
            return ""
        bulunan = _yuz.yuz_bul(goruntu)
        if bulunan is None:
            return ""
        return _yuz.vektor_cikar(goruntu, bulunan)
    except Exception:  # noqa: BLE001 - fotograf yuklemesi bu yuzden patlamasin
        return ""


def yuzu_isle(kisi_id: int, ad: str):
    """Fotograftan vektor cikarip veritabanina yazar; sonucu flash eder."""
    vektor = yuz_vektoru_cikar(kisi_id)
    if vektor:
        calistir(
            "UPDATE personel SET yuz_vektor = %s, yuz_model = %s,"
            " yuz_kayitli = TRUE WHERE id = %s",
            (vektor, YUZ_MODEL_ADI, kisi_id))
        flash(f"{ad}: yuz tanima tanimlandi.", "basari")
    else:
        # Bayrak da dusurulur: "yuz kayitli" deyip kapida tanimamak,
        # hicbir sey dememekten daha kotudur.
        calistir(
            "UPDATE personel SET yuz_vektor = NULL, yuz_kayitli = FALSE"
            " WHERE id = %s", (kisi_id,))
        if _yuz is None:
            flash("Fotograf kaydedildi ama yuz tanima bu sunucuda kurulu"
                  " degil (OpenCV / model dosyalari eksik).", "hata")
        else:
            flash("Fotografta yuz bulunamadi; yuzle giris tanimlanmadi."
                  " Yuzun net gorundugu bir fotograf deneyin.", "hata")


def bekleyen_kartlar():
    """
    Kapida okutulmus ama hicbir kisiye ait olmayan kartlar (en yeni basta).

    Yeni bir karti sisteme tanitmak icin numarasini elle yazmak gerekmez:
    kart kapiya okutulur, cihaz onu tanimadigi icin reddeder ama numarasini
    gecis kaydina yazar. Bu sorgu o numaralari buraya getirir.

    Kaydeden ve dogrulayan ayni donanim oldugu icin numara formati tanim
    geregi ayni olur. Sunucuya ayri bir USB okuyucu takilsaydi ayni kart
    icin farkli bir metin uretebilir, kayit kapida eslesmezdi.

    SADECE KART NUMARASINA BENZEYEN detaylar alinir. Basarisiz kart
    kayitlarinin hepsi ham UID degildir: sunucuya ulasilamadigi icin
    KARAR VERILEMEYEN okumalar "karar yok (1234)" olarak yazilir (bkz.
    rfid_screen). Onlar birer yetkisiz kart degildir ve bu havuza aday
    olarak dusmemelidir - dusseydi, ag her koptugunda listeye yanlis
    satirlar dolardi.

    DESEN NEDEN ONALTILIK: eski MFRC522 surumu UID'yi ondalik yaziyordu
    ve burada '^[0-9]+$' araniyordu. 18 Agustos 2026'da USB okuyucuya
    gecildi; yeni okuyucu ONALTILIK yaziyor (ornek: 1b33db13). Eski
    desen harf iceren hicbir numarayi kabul etmedigi icin havuz kalici
    olarak BOS gorunurdu - hata da vermezdi, sadece yeni kartlar hic
    listelenmezdi. Uzunluk siniri, "detay" alanina yazilan kisa
    aciklamalarin yanlislikla kart sanilmasini engelliyor.
    """
    satirlar = sorgula(
        "SELECT g.detay AS uid, MAX(g.zaman) AS zaman, COUNT(*) AS deneme"
        " FROM gecis_kaydi g"
        " LEFT JOIN personel p ON p.kart_uid = g.detay"
        " WHERE g.yontem = 'kart' AND g.sonuc = FALSE"
        "   AND g.personel_id IS NULL AND g.detay <> ''"
        "   AND g.detay REGEXP '^[0-9a-fA-F]{4,32}$'"
        "   AND p.id IS NULL"
        "   AND g.zaman >= DATE_SUB(NOW(), INTERVAL %s MINUTE)"
        " GROUP BY g.detay ORDER BY zaman DESC LIMIT 8",
        (KART_YAKALAMA_DAKIKA,))
    return satirlar


# ---------------- Calisma suresi / kesinti ----------------

def sure_metni(saniye) -> str:
    """Saniyeyi okunur sureye cevirir: '2 gun 5 saat', '18 dakika'."""
    if saniye is None:
        return "bilinmiyor"
    saniye = int(saniye)
    if saniye < 0:
        saniye = 0
    if saniye < 60:
        return f"{saniye} saniye"
    dakika, _ = divmod(saniye, 60)
    if dakika < 60:
        return f"{dakika} dakika"
    saat, dakika = divmod(dakika, 60)
    if saat < 24:
        return f"{saat} saat {dakika} dakika"
    gun, saat = divmod(saat, 24)
    return f"{gun} gun {saat} saat"


# Sablonlarda:  {{ sn|sure }}
app.jinja_env.filters["sure"] = sure_metni


def sure_canli(saniye) -> str:
    """
    Canli sayac bicimi: '21 sa 38 dk 12 sn'.

    sure_metni'den AYRI tutuluyor cunku ikisinin isi farkli:
      - sure_metni GECMIS bir sureyi anlatir ("2 saat kopuk kaldi");
        orada saniye gurultudur.
      - bu ise SUREN bir sureyi gosterir ve her saniye artar; saniye
        alani olmasa sayac donmus gorunurdu.

    Kisaltma kullaniliyor ("21 saat 38 dakika 12 saniye" yerine
    "21 sa 38 dk 12 sn"): dar sag sutunda uc birimli tam yazim iki
    satira tasiyor ve her saniye satir sayisi degistiginde kutu
    zipliyordu.

    ONEMLI: bu bicimin AYNISI panel.html icindeki JavaScript'te de
    var. Sayac tarayicida ilerliyor; iki bicim ayrisirsa sayfa ilk
    acildiginda bir metin, bir saniye sonra baska bir metin gorunur.
    """
    if saniye is None:
        return "bilinmiyor"
    saniye = max(0, int(saniye))
    gun, kalan = divmod(saniye, 86400)
    saat, kalan = divmod(kalan, 3600)
    dakika, sn = divmod(kalan, 60)
    if gun:
        return f"{gun} gün {saat} sa {dakika} dk"
    if saat:
        return f"{saat} sa {dakika} dk {sn} sn"
    if dakika:
        return f"{dakika} dk {sn} sn"
    return f"{sn} sn"


app.jinja_env.filters["surec"] = sure_canli


def makine_acilisi():
    """
    Isletim sisteminin acilis anini doner (bulunamazsa None).

    psutil KULLANILMIYOR: sunucuda internet yok, ek paket kurmak elle
    tekerlek dosyasi tasimak demek. Windows'ta GetTickCount64 ve
    Linux'ta /proc/uptime her kurulumda vardir.

    GetTickCount64 uyku/hazirda bekletme sirasinda ILERLEMEZ. Bu sunucu
    uyumaya alinmadigi icin fark etmez; alinirsa "acik kalma suresi"
    gercekten uyanik gecen sureyi gosterir, ki zaten dogru olan odur.
    """
    try:
        if os.name == "nt":
            import ctypes
            ms = ctypes.windll.kernel32.GetTickCount64()
            return datetime.now() - timedelta(milliseconds=ms)
        with open("/proc/uptime", encoding="ascii") as dosya:
            return datetime.now() - timedelta(seconds=float(dosya.read().split()[0]))
    except Exception:  # noqa: BLE001 - sure bilgisi ugruna panel patlamasin
        return None


def _cihaz_kesintilerini_isle():
    """
    Cihazlarin kopus/donus anlarini cihaz_kesinti tablosuna yazar.

    Cihaz kendi kopusunu bildiremez - koptugu icin zaten bildiremez.
    O yuzden kopus BURADAN, son_iletisim damgasinin eskimesinden
    anlasilir. Kesintinin baslangici "fark ettigimiz an" degil, cihazdan
    gelen SON iletisim ani olarak yaziliyor; yoksa her kesinti
    CIHAZ_KOPUK_SANIYE kadar kisa gorunurdu.

    Hic gorulmemis cihaz (son_iletisim NULL) icin kesinti ACILMAZ:
    henuz kurulmamis bir kapiyi "surekli arizali" diye raporlamak
    gercek arizalari gorunmez yapar.
    """
    # 'yerel' HARIC hepsi izlenir. 'yerel' bir cihaz sunucudan
    # kasitli olarak koparilmistir; onun icin kesinti acmak, kendi
    # kapattigimiz seyi ariza diye raporlamak olurdu. 'kapali' cihaz
    # ise BAGLIDIR (sadece kimseyi almiyor) - onun kopmasi gercek bir
    # arizadir ve gorulmesi gerekir.
    for c in sorgula("SELECT id, son_iletisim FROM cihaz"
                     " WHERE durum <> 'yerel'"):
        son = c["son_iletisim"]
        kopuk = (not son) or (datetime.now() - son) > timedelta(
            seconds=CIHAZ_KOPUK_SANIYE)
        acik = sorgula(
            "SELECT id FROM cihaz_kesinti WHERE cihaz_id = %s"
            " AND bitis IS NULL ORDER BY id DESC LIMIT 1", (c["id"],), tek=True)
        if kopuk and not acik and son:
            calistir("INSERT INTO cihaz_kesinti (cihaz_id, baslangic)"
                     " VALUES (%s, %s)", (c["id"], son))
        elif not kopuk and acik:
            calistir("UPDATE cihaz_kesinti SET bitis = %s WHERE id = %s",
                     (son, acik["id"]))


def _nabiz_dongusu():
    """
    Dakikada bir "ayaktayim" damgasi atar. Arka plan is parcacigi.

    Hatalar YUTULUR ama dongu durmaz: veritabani birkac dakika
    erisilemez olsa bile panel calismaya devam etmeli, sadece o
    dakikalarin damgasi eksik kalir. Damganin kendisi icin paneli
    durdurmak, olcmeye calistigimiz seyi bozmak olurdu.
    """
    global _oturum_id
    son_damga = 0.0
    while True:
        try:
            # Iki is ayri hizda: kendi damgamiz dakikada bir yeter
            # (sunucunun kapali kaldigi sureyi bir dakika hassasiyetle
            # olcmek fazlasiyla iyi), ama KAPININ kopmasi saniyeler
            # icinde fark edilmeli. Ikisi ayni dongude ayni sikliktaydi;
            # o zaman 10 saniyelik esigin hicbir anlami kalmiyordu.
            simdi = time.monotonic()
            if _oturum_id is None or (simdi - son_damga) >= NABIZ_SANIYE:
                if _oturum_id is None:
                    _oturum_id = calistir(
                        "INSERT INTO sunucu_oturum (baslangic, son_nabiz, acilis)"
                        " VALUES (%s, NOW(), %s)",
                        (PANEL_BASLANGIC, makine_acilisi()))
                else:
                    calistir("UPDATE sunucu_oturum SET son_nabiz = NOW()"
                             " WHERE id = %s", (_oturum_id,))
                son_damga = simdi
            _cihaz_kesintilerini_isle()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(KESINTI_KONTROL_SANIYE)


def nabiz_baslat():
    """Nabiz is parcacigini baslatir. daemon=True: panel kapaninca biter."""
    threading.Thread(target=_nabiz_dongusu, name="nabiz", daemon=True).start()


def sistem_durumu():
    """
    Panelin ustunde gosterilen "her sey ayakta mi" ozeti.

    Uc ayri soruyu ayri ayri cevaplar, cunku uclu de baska bir arizaya
    isaret eder:
      - Windows ne zamandir acik  -> elektrik kesintisi / reboot
      - Panel ne zamandir acik    -> program coktu mu (Windows ayaktayken)
      - Kapi ne zamandir bagli    -> ag / Pi arizasi
    """
    simdi = datetime.now()
    acilis = makine_acilisi()

    durum = {
        "acilis": acilis,
        "acik_saniye": int((simdi - acilis).total_seconds()) if acilis else None,
        "panel_baslangic": PANEL_BASLANGIC,
        "panel_saniye": int((simdi - PANEL_BASLANGIC).total_seconds()),
        "onceki_kapanis": None,
        "kapali_saniye": None,
        "yeniden_basladi": None,
        "cihazlar": [],
    }

    # Bir onceki calisma dilimi: son damgasi, sunucunun/panelin en gec
    # ne zaman hayatta oldugunu soyler. Aradaki bosluk = kapali kalinan
    # sure (en fazla bir nabiz araligi kadar sisebilir).
    if _oturum_id:
        onceki = sorgula(
            "SELECT baslangic, son_nabiz, acilis FROM sunucu_oturum"
            " WHERE id < %s ORDER BY id DESC LIMIT 1", (_oturum_id,), tek=True)
        if onceki:
            durum["onceki_kapanis"] = onceki["son_nabiz"]
            durum["kapali_saniye"] = int(
                (PANEL_BASLANGIC - onceki["son_nabiz"]).total_seconds())
            # Acilis damgasi degismediyse Windows hic kapanmamis,
            # sadece panel yeniden baslamis demektir.
            durum["yeniden_basladi"] = (
                "makine" if (onceki["acilis"] is None or acilis is None
                             or abs((onceki["acilis"] - acilis).total_seconds()) > 120)
                else "panel")

    # BUTUN cihazlar listeleniyor, 'yerel' olanlar dahil.
    #
    # Onceki surumde hizmet disi kapilar filtreleniyordu ve kapi
    # panelden TAMAMEN kayboluyordu: onu hizmet disi birakan kisi bile
    # bir daha goremiyordu. Bir kapinin ekranda hic olmamasi ile
    # sorunsuz calismasi ayni goruntu - kotu bir sessizlik.
    #
    # 'yerel' kapida "kopuk" HESAPLANMAZ: cihaz sunucudan bilerek
    # koparilmistir, damga atmamasi ariza degil beklenen davranistir.
    # Kirmizi gosterilseydi kasitli kapatma ile gercek kopma ayni
    # renkte olurdu.
    for c in sorgula("SELECT id, cihaz_adi, konum, durum, son_iletisim"
                     " FROM cihaz ORDER BY id"):
        son = c["son_iletisim"]
        if c["durum"] == "yerel":
            c["cevrimici"] = False
            c["beri"] = None
            c["gun_kesinti"] = 0
            c["gun_kesinti_sure"] = 0
            durum["cihazlar"].append(c)
            continue
        c["cevrimici"] = bool(son) and (simdi - son) <= timedelta(
            seconds=CIHAZ_KOPUK_SANIYE)

        # Suregelen kesinti varsa "ne kadardir kopuk", yoksa en son
        # kapanan kesintinin bitisinden beri "ne kadardir bagli".
        kesinti = sorgula(
            "SELECT baslangic, bitis FROM cihaz_kesinti WHERE cihaz_id = %s"
            " ORDER BY id DESC LIMIT 1", (c["id"],), tek=True)
        c["beri"] = None
        if kesinti and kesinti["bitis"] is None:
            c["beri"] = int((simdi - kesinti["baslangic"]).total_seconds())
        elif kesinti and c["cevrimici"]:
            c["beri"] = int((simdi - kesinti["bitis"]).total_seconds())
        elif c["cevrimici"]:
            # Hic kesinti yasanmamis: takibin basladigi ana kadar geri git.
            ilk = sorgula("SELECT MIN(baslangic) AS ilk FROM sunucu_oturum",
                          tek=True)
            if ilk and ilk["ilk"]:
                c["beri"] = int((simdi - ilk["ilk"]).total_seconds())

        gunluk = sorgula(
            "SELECT COUNT(*) AS adet,"
            " SUM(TIMESTAMPDIFF(SECOND, baslangic, COALESCE(bitis, NOW())))"
            "   AS toplam"
            " FROM cihaz_kesinti WHERE cihaz_id = %s"
            "   AND COALESCE(bitis, NOW()) >= DATE_SUB(NOW(), INTERVAL 24 HOUR)",
            (c["id"],), tek=True)
        c["gun_kesinti"] = int(gunluk["adet"] or 0)
        c["gun_kesinti_sure"] = int(gunluk["toplam"] or 0)
        durum["cihazlar"].append(c)

    return durum


# ---------------- Bildirimler ----------------
#
# Zil isaretinin arkasindaki mantik. Uc tur olay var ve ucu de
# MEVCUT VERIDEN TURETILIYOR; ayri bir bildirim tablosu yok
# (gerekcesi: sunucu/007-bildirimler.sql).
#
#   1) Kapi baglantisi koptu / geri geldi   -> cihaz_kesinti
#   2) Sunucu (panel) durdu / geri geldi    -> sunucu_oturum bosluklari
#   3) Kopukken kac kisi gecti              -> gecis_kaydi.detay '[yerel]'
#
# Ucuncusu asil sorunun cevabidir: baglanti koptugunda kapi calismaya
# devam ediyor ama o sirada kimlerin girdigi sunucuda ANINDA gorunmez;
# kayitlar cihazda birikir ve baglanti gelince toplu halde iner.
# Bildirim, "kopukken 3 kisi gecti, hepsi simdi senkronize edildi"
# diyerek o boslugu kapatir.


def ayar_oku(anahtar: str, varsayilan: str = "") -> str:
    """Tek bir ayar degeri. Tablo yoksa varsayilana duser."""
    try:
        satir = sorgula("SELECT deger FROM ayar WHERE anahtar = %s",
                        (anahtar,), tek=True)
        return satir["deger"] if satir else varsayilan
    except Exception:  # noqa: BLE001 - goc calistirilmamis olabilir
        return varsayilan


def ayar_yaz(anahtar: str, deger: str):
    calistir("INSERT INTO ayar (anahtar, deger) VALUES (%s, %s)"
             " ON DUPLICATE KEY UPDATE deger = VALUES(deger)",
             (anahtar, deger))


def _cevrimdisi_gecisler(cihaz_id: int, bas, bit) -> dict:
    """
    Verilen aralikta YEREL listeden karar verilerek acilan kapilar.

    Ayirt edici isaret, dogrula.py'nin basarili gecislerin detay
    alanina yazdigi "[yerel]" / "[yerel (sunucu yanit vermedi)]"
    etiketidir. Reddedilen denemelere etiket YAZILMAZ (web'deki
    "son okutulan kart" ozelligi o alani ham UID olarak okuyor),
    bu yuzden sorgu zaten sonuc = TRUE ile sinirli.

    Hem gecis hem KISI sayisi doner: ayni kisinin ard arda uc kez
    okutmasi "uc kisi girdi" diye okunmasin.
    """
    r = sorgula(
        "SELECT COUNT(*) AS gecis,"
        " COUNT(DISTINCT personel_id) AS kisi"
        " FROM gecis_kaydi"
        " WHERE cihaz_id = %s AND sonuc = TRUE"
        "   AND zaman >= %s AND zaman <= %s"
        "   AND detay LIKE %s",
        (cihaz_id, bas, bit, "%[yerel%"), tek=True)
    return {"gecis": int(r["gecis"] or 0), "kisi": int(r["kisi"] or 0)}


def bildirimler(limit: int = 12) -> dict:
    """
    Son olaylar ve kacinin okunmadigi.

    Doner: {"olaylar": [...], "yeni": n, "okundu": datetime|None}

    Olaylar zamana gore TERS siralanir; en yenisi ustte. "Yeni" olcusu
    ayar tablosundaki 'bildirim_okundu' damgasidir.
    """
    okundu_metin = ayar_oku("bildirim_okundu", "")
    okundu = None
    if okundu_metin:
        try:
            okundu = datetime.strptime(okundu_metin, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            okundu = None

    olaylar = []
    simdi = datetime.now()

    # ---- 1) Kapi baglantisi ----
    try:
        kesintiler = sorgula(
            "SELECT k.id, k.cihaz_id, k.baslangic, k.bitis, c.cihaz_adi"
            " FROM cihaz_kesinti k JOIN cihaz c ON c.id = k.cihaz_id"
            " ORDER BY k.baslangic DESC LIMIT %s", (limit,))
    except Exception:  # noqa: BLE001
        kesintiler = []

    for k in kesintiler:
        bitis = k["bitis"] or simdi
        sure = int((bitis - k["baslangic"]).total_seconds())
        sayim = _cevrimdisi_gecisler(k["cihaz_id"], k["baslangic"], bitis)

        if k["bitis"] is None:
            olaylar.append({
                "tur": "kopuk",
                "zaman": k["baslangic"],
                "baslik": f"{k['cihaz_adi']} bağlantısı kopuk",
                "metin": (f"{k['baslangic'].strftime('%d.%m %H:%M')}"
                          f" itibarıyla kopuk ({sure_metni(sure)})."
                          " Kapı kendi listesinden çalışmaya devam ediyor."),
                "sayim": sayim,
            })
        else:
            olaylar.append({
                "tur": "dondu",
                "zaman": k["bitis"],
                "baslik": f"{k['cihaz_adi']} bağlantısı geri geldi",
                "metin": (f"{k['baslangic'].strftime('%d.%m %H:%M')} -"
                          f" {k['bitis'].strftime('%H:%M')} arası"
                          f" {sure_metni(sure)} kopuk kaldı."),
                "sayim": sayim,
            })

    # ---- 2) Sunucu duruslari ----
    # Iki oturum satiri arasindaki bosluk. Bosluk esigi iki nabiz:
    # tek bir gecikmis damga durus sanilmasin.
    try:
        oturumlar = sorgula(
            "SELECT id, baslangic, son_nabiz, acilis FROM sunucu_oturum"
            " ORDER BY id DESC LIMIT %s", (limit + 1,))
    except Exception:  # noqa: BLE001
        oturumlar = []

    for i, o in enumerate(oturumlar):
        onceki = oturumlar[i + 1] if i + 1 < len(oturumlar) else None
        if not onceki:
            continue
        bosluk = int((o["baslangic"] - onceki["son_nabiz"]).total_seconds())
        if bosluk < NABIZ_SANIYE * 2:
            continue
        makine = (onceki["acilis"] is None or o["acilis"] is None
                  or abs((onceki["acilis"] - o["acilis"]).total_seconds()) > 120)
        olaylar.append({
            "tur": "sunucu",
            "zaman": o["baslangic"],
            "baslik": ("Sunucu yeniden başladı" if makine
                       else "Web paneli yeniden başladı"),
            "metin": (f"{onceki['son_nabiz'].strftime('%d.%m %H:%M')}"
                      f" itibarıyla durdu,"
                      f" {o['baslangic'].strftime('%H:%M')} itibarıyla"
                      f" geri geldi — {sure_metni(bosluk)} kapalı."
                      + ("" if makine else
                         " İşletim sistemi yeniden başlamamış,"
                         " yalnızca program durmuş.")),
            "sayim": None,
        })

    olaylar.sort(key=lambda x: x["zaman"], reverse=True)
    olaylar = olaylar[:limit]

    yeni = 0
    for o in olaylar:
        o["yeni"] = bool(okundu is None or o["zaman"] > okundu)
        if o["yeni"]:
            yeni += 1

    return {"olaylar": olaylar, "yeni": yeni, "okundu": okundu}


@app.context_processor
def sablon_degiskenleri():
    """
    Her sablonda kullanilabilen degiskenler.

    'istek_yolu' sol menude aktif sayfayi isaretlemek icin. Her gorunume
    ayri ayri gecirmek yerine buradan veriliyor; yeni bir sayfa eklerken
    unutulacak bir adim kalmasin.
    """
    veri = {"istek_yolu": request.path}
    # Zil sol menude, yani HER sayfada. Bu yuzden bildirimler tek tek
    # gorunumlere degil buradan veriliyor; yeni bir sayfa eklerken
    # unutulacak bir adim kalmasin.
    #
    # Oturum yokken sorgulanmaz: giris sayfasi veritabanina hic
    # dokunmamali, yoksa MySQL kapaliyken parola ekrani bile acilmaz.
    veri["bekleyen_kart"] = 0
    if session.get("girisli"):
        try:
            veri["bildirim"] = bildirimler()
        except Exception:  # noqa: BLE001 - zil, sayfayi asla dusurmesin
            veri["bildirim"] = {"olaylar": [], "yeni": 0, "okundu": None}
        # Menudeki sayi rozeti. Bekleyen kart, fark edilmezse kimsenin
        # aramadigi bir istir: kapida kart okutan kisi panele bakmaz,
        # panele bakan kisi de kapida ne okutuldugunu bilmez.
        try:
            veri["bekleyen_kart"] = len(bekleyen_kartlar())
        except Exception:  # noqa: BLE001
            veri["bekleyen_kart"] = 0
    else:
        veri["bildirim"] = {"olaylar": [], "yeni": 0, "okundu": None}
    return veri


def giris_gerekli(f):
    """Oturum acilmamissa giris sayfasina yonlendirir."""
    @wraps(f)
    def sarmalayici(*args, **kwargs):
        if not session.get("girisli"):
            return redirect(url_for("giris", sonraki=request.path))
        return f(*args, **kwargs)
    return sarmalayici


# ---------------- Oturum ----------------

@app.route("/giris", methods=["GET", "POST"])
def giris():
    ip = request.remote_addr or "?"
    if request.method == "POST":
        sayac, kilit_bitis = _giris_denemeleri.get(ip, [0, 0.0])
        kalan = int(kilit_bitis - time.monotonic())
        if kalan > 0:
            flash(f"Cok fazla hatali deneme. {kalan} saniye bekleyin.", "hata")
            return render_template("giris.html")

        # compare_digest: karsilastirma suresi girilen parolaya gore
        # degismesin (zamanlama saldirisi).
        if secrets.compare_digest(request.form.get("parola", ""), PANEL_PAROLA):
            _giris_denemeleri.pop(ip, None)
            # Oturum kimligini yenile: giris oncesi verilen bir cerezle
            # oturum sabitleme (session fixation) yapilamasin.
            session.clear()
            session["girisli"] = True
            session.permanent = True
            return redirect(request.args.get("sonraki") or url_for("panel"))

        sayac += 1
        if sayac >= GIRIS_MAX_DENEME:
            _giris_denemeleri[ip] = [0, time.monotonic() + GIRIS_KILIT_SANIYE]
            flash(f"Cok fazla hatali deneme. "
                  f"{GIRIS_KILIT_SANIYE} saniye bekleyin.", "hata")
        else:
            _giris_denemeleri[ip] = [sayac, 0.0]
            flash(f"Parola hatali. "
                  f"({GIRIS_MAX_DENEME - sayac} deneme hakki kaldi)", "hata")
    return render_template("giris.html")


@app.route("/cikis")
def cikis():
    session.clear()
    return redirect(url_for("giris"))


# ---------------- Panel (ozet) ----------------

@app.route("/")
@giris_gerekli
def panel():
    """
    Genel durum - SADELESTIRILDI (19 Agustos 2026).

    Onceki surumde alti sayac karti, yedi gunluk sutun grafigi, cihaz
    tablosu ve "son 10 okutma" karisik listesi vardi. Sayfa acilir
    acilmaz sorulan soru aslinda ikiydi: "sistem ayakta mi" ve "kim
    girdi". Grafik ve yontem dagilimi gunluk kullanimda okunmuyor,
    ihtiyac oldugunda Raporlar sayfasinda zaten var.

    Kalanlar:
      - sistem durumu (calisma sureleri, kapi baglantisi)
      - uc sayac: yetkili personel, bugunku giris, bugunku red
      - kapida bekleyen tanimsiz kartlar (yapilacak is)
      - SADECE GIRISLER listesi (sonuc = TRUE)

    Red sayaci duruyor ama listede degil: reddedilenleri gormek bir
    guvenlik incelemesidir, sayaca tiklanip filtreli kayit sayfasina
    gidilir. Girisle karisik tek liste, ikisini de zor okunur yapiyordu.
    """
    kisi = sorgula(
        "SELECT SUM(yetkili_mi = TRUE) AS yetkili,"
        " SUM(yetkili_mi = FALSE) AS pasif"
        " FROM personel", tek=True)

    bugun = sorgula(
        "SELECT SUM(sonuc = TRUE) AS giris, SUM(sonuc = FALSE) AS red"
        " FROM gecis_kaydi WHERE DATE(zaman) = CURDATE()", tek=True)

    ozet = {
        "personel": int(kisi["yetkili"] or 0),
        "pasif": int(kisi["pasif"] or 0),
        "giris": int(bugun["giris"] or 0),
        "red": int(bugun["red"] or 0),
    }

    # Yuz bayragi ile vektor arasindaki tutarsizlik: sessiz kalmasin.
    # Sayac karti olarak degil, sadece VARSA uyari serdi olarak.
    ozet["yuz_tutarsiz"] = sorgula(
        "SELECT COUNT(*) AS n FROM personel"
        " WHERE yuz_kayitli = TRUE"
        "   AND (yuz_vektor IS NULL OR yuz_vektor = '')", tek=True)["n"]

    # TUM okutmalar - basarili da reddedilen de. Onceki surumde liste
    # yalnizca acilan kapilari gosteriyordu; o zaman "kartim
    # calismadi" diyen birinin denemesi panelde hic gorunmuyor,
    # bakmak icin ayri bir sayfaya gitmek gerekiyordu. Izleme
    # ekraninda asil merak edilen zaten basarisiz denemedir.
    # KARAR NEREDE VERILDI
    # --------------------
    # Kapi, sunucuya ulasamadiginda kendi yerel listesinden karar
    # verebiliyor (bkz. config.YEREL_YEDEK). Bu, kayit sunucuya
    # dustugunde artik gorunmuyordu: panelde "acildi" yaziyor ama o
    # anda sunucuyla hic konusulmamis olabilir. Fark onemli, cunku
    # yerel liste son senkronizasyon anindaki halidir - araya giren
    # bir yetki iptali o kapiya henuz ulasmamis olabilir.
    #
    # Bilgi zaten gecis_kaydi.detay icinde duruyor ("0123 [yerel]",
    # bkz. app/dogrula.py detay()); burada sadece etiketi ayikliyoruz.
    # Ayri bir sutun EKLENMEDI - yerel karar istisnadir, her satira
    # "sunucu" yazmak tabloyu doldurup istisnayi gizlerdi.
    girisler = sorgula(
        "SELECT g.zaman, g.yontem, g.sonuc,"
        " COALESCE(p.ad_soyad, '(bilinmiyor)') AS ad_soyad, c.cihaz_adi,"
        " CASE WHEN g.detay LIKE '%[yerel%' THEN 'yerel' END AS karar"
        " FROM gecis_kaydi g"
        " LEFT JOIN personel p ON p.id = g.personel_id"
        " LEFT JOIN cihaz c ON c.id = g.cihaz_id"
        " ORDER BY g.zaman DESC LIMIT 40"
    )

    return render_template(
        "panel.html", ozet=ozet, girisler=girisler,
        durum=sistem_durumu(), nabiz=NABIZ_SANIYE,
        yenileme=YENILEME_SANIYE)


# ---------------- Personel ----------------

@app.route("/kartlar")
@giris_gerekli
def tanimsiz_kartlar():
    """
    Kapida okutulmus ama kimseye tanimli olmayan kartlar.

    Genel Durum sayfasindan buraya tasindi (19.08.2026): orasi surekli
    acik duran bir izleme ekrani, burasi ise bakilinca islem yapilip
    biten bir yapilacak-is listesi. Ikisi ayni sayfada oldugunda is
    listesi izleme ekraninin ustunu kapliyordu.
    """
    return render_template("kartlar.html",
                           bekleyen=bekleyen_kartlar(),
                           dakika=KART_YAKALAMA_DAKIKA)


@app.route("/api/nabiz")
@giris_gerekli
def api_nabiz():
    """
    HAFIF YOKLAMA - saniyede bir cagrilir.

    Genel Durum sayfasi once bunu sorar, sayfanin tamamini degil.
    Doner: en son gecis kaydinin id'si ve guncel sureler.

    NEDEN TAM SAYFA CEKILMIYOR
    --------------------------
    Sayfanin tamami her saniye uretilseydi her turda sablon islenir,
    bildirim olaylari hesaplanir (kesinti basina ayri bir sorgu) ve
    ~40 kayitlik liste bastan yazilirdi. Buradaki cevap birkac yuz
    bayt ve birkac hafif sorgu. Liste ancak son_id DEGISTIGINDE
    yeniden cekiliyor - yani gercekten yeni bir okutma oldugunda.

    Ayni kademeli mantik Pi tarafinda da var (bkz. app/sync.py,
    degisiklik_var_mi): once "degisti mi" diye sor, degistiyse tam
    turu yap.
    """
    son = sorgula("SELECT MAX(id) AS son_id FROM gecis_kaydi", tek=True)
    durum = sistem_durumu()

    # DURUM IMZASI
    # ------------
    # Sayfanin sag sutunu once YALNIZCA son_id degisince yenileniyordu,
    # yani ancak biri kapidan gecince. Kapinin kablosu cekildiginde yeni
    # bir kayit olusmadigi icin kutu hic yenilenmiyor, sayfa elle
    # yenilenene kadar yesil "Bagli" kaliyordu.
    #
    # Daha kotusu: sayac bu arada ilerlemeye devam ediyordu ama sayinin
    # ANLAMI degismisti ("ne kadardir bagli" -> "ne kadardir kopuk"),
    # altindaki yazi hala "kesintisiz bagli" diyordu. Yani gec kalmakla
    # kalmiyor, yanlis bilgi gosteriyordu.
    #
    # Imza cihaz sayisini ve her cihazin cevrimici olup olmadigini
    # tasiyor. Degisti mi diye tek bir metin karsilastirmasi yapiliyor;
    # boylece tam sayfa yenilemesi yalnizca GERCEKTEN bir sey
    # degistiginde tetikleniyor ve saniyelik yoklama hafif kaliyor.
    #
    # Cihaz sayisi da imzada: 'yerel' hale alinan bir kapi listeden
    # tamamen cikiyor, cevrimici bayraklari degismeden.
    imza = ";".join(
        "%s:%s:%d" % (c["id"], c.get("durum") or "",
                      1 if c.get("cevrimici") else 0)
        for c in durum.get("cihazlar", []))

    return {
        "son_id": int(son["son_id"] or 0),
        "imza": imza,
        # PANEL SURUMU
        # ------------
        # Panel her yeniden baslatildiginda degisen bir damga. Acik
        # duran sekmeler bunu gorunce kendilerini yeniden yukler.
        #
        # Neden gerekli: bu sayfanin mantiginin buyuk kismi JavaScript
        # icinde. Sunucudaki dosyalar guncellendiginde ZATEN ACIK olan
        # bir sekme eski kodu calistirmaya devam eder - dosya yeni,
        # ekran eski. Hangi surumun calistigi disaridan anlasilmadigi
        # icin bu, "duzelttim ama duzelmemis" goruntusu veriyor ve
        # olmayan bir hatayi aratiyor.
        #
        # Panel kendi kendine yeniden baslamaz; bu damganin degismesi
        # ya bir guncelleme ya da bir cokme demektir. Ikisinde de
        # sayfanin tazelenmesi dogru davranis.
        "surum": PANEL_BASLANGIC.strftime("%Y%m%d%H%M%S"),
        "sunucu_sn": durum.get("acik_saniye"),
        "panel_sn": durum.get("panel_saniye"),
        # Cihaz sureleri id'ye gore: sayfada birden fazla kapi olabilir.
        "cihazlar": {
            str(c["id"]): {"sn": c.get("beri"),
                           "cevrimici": bool(c.get("cevrimici"))}
            for c in durum.get("cihazlar", [])
        },
    }


@app.route("/bildirimler/okundu", methods=["POST"])
@giris_gerekli
def bildirim_okundu():
    """
    Zili sifirlar: bu andan oncekiler okunmus sayilir.

    GET degil POST: tarayici ve vekiller GET adreslerini onbellege
    alabilir ya da onceden getirebilir; zil boylece kimse tiklamadan
    sifirlanirdi.

    Cevap JSON, cunku sayfa yeniden yuklenmiyor - zil yerinde sonuyor.
    """
    ayar_yaz("bildirim_okundu", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {"ok": True}


# KISI ROLLERI - sunucudaki personel.rol ENUM'u ile AYNI anahtarlar.
# Tek yerde tutuluyor: form seceneklerini, filtre dugmelerini ve
# tablodaki rozeti ayni sozluk besliyor. Uc yere ayri ayri yazilsaydi
# yeni bir rol eklendiginde biri unutulur ve o rol sessizce
# secilemez / gorunmez olurdu.
ROLLER = {
    "personel": "Personel",
    "ogrenci":  "Öğrenci",
    "misafir":  "Misafir",
}


@app.route("/personel")
@giris_gerekli
def personel_listesi():
    arama = request.args.get("q", "").strip()
    rol = request.args.get("rol", "").strip()

    # Kosullar parcali kuruluyor: arama ve rol filtresi BIRLIKTE de
    # kullanilabilmeli. Iki ayri dala bolunseydi "misafirler icinde
    # ara" yapilamazdi.
    kosullar, params = [], []
    if arama:
        kosullar.append("(ad_soyad LIKE %s OR kart_uid LIKE %s)")
        params += [f"%{arama}%", f"%{arama}%"]
    if rol in ROLLER:
        kosullar.append("rol = %s")
        params.append(rol)
    nerede = ("WHERE " + " AND ".join(kosullar)) if kosullar else ""
    kisiler = sorgula(f"SELECT * FROM personel {nerede} ORDER BY ad_soyad",
                      tuple(params))

    # Filtre dugmelerindeki sayilar TUM tablodan, filtreden bagimsiz
    # hesaplaniyor: "Misafir (3)" yazan dugmeye basildiginda 3 satir
    # gelmeli. Filtrelenmis kumeden sayilsaydi secili rol disindaki
    # her dugme 0 gosterirdi.
    sayilar = {r["rol"]: r["adet"] for r in sorgula(
        "SELECT rol, COUNT(*) AS adet FROM personel GROUP BY rol")}

    return render_template("personel.html", kisiler=kisiler, arama=arama,
                           rol=rol, roller=ROLLER, sayilar=sayilar,
                           toplam=sum(sayilar.values()))


@app.route("/personel/yeni", methods=["GET", "POST"])
@giris_gerekli
def personel_ekle():
    if request.method == "POST":
        ad = request.form.get("ad_soyad", "").strip()
        kart = request.form.get("kart_uid", "").strip()
        pin = request.form.get("pin", "").strip()
        # Bilinmeyen deger 'personel'e dusuruluyor. Dogrudan yazilsaydi
        # MySQL ENUM'u reddeder ve sayfa 500 verirdi; elle duzenlenmis
        # bir form icin bu fazla sert bir cevap.
        rol = request.form.get("rol", "personel")
        if rol not in ROLLER:
            rol = "personel"

        def formu_goster():
            return render_template("personel_form.html", kisi=None,
                                   bekleyen=bekleyen_kartlar(),
                                   roller=ROLLER, girilen=request.form)

        if not ad:
            flash("Ad soyad bos olamaz.", "hata")
            return formu_goster()
        if not kart and not pin:
            flash("En az bir giris yontemi girin (kart veya PIN).", "hata")
            return formu_goster()

        try:
            kisi_id = calistir(
                "INSERT INTO personel (ad_soyad, rol, kart_uid, pin_ozeti,"
                " yetkili_mi) VALUES (%s, %s, %s, %s, TRUE)",
                (ad, rol, kart or None, pin_ozetle(pin)),
            )
        except mysql.connector.IntegrityError:
            flash(f"'{kart}' kart numarasi baska bir kisiye kayitli.", "hata")
            return formu_goster()

        try:
            yol = foto_kaydet(request.files.get("fotograf"), kisi_id)
            if yol:
                calistir("UPDATE personel SET foto_yolu = %s WHERE id = %s",
                         (yol, kisi_id))
                # Fotograf yuklendiyse yuz vektorunu de uret: aksi halde
                # kisi listede fotografli gorunur ama yuzuyle giremez.
                yuzu_isle(kisi_id, ad)
        except ValueError as e:
            flash(f"{ad} eklendi ama fotograf yuklenemedi: {e}", "hata")
            return redirect(url_for("personel_listesi"))

        flash(f"{ad} eklendi. Kapi cihazina en gec 1 dakika icinde iner.",
              "basari")
        return redirect(url_for("personel_listesi"))

    # Panelden "Kisiye tanimla" ile gelindiyse kart numarasi adres
    # satirinda tasiniyor; form onunla acilsin. Numarayi elle kopyalamak
    # bu arayuzdeki en sik hata kaynagiydi - bir hane yanlis yazilinca
    # kayit sessizce calismiyor, kart kapida yine reddediliyordu.
    hazir_kart = request.args.get("kart", "").strip()
    return render_template("personel_form.html", kisi=None,
                           bekleyen=bekleyen_kartlar(), roller=ROLLER,
                           girilen={"kart_uid": hazir_kart} if hazir_kart
                                   else None)


@app.route("/personel/<int:kisi_id>/duzenle", methods=["GET", "POST"])
@giris_gerekli
def personel_duzenle(kisi_id):
    kisi = sorgula("SELECT * FROM personel WHERE id = %s", (kisi_id,), tek=True)
    if not kisi:
        flash("Kayit bulunamadi.", "hata")
        return redirect(url_for("personel_listesi"))

    if request.method == "POST":
        ad = request.form.get("ad_soyad", "").strip()
        kart = request.form.get("kart_uid", "").strip()
        pin = request.form.get("pin", "").strip()
        rol = request.form.get("rol", kisi["rol"])
        if rol not in ROLLER:
            rol = kisi["rol"]

        if pin:
            calistir(
                "UPDATE personel SET ad_soyad = %s, rol = %s, kart_uid = %s,"
                " pin_ozeti = %s WHERE id = %s",
                (ad, rol, kart or None, pin_ozetle(pin), kisi_id))
        else:
            # PIN alani bos birakildiysa mevcut PIN korunur
            calistir(
                "UPDATE personel SET ad_soyad = %s, rol = %s, kart_uid = %s"
                " WHERE id = %s", (ad, rol, kart or None, kisi_id))

        try:
            yol = foto_kaydet(request.files.get("fotograf"), kisi_id)
            if yol:
                calistir("UPDATE personel SET foto_yolu = %s WHERE id = %s",
                         (yol, kisi_id))
                # Yeni fotograf = yeni vektor. Eski vektor bırakılırsa
                # kisi degistirdigi fotografla degil eskisiyle taninir.
                yuzu_isle(kisi_id, ad)
        except ValueError as e:
            flash(str(e), "hata")

        flash(f"{ad} guncellendi.", "basari")
        return redirect(url_for("personel_listesi"))

    return render_template("personel_form.html", kisi=kisi,
                           bekleyen=bekleyen_kartlar(), roller=ROLLER,
                           girilen=None)


@app.route("/api/bekleyen-kartlar")
@giris_gerekli
def api_bekleyen_kartlar():
    """Form acikken sayfa yenilenmeden kart listesini tazelemek icin."""
    return {
        "kartlar": [
            {"uid": k["uid"],
             "zaman": k["zaman"].strftime("%H:%M:%S"),
             "deneme": k["deneme"]}
            for k in bekleyen_kartlar()
        ]
    }


@app.route("/personel/<int:kisi_id>/yetki", methods=["POST"])
@giris_gerekli
def personel_yetki(kisi_id):
    """Yetkiyi acar/kapatir. Kapatmak = karti iptal etmek."""
    kisi = sorgula("SELECT * FROM personel WHERE id = %s", (kisi_id,), tek=True)
    if kisi:
        yeni = not kisi["yetkili_mi"]
        calistir("UPDATE personel SET yetkili_mi = %s WHERE id = %s",
                 (yeni, kisi_id))
        flash(f"{kisi['ad_soyad']} - erisim "
              f"{'acildi' if yeni else 'KAPATILDI'}.", "basari")
    return redirect(url_for("personel_listesi"))


@app.route("/personel/<int:kisi_id>/sil", methods=["POST"])
@giris_gerekli
def personel_sil(kisi_id):
    """
    Kisiyi siler. Gecis kayitlari SILINMEZ; gecis_kaydi tablosundaki
    yabanci anahtar ON DELETE SET NULL oldugu icin eski kayitlar
    'bilinmiyor' olarak durmaya devam eder. Denetim izi korunur.
    """
    calistir("DELETE FROM personel WHERE id = %s", (kisi_id,))
    for uzanti in IZINLI_UZANTI:
        yol = os.path.join(FOTO_KLASOR, f"{kisi_id}{uzanti}")
        if os.path.exists(yol):
            try:
                os.remove(yol)
            except OSError:
                pass
    flash("Kisi silindi. Gecmis kayitlari arsivde korundu.", "basari")
    return redirect(url_for("personel_listesi"))


# ---------------- Gecis kayitlari ----------------

@app.route("/kayitlar")
@giris_gerekli
def kayitlar():
    yontem = request.args.get("yontem", "")
    sonuc = request.args.get("sonuc", "")
    baslangic = request.args.get("baslangic", "")
    bitis = request.args.get("bitis", "")
    cihaz = request.args.get("cihaz", "")

    kosullar, params = [], []
    if yontem in ("kart", "sifre", "yuz"):
        kosullar.append("g.yontem = %s")
        params.append(yontem)
    if sonuc in ("1", "0"):
        kosullar.append("g.sonuc = %s")
        params.append(sonuc == "1")
    if baslangic:
        kosullar.append("g.zaman >= %s")
        params.append(baslangic + " 00:00:00")
    if bitis:
        kosullar.append("g.zaman <= %s")
        params.append(bitis + " 23:59:59")
    # Cihaz filtresi: coklu kapida "hangi kapidan gecmis" sorusu
    # kayitlarin en sik sorulani olur. isdigit() kontrolu, deger
    # dogrudan SQL'e degil parametreye gitse bile, anlamsiz girdiyi
    # bastan eler.
    if cihaz.isdigit():
        kosullar.append("g.cihaz_id = %s")
        params.append(int(cihaz))

    nerede = ("WHERE " + " AND ".join(kosullar)) if kosullar else ""
    satirlar = sorgula(
        "SELECT g.id, g.zaman, g.yontem, g.sonuc, g.detay,"
        " COALESCE(p.ad_soyad, '(bilinmiyor)') AS ad_soyad, c.cihaz_adi"
        " FROM gecis_kaydi g"
        " LEFT JOIN personel p ON p.id = g.personel_id"
        " LEFT JOIN cihaz c ON c.id = g.cihaz_id"
        f" {nerede} ORDER BY g.zaman DESC LIMIT 500", tuple(params))

    # Filtreye uyan kayitlarin ozeti. Liste 500'de kesiliyor; ozet
    # TUM eslesme uzerinden hesaplaniyor, yoksa "3 red" yaziyor ama
    # aslinda 40 tane var gibi bir yaniltma olurdu.
    ozet = sorgula(
        "SELECT COUNT(*) AS toplam, SUM(g.sonuc = TRUE) AS izin,"
        " SUM(g.sonuc = FALSE) AS red"
        f" FROM gecis_kaydi g {nerede}", tuple(params), tek=True)

    return render_template("kayitlar.html", satirlar=satirlar,
                           yontem=yontem, sonuc=sonuc, cihaz=cihaz,
                           baslangic=baslangic, bitis=bitis, ozet=ozet,
                           cihaz_secenekleri=sorgula(
                               "SELECT id, cihaz_adi FROM cihaz"
                               " ORDER BY cihaz_adi"))


# ---------------- Cihazlar ----------------

@app.route("/cihazlar", methods=["GET", "POST"])
@giris_gerekli
def cihazlar():
    """
    Kapi cihazlarinin listesi ve yeni cihaz ekleme.

    Bir cihaz eklemek iki adimli bir is: burada bir satir olusturulur,
    sonra cihazin kendisine BU SATIRIN id'si tanitilir
    (data/gizli.json -> KAPI_DEVICE_ID). Numara tutmazsa cihaz kayit
    gonderemez; gecis_kaydi.cihaz_id yabanci anahtari reddeder ve
    kuyruk sessizce birikir. Bu yuzden id sayfada BUYUK gosteriliyor.

    SILME YOK: gecis kayitlari cihaza bagli. Bkz. 005-coklu-kapi.sql.
    """
    if request.method == "POST":
        ad = request.form.get("cihaz_adi", "").strip()
        konum = request.form.get("konum", "").strip()
        if not ad:
            flash("Cihaz adi bos olamaz.", "hata")
            return redirect(url_for("cihazlar"))
        try:
            yeni_id = calistir(
                "INSERT INTO cihaz (cihaz_adi, konum) VALUES (%s, %s)",
                (ad, konum or None))
        except mysql.connector.IntegrityError:
            flash(f"'{ad}' adinda bir cihaz zaten var.", "hata")
            return redirect(url_for("cihazlar"))
        flash(f"{ad} eklendi. Cihazin gizli.json dosyasina"
              f" KAPI_DEVICE_ID = {yeni_id} yazin.", "basari")
        return redirect(url_for("cihazlar"))

    satirlar = sorgula(
        "SELECT c.*, COUNT(g.id) AS kayit_sayisi,"
        " SUM(DATE(g.zaman) = CURDATE()) AS bugun"
        " FROM cihaz c LEFT JOIN gecis_kaydi g ON g.cihaz_id = c.id"
        " GROUP BY c.id ORDER BY c.id")
    simdi = datetime.now()
    for c in satirlar:
        son = c["son_iletisim"]
        c["cevrimici"] = bool(son) and (simdi - son) < timedelta(
            hours=CIHAZ_UYARI_SAAT)
        # SET sutunu surucu surumune gore metin ya da kume gelebilir;
        # sablon tek bicim gorsun diye burada kumeye cevriliyor.
        ham = c.get("izinli_roller")
        if isinstance(ham, (set, frozenset, list, tuple)):
            c["roller"] = {str(r) for r in ham}
        else:
            c["roller"] = {r for r in str(ham or "").split(",") if r}
    return render_template("cihazlar.html", cihazlar=satirlar, roller=ROLLER)


@app.route("/cihazlar/<int:cihaz_id>/guncelle", methods=["POST"])
@giris_gerekli
def cihaz_guncelle(cihaz_id):
    ad = request.form.get("cihaz_adi", "").strip()
    konum = request.form.get("konum", "").strip()
    if not ad:
        flash("Cihaz adi bos olamaz.", "hata")
        return redirect(url_for("cihazlar"))

    # IZINLI ROLLER
    # Isaretsiz kutu form verisinde HIC gorunmez; bu yuzden "hicbiri
    # secilmedi" ile "alan gonderilmedi" ayni sey gibi gorunur. Ikisi de
    # bos SET'e yazilsaydi kapi kimseyi almaz hale gelirdi - hem de
    # kullanici sadece cihazin adini degistirdigini sanarken.
    #
    # Bu yuzden bos secim REDDEDILIYOR: bir kapiyi herkese kapatmanin
    # yolu rolleri bosaltmak degil, "Kapiyi kapat" dugmesidir. O yol
    # panelde acikca gorunur ve geri alinabilir.
    secilen = [r for r in request.form.getlist("roller") if r in ROLLER]
    if not secilen:
        flash("En az bir kisi turu secili olmali."
              " Kapiyi tamamen kapatmak icin 'Kapiyi kapat' kullanin.",
              "hata")
        return redirect(url_for("cihazlar"))

    try:
        calistir("UPDATE cihaz SET cihaz_adi = %s, konum = %s,"
                 " izinli_roller = %s WHERE id = %s",
                 (ad, konum or None, ",".join(secilen), cihaz_id))
    except mysql.connector.IntegrityError:
        flash(f"'{ad}' adinda baska bir cihaz var.", "hata")
        return redirect(url_for("cihazlar"))
    flash("Cihaz guncellendi.", "basari")
    return redirect(url_for("cihazlar"))


# Cihaz durumlarinin insan diline cevirisi. Tek yerde tutuluyor:
# flash mesaji ile tablodaki rozet ayri ayri yazilsaydi zamanla
# birbirinden ayrisir, ayni durum iki farkli isimle anilirdi.
CIHAZ_DURUMLARI = {
    "acik":   "hizmette",
    "yerel":  "hizmet disi (sunucu bagi kesik)",
    "kapali": "kapali (kimse giremez)",
}


@app.route("/cihazlar/<int:cihaz_id>/durum", methods=["POST"])
@giris_gerekli
def cihaz_durum(cihaz_id):
    """
    Cihazi uc halden birine alir (silmez).

    Hedef durum GONDEREN DUGMEDEN geliyor, mevcut durumdan
    hesaplanmiyor. Ucuncu hal eklendiginde "tersine cevir" mantigi
    anlamsizlasti: 'kapali' bir cihazin karsiti 'acik' mi 'yerel' mi
    olacagi belirsizdir. Butona basan zaten nereye gitmek istedigini
    biliyor.
    """
    hedef = request.form.get("hedef", "")
    if hedef not in CIHAZ_DURUMLARI:
        flash("Gecersiz durum.", "hata")
        return redirect(url_for("cihazlar"))

    c = sorgula("SELECT cihaz_adi FROM cihaz WHERE id = %s",
                (cihaz_id,), tek=True)
    if not c:
        flash("Cihaz bulunamadi.", "hata")
        return redirect(url_for("cihazlar"))

    # 'aktif' de yaziliyor: eski sutun 010 gocune kadar duruyor ve
    # guncellenmemis bir cihaz hala onu okuyor olabilir. Ikisi
    # ayrisirsa kapi panelde gorunenden baska turlu davranir.
    calistir("UPDATE cihaz SET durum = %s, aktif = %s WHERE id = %s",
             (hedef, hedef != "yerel", cihaz_id))
    flash(f"{c['cihaz_adi']} -> {CIHAZ_DURUMLARI[hedef]}."
          " Kayitlari yerinde duruyor.", "basari")
    return redirect(url_for("cihazlar"))


# ---------------- Raporlar ----------------

@app.route("/raporlar")
@giris_gerekli
def raporlar():
    yontem_dagilimi = sorgula(
        "SELECT yontem, COUNT(*) AS adet,"
        " SUM(sonuc = FALSE) AS basarisiz"
        " FROM gecis_kaydi GROUP BY yontem ORDER BY adet DESC")

    gunluk = sorgula(
        "SELECT DATE(zaman) AS gun, COUNT(*) AS adet,"
        " SUM(sonuc = TRUE) AS basarili"
        " FROM gecis_kaydi WHERE zaman >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)"
        " GROUP BY DATE(zaman) ORDER BY gun DESC")

    en_cok = sorgula(
        "SELECT COALESCE(p.ad_soyad, '(bilinmiyor)') AS ad_soyad,"
        " COUNT(*) AS adet FROM gecis_kaydi g"
        " LEFT JOIN personel p ON p.id = g.personel_id"
        " WHERE g.sonuc = TRUE GROUP BY g.personel_id, p.ad_soyad"
        " ORDER BY adet DESC LIMIT 10")

    saatlik = sorgula(
        "SELECT HOUR(zaman) AS saat, COUNT(*) AS adet FROM gecis_kaydi"
        " GROUP BY HOUR(zaman) ORDER BY saat")

    en_yuksek = max([s["adet"] for s in saatlik], default=1) or 1

    # ---- Sunucu calisma dilimleri ----
    # En yeniden eskiye. Iki ardisik satir arasindaki bosluk = sunucunun
    # (veya sadece panelin) kapali kaldigi sure. Bu bosluk SATIRIN
    # KENDISINDE yok, iki satiri karsilastirarak hesaplaniyor; sorgu
    # icinde LAG() yazmak yerine burada yapiliyor cunku MySQL 8 pencere
    # fonksiyonu okunmasi zor bir sorgu uretirdi ve satir sayisi 15.
    oturumlar = sorgula(
        "SELECT id, baslangic, son_nabiz, acilis,"
        " TIMESTAMPDIFF(SECOND, baslangic, son_nabiz) AS sure"
        " FROM sunucu_oturum ORDER BY id DESC LIMIT 15")
    for i, o in enumerate(oturumlar):
        o["acik"] = (o["id"] == _oturum_id)
        onceki = oturumlar[i + 1] if i + 1 < len(oturumlar) else None
        if onceki:
            o["kapali_saniye"] = int(
                (o["baslangic"] - onceki["son_nabiz"]).total_seconds())
            # Acilis damgasi ayni ise makine hic kapanmamis; sadece
            # panel surecinin oldugu anlamina gelir. Bu ayrim, "gece
            # elektrik mi kesildi yoksa program mi coktu" sorusunun
            # tek dogrudan cevabi.
            o["tur"] = ("makine" if (o["acilis"] is None
                                     or onceki["acilis"] is None
                                     or abs((o["acilis"] - onceki["acilis"])
                                            .total_seconds()) > 120)
                        else "panel")
        else:
            o["kapali_saniye"] = None
            o["tur"] = None

    # ---- Kapi baglantisi kesintileri ----
    kesintiler = sorgula(
        "SELECT k.id, k.baslangic, k.bitis, c.cihaz_adi,"
        " TIMESTAMPDIFF(SECOND, k.baslangic, COALESCE(k.bitis, NOW()))"
        "   AS sure"
        " FROM cihaz_kesinti k JOIN cihaz c ON c.id = k.cihaz_id"
        " ORDER BY k.id DESC LIMIT 20")

    return render_template("raporlar.html", yontem_dagilimi=yontem_dagilimi,
                           gunluk=gunluk, en_cok=en_cok, saatlik=saatlik,
                           en_yuksek=en_yuksek, oturumlar=oturumlar,
                           kesintiler=kesintiler)


if __name__ == "__main__":
    # host="0.0.0.0" : ag uzerindeki diger bilgisayarlar da erisebilsin.
    #
    # debug=True KAPATILDI. Acik oldugunda:
    #   - hata sayfasinda Werkzeug hata ayiklayicisi calisir ve
    #     tarayicidan sunucuda Python kodu calistirilabilir,
    #   - istisna izleri (dosya yollari, sorgu metinleri) ekrana basilir.
    # Universite agindaki bir sunucuda ikisi de kabul edilemez.
    #
    # Ayrica Flask'in kendi sunucusu gelistirme icindir (tek istek,
    # cokme durumunda kendini toparlamaz). Kuruluysa waitress
    # kullanilir; Windows'ta uretim icin onerilen yol budur.
    #     pip install waitress
    # Nabiz is parcacigi BURADA baslatiliyor, modul seviyesinde degil.
    # Modulde olsaydi kodu ice aktaran her arac (test, kabuk, goc
    # betigi) veritabanina sahte bir oturum satiri yazar ve "sunucu
    # yeniden basladi" raporu gercekte olmayan kesintilerle dolardi.
    nabiz_baslat()

    try:
        from waitress import serve
        print("Waitress ile calisiyor:  http://0.0.0.0:5000")
        serve(app, host="0.0.0.0", port=5000, threads=8)
    except ImportError:
        print("UYARI: waitress kurulu degil, gelistirme sunucusu kullaniliyor.")
        print("       Kurmak icin:  pip install waitress")
        app.run(host="0.0.0.0", port=5000, debug=False)
