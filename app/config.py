"""
Uygulama geneli ayarlar ve ortam tespiti.

Raspberry Pi uzerinde calisirken gercek donanim (USB RFID, kamera, GPIO)
kullanilir. Windows / masaustunde gelistirirken otomatik olarak "mock"
(taklit) moda gecilir, boylece arayuz donanim olmadan da test edilebilir.
"""
import json
import os
import platform

# ---------------- Gizli bilgiler ----------------
# Sifreler kaynak koda YAZILMAZ. Sirasiyla su kaynaklara bakilir:
#   1) ortam degiskeni          (KAPI_DB_PASSWORD gibi)
#   2) data/gizli.json dosyasi  (surum kontrolune girmez, chmod 600)
#   3) koddaki varsayilan       (sadece gelistirme icin)
#
# Boylece Pi'deki dosya ele gecse bile depo/yedek kopyalarinda sifre
# bulunmaz; sifre degistirmek icin kod degistirmek gerekmez.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_BASE_DIR, "data")
_GIZLI_DOSYA = os.path.join(DATA_DIR, "gizli.json")

try:
    with open(_GIZLI_DOSYA, "r", encoding="utf-8") as _f:
        _GIZLI = json.load(_f)
except (OSError, json.JSONDecodeError):
    _GIZLI = {}


def _ayar(anahtar: str, varsayilan: str) -> str:
    """Ortam degiskeni > gizli.json > varsayilan sirasiyla deger okur.

    BOS DEGER DE BIR CEVAPTIR. gizli.json icinde bir anahtari "" yapmak,
    o ayari KAPATMAK icin kullaniliyor (ornek: KAPI_DEMO_PAROLA = ""
    demo sifreyi devre disi birakir). Onceki `or` zinciri bos degeri
    "verilmemis" sayip varsayilana duserdi: dosyada demo parola
    kapatilmis gorunurken kodda 123456 gecerli kaliyordu ve hicbir
    yerde uyari cikmiyordu.
    """
    if anahtar in os.environ:
        return os.environ[anahtar]
    if anahtar in _GIZLI:
        return str(_GIZLI[anahtar])
    return varsayilan


# Ekran cozunurlugu.
# Ekran DIKEY (portrait) konumlandirilir: 600 genis x 1024 yuksek.
# (7" panel fiziksel olarak dik monte edilir; ana menu dikey akar.)
SCREEN_WIDTH = 600
SCREEN_HEIGHT = 1024

# Uygulama tam ekran mi baslasin? (Pi'de True olmali, gelistirmede False rahat)
FULLSCREEN = False

# Sifre giris uzunlugu
PASSWORD_LENGTH = 6

# Demo/gelistirme icin gecerli sifre (hic kullanici kayitli degilse gecerlidir)
DEMO_PASSWORD = _ayar("KAPI_DEMO_PAROLA", "123456")

# Admin paneline giris PIN'i (dokunmatik numpad ile girilir).
# Gercek kurulumda data/gizli.json icine yazilir, koda birakilmaz.
ADMIN_PIN = _ayar("KAPI_ADMIN_PIN", "0000")

# ---------------- Kaba kuvvet korumasi ----------------
# Ust uste bu kadar hatali denemeden sonra numpad KILIT_SANIYE boyunca
# kilitlenir. 6 haneli PIN = 1.000.000 ihtimal; sinirsiz deneme
# birakilirsa kapiya oturup deneyen biri er gec bulur.
MAX_HATALI_DENEME = 5
KILIT_SANIYE = 30

# Yerel kayitlarin saklanma suresi (gun). Sunucuya gonderilmis ve bu
# sureden eski kayitlar temizlenir; SD kart dolmasin.
LOG_SAKLAMA_GUN = 90

# SQLite veritabani: kisiler (hash'li PIN'ler) + giris/cikis kayitlari
DB_FILE = os.path.join(DATA_DIR, "access.db")
# Eski JSON deposu; ilk aciliste veritabanina aktarilir (varsa)
USERS_FILE = os.path.join(DATA_DIR, "users.json")
FACES_DIR = os.path.join(DATA_DIR, "faces")


# ---------------- Merkezi sunucu (MySQL) ----------------
# Sunucu hem yonetim katmanidir hem de - ag varken - erisim kararinin
# KAYNAGIDIR (bkz. app/dogrula.py). Ag yoksa karar yerel SQLite'a duser,
# boylece kablo cikarsa da kapi calismaya devam eder.
SERVER_HOST = _ayar("KAPI_DB_HOST", "10.40.80.189")
SERVER_PORT = int(_ayar("KAPI_DB_PORT", "3306"))
SERVER_USER = _ayar("KAPI_DB_USER", "kapi_cihaz")
SERVER_PASSWORD = _ayar("KAPI_DB_PASSWORD", "kapi1234")
SERVER_DB = _ayar("KAPI_DB_NAME", "kapi_sistemi")

# Bu cihazin sunucudaki cihaz.id degeri
DEVICE_ID = int(_ayar("KAPI_DEVICE_ID", "1"))

# ---------------- Dogrulama (app/dogrula.py) ----------------
# KARARI SUNUCU VERIR. Bu bir tercih degil kural: erisim listesinin tek
# dogru kopyasi sunucudadir, cihazdaki kopya her zaman gecmise aittir.
#
# YEREL YEDEK ACIK MI?
# --------------------
#   True  -> sunucuya ulasilamazsa karar yerel SQLite'tan verilir.
#            Kapi ag koptugunda da calisir; bedeli, web'den iptal
#            edilmis bir kartin senkronizasyon turu gelene kadar
#            gecebilmesidir.
#   False -> yerele HIC dusulmez. Sunucu cevap verene kadar beklenir,
#            kart okuyan kisi "Sunucu bekleniyor" mesajini gorur.
#            Ag koptugunda KAPI KIMSEYI ICERI ALMAZ.
#
# Su an ACIK (istenen davranis: sunucu varsa sunucudan, yoksa yerelden).
# Kapatmak icin tek satir yeter: asagidaki degeri False yap.
#
# 18 Agustos 2026: gun icinde once False yapilmisti ("cevap gelene kadar
# karti tut"), sonra hibrit davranisa geri donuldu. Kapinin ag koptugunda
# calismaya devam etmesi, iptal edilmis bir kartin senkronizasyon turuna
# kadar gecebilme riskine tercih edildi.
YEREL_YEDEK = True

# TEK bir sunucu denemesinin ust siniri (saniye).
#
# mysql.connector'un connection_timeout parametresi sadece baglanti
# KURULUMUNU sinirlar; baglanti acikken sunucu SELECT'e cevap vermezse
# cagri suresiz asilir. Bu yuzden sorgu ayri bir is parcaciginda
# calistirilip join(DOGRULAMA_TIMEOUT) ile beklenir - ust sinir
# surucunun degil bizim kontrolumuzdedir.
#
# YEREL_YEDEK kapaliyken bu sure "pes etme" degil "bir turu bitirme"
# suresidir: dolunca yerele dusulmez, yeniden denenir.
DOGRULAMA_TIMEOUT = 5

# Basarisiz bir denemeden sonra yeniden denemeden once beklenen sure.
# Olmasaydi sunucu kapaliyken saniyede yuzlerce baglanti denemesi
# yapilirdi; hem ag hem CPU bosuna mesgul olur, sunucu ayaga kalkinca
# da SYN yigini altinda kalirdi.
DOGRULAMA_YENIDEN_DENEME = 1.0

# Toplam bekleme siniri (saniye). 0 = SINIRSIZ; kullanici "Vazgec"e
# basana kadar sunucu beklenir ("cevap gelene kadar karti tut").
# Sifirdan buyuk bir deger verilirse sure dolunca deneme birakilir ve
# ekranda "sunucuya ulasilamiyor" gosterilir (kapi yine acilmaz).
DOGRULAMA_BEKLEME_SINIRI = 0

# Sunucuya ulasilamadiktan sonra bu sure boyunca TEKRAR DENENMEZ,
# dogrudan yerele dusulur. SADECE YEREL_YEDEK = True iken anlamlidir:
# amaci, ag kopukken okutulan her kartin bos yere zaman asimi
# beklemesini onlemektir. Yedek kapaliyken beklemek zaten tek
# secenek oldugu icin bu sayac devreye girmez.
DOGRULAMA_HATA_BEKLEME = 15

# ---------------- Yuz tanima (app/yuz.py) ----------------
# YuNet (yuz bulma) + SFace (kimlik vektoru). Ikisi de DUZ opencv-python
# icinde gelir; opencv-contrib / dlib / face_recognition GEREKMEZ.
# Model dosyalari surum kontrolune girmez, cihaza elle kopyalanir.
YUZ_MODEL_DIR = os.path.join(DATA_DIR, "modeller")
YUZ_YUNET_DOSYA = "face_detection_yunet_2023mar.onnx"
YUZ_SFACE_DOSYA = "face_recognition_sface_2021dec.onnx"

# Kosinus benzerligi esigi. OpenCV'nin kendi ornegi 0.363 kullanir;
# o deger "ayni kisi mi" sorusunu DENGELI cevaplamak icin secilmis,
# yani yanlis kabul ile yanlis reddi esit maliyetli sayar.
#
# Bir KAPIDA maliyetler esit degildir: taninmayan kisi karti veya
# PIN'i kullanir (kucuk rahatsizlik), yanlis taninan kisi ise iceri
# girer (guvenlik ihlali). Bu yuzden esik bilerek yukseltildi.
# Yukseltmek yanlis kabulu azaltir, yanlis reddi artirir.
YUZ_ESIK = 0.40

# En iyi eslesme, ikinci en iyiden en az bu kadar onde olmali.
# Birbirine benzeyen iki kayit arasinda "en yuksegi al" demek kapiyi
# tesadufe birakmaktir; fark kucukse karar verilmez.
YUZ_FARK = 0.05

# Kac ardisik karede yuz gorulunce vektor cikarilsin. Tek karede karar
# vermek, kameranin yakaladigi bulanik bir ara kareyle islem yapma
# riski demektir. Her kare ~30-60 ms; 6 kare ~0.3 sn.
YUZ_KARE_ONAY = 6

# Yuz ARAMA hangi genislikte yapilsin? Kare bu genislige kucultulup
# YuNet'e verilir, bulunan kutu sonra tam cozunurluge geri olceklenir.
#
# Olcum (bu PC, 640x480): 34 ms/kare. Pi 5'te bunun ~2 kati olur ve
# onizleme saniyede 12-15 kareye duser - kullanici "kamera takiliyor"
# der. 320 genislikte islenecek piksel 4'te 1'e iner.
#
# Vektor DAIMA tam cozunurluklu kareden cikarilir; kucultme sadece
# aramayi hizlandirir, kimlik dogrulugunu etkilemez.
YUZ_BULMA_GENISLIK = 320

# ---------------- Senkronizasyon zamanlamasi ----------------
# UC KADEMELI. Sabit araliklarla surekli sunucuya baglanmak yerine,
# baglanti sayisi yapilan ise gore belirlenir.
#
#   1) OLAY  : Cihazda bir islem olunca (kart okundu, kisi eklendi,
#              silindi) beklemeden tam senkronizasyon yapilir.
#              Gecikme ~0. Tetikleyen: SyncWorker.tetikle()
#
#   2) YOKLAMA: Bos zamanlarda, sunucuya SADECE "liste degisti mi"
#              diye sorulur. Tek satirlik cevap doner (~100 bayt),
#              tam senkronizasyon yapilmaz. Degisiklik varsa 1. kademeye
#              gecilir. Bu kademe olmasaydi, web'den iptal edilen bir
#              kart cihaza 12 saate kadar ulasmazdi.
#              0 yapilirsa bu kademe kapanir (hocanin tarifi birebir:
#              sadece olay + 12 saat).
#
#   3) BOSTA : Hicbir sey olmasa bile bu surede bir tam senkronizasyon.
#              Amaci veri tasimak degil, "cihaz hayatta" bilgisini
#              sunucuya yazmak ve listenin bayatlamasini onlemek.
SYNC_YOKLAMA_SANIYE = 30
SYNC_BOSTA_SAAT = 12

# ---- Hayattayim damgasi ----
# cihaz.son_iletisim bu araliklarla guncellenir. Sunucudaki panel
# baglanti kopmasini SADECE bu damganin eskimesinden anlayabilir:
# fis cekildiginde Pi de oldugu icin kendi kopusunu bildiremez.
#
# Dolayisiyla olculebilecek EN KISA kesinti, damga araliginin birkac
# katidir. 4 saniye secildi cunku 10 saniyelik bir kopmanin bile
# kayda gecmesi isteniyor (sunucu tarafinda esik 90 degil 10 saniye;
# bkz. web/app.py CIHAZ_KOPUK_SANIYE).
#
# Bu damga icin HER SEFERINDE yeni baglanti acilmaz - 4 saniyede bir
# TCP el sikismasi + kimlik dogrulama, tasidigi tek satirlik veriden
# kat kat pahali olurdu. Ayri ve KALICI bir baglanti kullanilir
# (app/sync.py, _damga_dongusu).
#
# 4 -> 2 (20.08.2026): kopmanin panele yansimasi 7-11 saniye suruyordu
# ve izlerken bu "calismiyor" hissi veriyordu. Olculebilir en kisa
# kesinti bu araligin birkac katidir; asil sinir burasi, sunucudaki
# esik degil.
DAMGA_SANIYE = 2

# Bir turda sunucuya gonderilecek azami kayit sayisi. Uzun cevrimdisi
# donemden sonra kuyruk cok buyuk olabilir; tek seferde gondermek
# baglantiyi kilitler.
SYNC_PARTI_BOYU = 200

# Sunucuyla bu kadar saat gorusulemezse liste "bayat" sayilir ve
# admin ekraninda uyari gosterilir (iptal edilmis kart hala gecebilir).
STALE_AFTER_HOURS = 12


def _is_raspberry_pi() -> bool:
    """Cihazin Raspberry Pi olup olmadigini tespit eder."""
    if platform.system() != "Linux":
        return False
    # Ortam degiskeni ile zorla mock moda gecmeye izin ver
    if os.environ.get("FORCE_MOCK") == "1":
        return False
    try:
        with open("/proc/device-tree/model", "r") as f:
            return "raspberry pi" in f.read().lower()
    except (FileNotFoundError, PermissionError):
        return False


# Gercek donanim var mi? False ise mock modda calisir.
IS_RASPBERRY_PI = _is_raspberry_pi()

# ---------------- RFID okuyucu ----------------
# 13,56 MHz USB okuyucu (lsusb: ffff:0035 "IC Reader"). Cihaz kendini
# KLAVYE olarak tanitir: karti okuyunca UID'yi tus vurusu olarak yazip
# Enter'a basar.
#
# Neden by-id yolu: /dev/input/eventN numarasi her aciliata degisebilir
# (baska bir USB aygit once takilirsa kayar). by-id adi cihazin seri
# numarasindan uretilir, sabittir. Yanlis aygiti dinlemek demek gercek
# klavyeyi ele gecirmek demek olurdu.
RFID_AYGIT_DESENI = _ayar(
    "KAPI_RFID_AYGIT",
    "/dev/input/by-id/*IC_Reader*event-kbd")

# Okuyucu Enter'a basmadan bu sure gecerse biriken haneler atilir.
# Yarim kalmis bir okuma bir sonraki kartin basina yapismasin.
RFID_HANE_ZAMAN_ASIMI = 1.0


# ================= KAPI DONANIMI (app/hardware/kapi_donanim.py) =================
# Selenoid kilit + buzzer + 3'lu trafik lambasi + MC38 manyetik sensor.
# Hepsi Raspberry Pi'nin 40 pinli GPIO basligina baglanir. Windows /
# masaustunde bu katman "mock" calisir: hicbir pin surulmez, eylemler
# terminale yazilir, sensor durumu simule_kapi() ile elle degistirilir.
#
# Pin numaralari BCM semasiyla verilir (GPIO17 = 17), fiziksel pin
# numarasiyla DEGIL. gpiozero da BCM bekler.
#
#   Islev                  BCM   Fiziksel   Not
#   --------------------   ----  --------   ------------------------------
#   Selenoid kilit rolesi   17     11       role kartinin IN bacagi
#   Buzzer (aktif)          27     13       + ucu; - ucu GND
#   LED kirmizi             5      29       220-330 ohm seri direnc
#   LED sari                6      31       220-330 ohm seri direnc
#   LED yesil               13     33       220-330 ohm seri direnc
#   MC38 manyetik sensor    26     37       diger uc GND; ic pull-up acik
#
# Ortak GND: role karti, buzzer, LED'ler ve MC38'in hepsi Pi'nin GND
# hattini paylasir. Selenoid 12 V ayri adaptorden beslenir; Pi'nin
# 5V hattindan CEKILMEZ (selenoid ~500 mA ceker, Pi'yi resetler).

# --- Selenoid kilit rolesi ---
# Ucuz 1-kanal role kartlari GENELDE aktif-dusuktur: IN bacagi GND
# seviyesine cekilince role ceker. Yanlis ayar kapinin ters calismasi
# (bosta acik, onayda kilitli) olarak gorunur.
KAPI_ROLE_PIN = int(_ayar("KAPI_ROLE_PIN", "17"))
KAPI_ROLE_AKTIF_DUSUK = _ayar("KAPI_ROLE_AKTIF_DUSUK", "1") == "1"

# Kilit tipi: fail-secure varsayildi (guc kesilince kilitli kalir; bkz.
# FIZIKSEL-GUVENLIK.md - kacis yolundaysa fail-safe + cikis butonu
# gerekir). Role cekince kilit ACILIR. Onaydan sonra kapi bu kadar
# saniye acik tutulur, sonra kendiliginden kilitlenir.
KAPI_ACIK_SANIYE = float(_ayar("KAPI_ACIK_SANIYE", "5"))

# --- Buzzer ---
# Aktif buzzer: tek pin HIGH olunca oter (osilator icinde). Pasif
# buzzer kullanilirsa ton uretmek icin PWM gerekir - desteklenmiyor.
KAPI_BUZZER_PIN = int(_ayar("KAPI_BUZZER_PIN", "27"))

# --- 3'lu trafik lambasi ---
# Durum makinesi:
#   KIRMIZI : bosta / kapi kilitli (menu ekrani)
#   SARI    : dogrulama suruyor (kart/PIN/yuz bekleniyor) veya kapi
#             acik kaldi uyarisi (yanip soner)
#   YESIL   : giris onaylandi, kilit acik
KAPI_LED_KIRMIZI_PIN = int(_ayar("KAPI_LED_KIRMIZI_PIN", "5"))
KAPI_LED_SARI_PIN = int(_ayar("KAPI_LED_SARI_PIN", "6"))
KAPI_LED_YESIL_PIN = int(_ayar("KAPI_LED_YESIL_PIN", "13"))

# --- MC38 manyetik kapi sensoru (reed switch) ---
# Iki telli manyetik kontak. Bir uc GPIO, diger uc GND; Pi'nin ic
# pull-up direnci acik tutulur (harici direnc gerekmez).
#
# MC38 normalde-kapali (NC) tiptir: miknatis yaklasiktken (kapi KAPALI)
# kontak kapali -> pin GND'ye cekilir -> LOW okunur. Kapi acilinca
# kontak acilir -> pull-up pini HIGH yapar.
# KAPI_SENSOR_NC = False, normalde-acik (NO) sensor icin durumu ters cevirir.
KAPI_SENSOR_PIN = int(_ayar("KAPI_SENSOR_PIN", "26"))
KAPI_SENSOR_NC = _ayar("KAPI_SENSOR_NC", "1") == "1"

# Sensor gurultusu: durum degisikligi gecerli sayilmadan once bu kadar
# saniye sabit kalmali (kapi carpmasi / titresim tek okuma uretmesin).
KAPI_SENSOR_DEBOUNCE = float(_ayar("KAPI_SENSOR_DEBOUNCE", "0.15"))

# Kapi bu sureden uzun acik kalirsa "acik kaldi" uyarisi: sari LED
# yanip soner, buzzer araliklarla oter, olay loglanir.
KAPI_ACIK_KALDI_SANIYE = float(_ayar("KAPI_ACIK_KALDI_SANIYE", "20"))

# Gecerli bir onaydan sonra sensorun "acildi" demesi normaldir. Onaydan
# bu kadar saniye SONRA gelen acilma "izinsiz acilma" (zorlama) sayilir:
# alarm buzzer + kirmizi LED + olay logu.
KAPI_ONAY_PENCERE_SANIYE = float(_ayar("KAPI_ONAY_PENCERE_SANIYE", "8"))
