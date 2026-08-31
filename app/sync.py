"""
Merkezi MySQL sunucusu ile senkronizasyon.

TASARIM KARARI
--------------
Bu modul KARAR VERMEZ; sadece iki taraftaki veriyi esitler. Kapinin
acilip acilmayacagina app/dogrula.py karar verir: ag varken karari
sunucu verir, ulasilamiyorsa yerel SQLite'a dusulur.

Buradaki esitlemenin amaci bu yuzden degisti. Onceden yerel liste
kararin TEK kaynagiydi; artik YEDEK kaynak. Ag koptugunda kapinin
calismaya devam edebilmesi, bu modulun listeyi guncel tutmus olmasina
bagli - yani esitleme artik bir kolaylik degil, cevrimdisi calismanin
onkosulu.

VERI AKISI (iki yonlu)
    Sunucu -> Pi : personel listesi            (SELECT)
    Pi -> Sunucu : gecis kayitlari             (INSERT, kuyruktan)
    Pi -> Sunucu : Pi'de eklenen personel      (INSERT)
    Pi -> Sunucu : Pi'de duzenlenen personel   (UPDATE)
    Pi -> Sunucu : Pi'de silinen personel      (UPDATE yetkili_mi=0)
    Pi -> Sunucu : cihaz.son_iletisim          (UPDATE)

ZAMANLAMA - UC KADEME
    1) OLAY   : cihazda islem olunca aninda tam senkronizasyon
    2) YOKLAMA: bos zamanda sadece "liste degisti mi" sorusu (tek satir)
    3) BOSTA  : hicbir sey olmasa bile SYNC_BOSTA_SAAT'te bir tam tur

Sabit araliklarla surekli baglanmak yerine baglanti sayisi yapilan ise
gore belirlenir. Bos duran bir kapi gunde 2 tam tur yapar; yogun bir
kapi ise her gecisde aninda yazar.

SILME NEDEN YUMUSAK
-------------------
Cihazda silinen kisi hemen yok edilmez; once sunucuya "yetkisi
kaldirildi" (yetkili_mi = FALSE) olarak bildirilir, onaydan sonra
yerelden silinir. Boylece kimin ne zaman yetkisini kaybettigi
sunucuda kayitli kalir - erisim sistemlerinde bu bilgi silinmemelidir.

YETKI
-----
Cihaz kullanicisi SELECT + INSERT yapabilir; UPDATE yetkisi yalnizca
cihaz tablosu ile personel tablosunun BELIRLI SUTUNLARIYLA sinirlidir.
DELETE, DROP ve ALTER yetkisi yoktur. Kapi cihazi fiziksel olarak
erisilebilir bir yerde durdugu icin, sifresi ele gecse bile hicbir
kayit silinemez (en az yetki ilkesi).

KART IPTALI
-----------
Cevrimdisi bir Pi eski listeyi kullanmaya devam eder; iptal edilen bir
kart ag geri gelene kadar gecerli kalir. Bu kacinilmazdir, o yuzden
gizlenmez: is_stale() ile liste bayatladiginda admin ekraninda uyari
gosterilir.
"""
import threading
import time
from datetime import datetime, timedelta

from PyQt5.QtCore import QThread, pyqtSignal

from app import store
from app.config import (DAMGA_SANIYE, DEVICE_ID, SERVER_DB, SERVER_HOST,
                        SERVER_PASSWORD, SERVER_PORT, SERVER_USER,
                        STALE_AFTER_HOURS, SYNC_BOSTA_SAAT, SYNC_PARTI_BOYU,
                        SYNC_YOKLAMA_SANIYE)

try:
    import mysql.connector
except ImportError:  # masaustu gelistirmede kurulu olmayabilir
    mysql = None

# Baglanti kurulamazsa uygulamayi kilitlememek icin kisa zaman asimi.
_CONNECT_TIMEOUT = 5

# Son basarili senkronizasyon zamani (None ise hic konusulmadi)
_last_success = None

# Sunucudaki listenin son bilinen damgasi: (en son guncelleme, kayit sayisi)
_son_damga = None


def _connect():
    """Sunucuya baglanir. Basarisizsa istisna firlatir."""
    if mysql is None:
        raise RuntimeError("mysql-connector-python kurulu degil")
    return mysql.connector.connect(
        host=SERVER_HOST,
        port=SERVER_PORT,
        user=SERVER_USER,
        password=SERVER_PASSWORD,
        database=SERVER_DB,
        connection_timeout=_CONNECT_TIMEOUT,
        autocommit=False,
    )


# ---------------- Tek yonlu adimlar ----------------

def _push_pending_users(con) -> list:
    """
    Pi'de eklenip sunucuya yollanmamis kisileri yukler.

    Geriye (yerel_id, sunucu_id) ciftlerini DONER, yerele YAZMAZ.
    Yazma isi sync_once icinde, MySQL commit'i basarili olduktan sonra
    yapilir.

    Eski surum sunucu_id'yi burada aninda SQLite'a isliyordu. SQLite
    autocommit oldugu icin bu yazma kaliciydi; sonraki adimlardan biri
    patlayip MySQL rollback edilirse kisi sunucuda YOK ama yerelde
    "gonderildi" isaretli kaliyordu. Bir daha gonderilmiyor, ilk
    pull'da da sunucuda karsiligi bulunmadigi icin yerelden SILINIYORDU.
    Kisi hicbir hata gorunmeden kayboluyordu.
    """
    bekleyen = store.pending_users()
    if not bekleyen:
        return []
    imlec = con.cursor()
    eslesmeler = []
    for u in bekleyen:
        ozet = store.encode_pin(u["pin_hash"], u["pin_salt"], u["pin_iter"])
        try:
            # ROL de gonderiliyor. Cihaz sunucudaki rol sutununu
            # SONRADAN degistiremez (sutun bazinda UPDATE yetkisi yok)
            # ama ilk kaydi olustururken belirtebiliyor. Gonderilmeseydi
            # sunucu varsayilani devreye girer ve cihazdan eklenen
            # herkes sessizce 'personel' olurdu.
            imlec.execute(
                "INSERT INTO personel (ad_soyad, rol, kart_uid, pin_ozeti,"
                " yuz_kayitli, yuz_vektor, yuz_model, yetkili_mi)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (u["name"], u.get("rol") or "personel",
                 u["card"] or None, ozet, bool(u["face"]),
                 u.get("yuz_vektor") or None, u.get("yuz_model") or None,
                 True),
            )
            eslesmeler.append((u["id"], imlec.lastrowid))
        except mysql.connector.IntegrityError:
            # Ayni kart_uid sunucuda zaten var: mevcut kaydi eslestir.
            if not u["card"]:
                continue
            imlec.execute(
                "SELECT id FROM personel WHERE kart_uid = %s", (u["card"],)
            )
            satir = imlec.fetchone()
            if satir:
                eslesmeler.append((u["id"], satir[0]))
    imlec.close()
    return eslesmeler


def _push_local_changes(con) -> list:
    """
    Cihazda duzenlenen ve silinen kisileri sunucuya yazar.

    Geriye (yerel_id, silinmis_mi) ciftlerini DONER, yerele YAZMAZ;
    isaretleme commit sonrasina birakilir (bkz. _push_pending_users).

    Silme burada UPDATE ile yapilir: yetkili_mi = FALSE. Cihazin DELETE
    yetkisi yok - olsaydi, kapiya fiziksel erisimi olan biri butun
    personel kayitlarini silebilirdi.

    CAKISMA: sunucudaki kayit cihazdakinden daha yeniyse UZERINE
    YAZILMAZ. WHERE kosulundaki damga karsilastirmasi bunu veritabani
    seviyesinde garanti eder; iki taraf ayni anda yazsa bile.
    """
    degisenler = store.yerel_degisenler()
    if not degisenler:
        return []
    imlec = con.cursor()
    islenen = []
    for u in degisenler:
        ozet = store.encode_pin(u["pin_hash"], u["pin_salt"], u["pin_iter"])
        try:
            imlec.execute(
                "UPDATE personel SET ad_soyad = %s, kart_uid = %s,"
                " pin_ozeti = %s, yuz_kayitli = %s, yuz_vektor = %s,"
                " yuz_model = %s, yetkili_mi = %s, guncelleme = %s"
                " WHERE id = %s AND (guncelleme IS NULL OR guncelleme < %s)",
                (u["name"], u["card"] or None, ozet, bool(u["face"]),
                 u.get("yuz_vektor") or None, u.get("yuz_model") or None,
                 not u["silindi"], u["guncelleme"], u["sunucu_id"],
                 u["guncelleme"]),
            )
            # Satir guncellenmemis olabilir (sunucudaki kayit daha yeni).
            # Yine de islenmis sayilir: bir sonraki liste indirmede
            # sunucunun degeri cihaza uygulanir, cakisma cozulmus olur.
            islenen.append((u["id"], bool(u["silindi"])))
        except mysql.connector.Error:
            # Ornegin kart_uid cakismasi. Kayit kuyrukta kalir,
            # sonraki turda tekrar denenir.
            continue
    imlec.close()
    return islenen


def _server_damgasi(con) -> tuple:
    """
    Sunucudaki personel listesinin 'degisti mi' damgasi.

    Tek satir doner: (en son guncelleme zamani, kayit sayisi).
    Tam senkronizasyondan cok daha ucuzdur; bos zamanlarda bu sorulur.
    Kayit sayisi da alinir, cunku bir satir dogrudan silinirse en son
    guncelleme zamani degismeyebilir ama sayi degisir.
    """
    imlec = con.cursor()
    imlec.execute(
        "SELECT COALESCE(MAX(guncelleme), '-'), COUNT(*) FROM personel"
    )
    satir = imlec.fetchone()
    imlec.close()
    return (str(satir[0]), int(satir[1])) if satir else ("-", 0)


def _push_logs(con) -> tuple:
    """
    Kuyruktaki gecis kayitlarini sunucuya yazar; (adet, id listesi).

    INSERT IGNORE + UNIQUE(cihaz_id, yerel_log_id):
    MySQL commit'i basarili olup, yerelde "gonderildi" isareti
    konmadan once uygulama kapanirsa (elektrik kesintisi, kilitlenme)
    ayni kayitlar bir sonraki turda tekrar gonderilir. Benzersizlik
    kisiti olmadan sunucuda ayni gecis iki kez gorunurdu. Simdi
    veritabani ikinci kopyayi sessizce reddediyor.
    """
    kayitlar = store.unsynced_logs(SYNC_PARTI_BOYU)
    if not kayitlar:
        return 0, []
    imlec = con.cursor()
    yazilan = []
    for k in kayitlar:
        imlec.execute(
            "INSERT IGNORE INTO gecis_kaydi (personel_id, cihaz_id,"
            " yerel_log_id, zaman, yontem, sonuc, detay, senkronize)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (k.get("personel_sunucu_id"), DEVICE_ID, k["id"], k["ts"],
             k["method"], bool(k["success"]), k.get("detail") or "", True),
        )
        yazilan.append(k["id"])
    imlec.close()
    # Yerelde ancak sunucu commit'inden SONRA isaretlenir; arada
    # baglanti koparsa kayit kuyrukta kalir, kaybolmaz.
    return len(yazilan), yazilan


def _pull_users(con) -> dict:
    """Sunucudaki yetkili personel listesini indirip yerele uygular."""
    imlec = con.cursor(dictionary=True)
    imlec.execute(
        "SELECT id, ad_soyad, rol, kart_uid, pin_ozeti, yuz_kayitli,"
        " yuz_vektor, yuz_model, guncelleme"
        " FROM personel WHERE yetkili_mi = TRUE"
    )
    satirlar = [
        {
            "sunucu_id": r["id"],
            "name": r["ad_soyad"] or "-",
            # Kisi turu (personel / ogrenci / misafir). Kapi yetkisi
            # buna gore hesaplanacak; ag koptugunda da hesaplanabilmesi
            # icin yerele iniyor.
            "rol": r["rol"] or "personel",
            "card": r["kart_uid"] or "",
            "pin_ozeti": r["pin_ozeti"] or "",
            "face": bool(r["yuz_kayitli"]),
            # Yerel yedek yolunun (YEREL_YEDEK) yuzle giris yapabilmesi
            # icin vektor de indirilir. Karar normalde sunucudan gelen
            # listeyle verilir; bu kopya yalnizca ag koptugunda islevlidir.
            "yuz_vektor": r["yuz_vektor"] or "",
            "yuz_model": r["yuz_model"] or "",
            # Cakisma cozumu icin: hangi taraf daha yeni?
            "guncelleme": (r["guncelleme"].strftime("%Y-%m-%d %H:%M:%S")
                           if r["guncelleme"] else ""),
        }
        for r in imlec.fetchall()
    ]
    imlec.close()
    return store.apply_server_users(satirlar)


def _touch_device(con):
    """cihaz.son_iletisim alanini gunceller (hayattayim sinyali)."""
    imlec = con.cursor()
    # SAAT SUNUCUNUN: NOW() burada degil, MySQL'de hesaplanir.
    # Bkz. _damga_at icindeki ayrintili gerekce.
    imlec.execute(
        "UPDATE cihaz SET son_iletisim = NOW() WHERE id = %s",
        (DEVICE_ID,),
    )
    imlec.close()


# ---------------- Hayattayim damgasi ----------------
#
# NEDEN AYRI BIR IS PARCACIGI
# ---------------------------
# Sunucudaki panel, kapiyla baglantinin koptugunu SADECE bu damganin
# eskimesinden anlayabiliyor: fis cekildiginde Pi de oldugu icin kendi
# kopusunu bildiremez. Dolayisiyla olculebilecek en kisa kesinti,
# damga araliginin birkac katidir.
#
# Damga senkronizasyon turlerine birakilamazdi: tam tur bos kapida 12
# saatte bir, hafif yoklama 30 saniyede bir donuyor. Ikisi de "10
# saniyelik kopmayi gor" istegi icin fazla seyrek. Bu dongu 4 saniyede
# bir doner ve TEK bir UPDATE calistirir.
#
# Baglanti KALICI: 4 saniyede bir yeni oturum acmak, TCP el sikismasi
# ve kimlik dogrulama demektir - tasidigi tek satirlik veriden kat kat
# pahali olurdu. Baglanti koptugunda nesne atilir, sonraki turda
# sifirdan kurulur.
#
# Sync is parcaciginin baglantisindan AYRI tutuluyor: ayni baglanti
# paylasilsaydi, uzun suren bir tam senkronizasyon turu boyunca damga
# atilamaz ve kapi tam da en mesgul aninda kopuk gorunurdu.

_damga_baglanti = None
_damga_basladi = False

# CIHAZ DURUMU (sunucudaki cihaz.durum sutunu)
# -------------------------------------------
#   'acik'   Normal calisma.
#
#   'yerel'  Sunucu bagi KESIK. Karar yerel listeden verilir, gecis
#            kayitlari cihazda birikir ve sunucuda gorunmez; hizmete
#            alininca birikenler akar. "Ag koptugunda ne oluyor"
#            senaryosunu kablo cekmeden denemenin de tek yolu budur.
#
#   'kapali' KIMSE GIREMEZ. Sunucu bagi ACIK kalir - yoksa kapiyi
#            geri acmak icin birinin Pi'nin yanina gitmesi gerekirdi.
#            Denemeler kayda gecer: "kapi kapaliyken 14 kisi girmeye
#            calismis" bilgisi, kapinin yanlis zamanda kapatildigini
#            gosterir.
#
# BASLANGIC DEGERI DISKTEN OKUNUR. Bellekte tutulsaydi, kapali bir
# kapida Pi yeniden basladiginda (ve o sirada ag yoksa) kapi kendini
# 'acik' sanip herkesi iceri alirdi.
_cihaz_durum = store.ayar_oku("cihaz_durum", "acik")

# BU KAPIDAN GECEBILEN KISI TURLERI (sunucudaki cihaz.izinli_roller).
#
# Durum gibi bu da DISKTEN okunuyor. Ag koptugunda kapi kararini yerel
# listeden veriyor; rol kurali yalnizca sunucuda dursaydi tam o anda
# uygulanamaz ve "bu kapiya sadece personel girer" kurali en cok
# gerektigi anda calismazdi.
#
# BILINMIYORSA HEPSI. Hic sunucu gormemis bir cihazda liste bos
# olsaydi kapi kimseyi almazdi - yeni kurulan bir kapi, ilk
# senkronizasyona kadar calismaz hale gelirdi. Daraltma BILEREK
# yapilan bir islem; varsayilan olarak uygulanmaz.
_TUM_ROLLER = ("personel", "ogrenci", "misafir")
_izinli_roller = frozenset(
    r for r in store.ayar_oku("izinli_roller", ",".join(_TUM_ROLLER)).split(",")
    if r.strip()
) or frozenset(_TUM_ROLLER)


def izinli_roller() -> frozenset:
    """Bu kapidan gecebilen kisi turleri."""
    return _izinli_roller


def _rolleri_uygula(ham):
    """
    Sunucudan okunan izinli rol listesini yerlestirir ve DISKE yazar.

    Gelen deger surucu surumune gore ya virgullu METIN ('personel,
    ogrenci') ya da bir KUME olabilir; ikisi de kabul ediliyor. Tek
    bicime zorlamak, surucu guncellendiginde kapiyi sessizce kilitleyen
    turden bir bagimlilik olurdu.
    """
    global _izinli_roller
    if ham is None:
        return
    if isinstance(ham, (set, frozenset, list, tuple)):
        gelen = frozenset(str(r).strip() for r in ham if str(r).strip())
    else:
        gelen = frozenset(r.strip() for r in str(ham).split(",") if r.strip())
    # BOS LISTE YOK SAYILIR. Sunucuda bos SET "kimse giremez" demek ama
    # bos deger ayni zamanda "okunamadi"nin da gorunumu; ikisini ayirt
    # edemedigimiz icin kapiyi kilitleme riskini almiyoruz.
    if not gelen or gelen == _izinli_roller:
        return
    _izinli_roller = gelen
    store.ayar_yaz("izinli_roller", ",".join(sorted(gelen)))


# 'yerel'den cikis ani. Senkronizasyon is parcacigi bunu gorunce
# beklemeden tam tur yapar; yoksa birikmis kayitlar bir sonraki
# yoklamaya (30 sn) kadar sunucuya gitmezdi.
_aktife_dondu = threading.Event()


def cihaz_durumu() -> str:
    """Cihazin sunucudan ogrendigi son durum: acik / yerel / kapali."""
    return _cihaz_durum


def cihaz_pasif_mi() -> bool:
    """Sunucu bagi kesik mi? (durum = 'yerel')"""
    return _cihaz_durum == "yerel"


def kapi_kapali_mi() -> bool:
    """Kapi tamamen kapatildi mi? (durum = 'kapali')"""
    return _cihaz_durum == "kapali"


def _durumu_uygula(yeni_durum: str):
    """
    Sunucudan okunan durumu yerlestirir ve DISKE yazar.

    Diske yazma yalnizca DEGISIMDE yapiliyor: damga dongusu birkac
    saniyede bir donuyor, her turda yazsaydi SD kart bosuna yipranirdi.
    """
    global _cihaz_durum
    if yeni_durum not in ("acik", "yerel", "kapali"):
        return
    onceki = _cihaz_durum
    if yeni_durum == onceki:
        return
    _cihaz_durum = yeni_durum
    store.ayar_yaz("cihaz_durum", yeni_durum)
    # 'yerel'den cikildi: birikmis kayitlar hemen gitsin.
    if onceki == "yerel":
        _aktife_dondu.set()


def _damga_at():
    """
    Cihazin durumunu okur; 'yerel' degilse son_iletisim damgasini atar.

    Durum SORGUSU damganin icinde: zaten saniyeler araligiyla donen
    tek yer burasi. Ayri bir yoklama dongusu acilsaydi iki dongu ayni
    soruyu iki kez sorardi.

    'yerel' halde damga ATILMAZ. Atilsaydi sunucu cihazi "bagli"
    gorur, oysa cihaz o sirada sunucuyla hicbir is yapmiyor - panelde
    yaniltici bir "her sey yolunda" gorunurdu.

    'kapali' halde damga ATILIR: kapi kapatilmistir ama BAGLIDIR.
    Damga kesilseydi panel bunu bir ag arizasi gibi gosterir, kasitli
    kapatma ile gercek kopma birbirine karisirdi.
    """
    global _damga_baglanti
    if _damga_baglanti is None:
        _damga_baglanti = _connect()
    imlec = _damga_baglanti.cursor()
    try:
        imlec.execute(
            "SELECT durum, izinli_roller FROM cihaz WHERE id = %s",
            (DEVICE_ID,))
        satir = imlec.fetchone()
        # Kayit YOKSA 'acik' sayilir: cihaz henuz eklenmemis olabilir,
        # bu bir kapatma karari degildir.
        _durumu_uygula(str(satir[0]) if satir else "acik")
        # Rol listesi ayni sorgudan geliyor: durum icin zaten saniyede
        # bir bu satir okunuyor, ikinci bir sorgu bedava bilgi icin
        # fazladan gidis-donus olurdu.
        if satir:
            _rolleri_uygula(satir[1])
        if _cihaz_durum != "yerel":
            # SAAT SUNUCUNUN, CIHAZIN DEGIL
            # --------------------------------
            # Damga eskiden cihazin saatiyle yaziliyordu
            # (datetime.now()), ama "eskidi mi" sorusunu SUNUCU kendi
            # saatiyle soruyor. Iki saat ayni degilse fark dogrudan
            # olcume giriyor.
            #
            # 21.08.2026'da olculdu: Pi'nin saati sunucudan 24 saniye
            # ILERIDEYDI. Damga gelecekte gorundugu icin kopukluk
            # esigi 6 saniye yerine ancak 30 saniye sonra asiliyordu.
            # Yani esigi ne kadar kisaltirsak kisaltalim, gecikme saat
            # farki kadar kaliyordu.
            #
            # NOW() MySQL'de hesaplaninca karsilastirmanin iki tarafi
            # da ayni saatten gelir; cihazin saati kaysa bile olcum
            # bozulmaz. Pi'de donanim saati yok (RTC modulu
            # takilmayacagina karar verildi), yani kayma kalici bir
            # gercek - olcum ona bagimli olmamali.
            imlec.execute(
                "UPDATE cihaz SET son_iletisim = NOW() WHERE id = %s",
                (DEVICE_ID,),
            )
    finally:
        imlec.close()
    _damga_baglanti.commit()


def _damga_dongusu():
    global _damga_baglanti
    while True:
        try:
            _damga_at()
        except Exception:  # noqa: BLE001 - ag hatasi cesitli olabilir
            # Baglanti bozuldu. Nesne atilir; bir sonraki turda
            # yeniden kurulur. Hata YUTULUR: sunucuya ulasilamamasi
            # kapinin calismasini engellememeli, zaten kesintiyi
            # olcmeye calistigimiz durum tam da budur.
            try:
                if _damga_baglanti is not None:
                    _damga_baglanti.close()
            except Exception:  # noqa: BLE001
                pass
            _damga_baglanti = None
        time.sleep(DAMGA_SANIYE)


def damga_baslat():
    """Damga dongusunu bir kez baslatir (ikinci cagri etkisizdir)."""
    global _damga_basladi
    if _damga_basladi or mysql is None:
        return
    _damga_basladi = True
    threading.Thread(target=_damga_dongusu, name="damga", daemon=True).start()


# ---------------- Disari acilan arayuz ----------------

def sync_once() -> dict:
    """
    Tam bir senkronizasyon turu. Asla istisna firlatmaz; sonucu
    sozluk olarak doner, boylece cagiran taraf arayuzu kilitlemez.
    """
    global _last_success, _son_damga
    # Hizmet disi birakilmis cihaz sunucuya HIC yazmaz: kayitlar
    # yerelde birikir (gonderildi = 0 kalir) ve cihaz hizmete
    # alininca ilk turda toplu halde gider. Kuyruk zaten bu is icin
    # var; ag koptugunda da ayni yol isliyor.
    if cihaz_pasif_mi():
        return {"ok": False, "mesaj": "cihaz hizmet disi", "log": 0,
                "kisi": 0}
    con = None
    try:
        con = _connect()
        kisi_eslesmeleri = _push_pending_users(con)
        yerel_degisimler = _push_local_changes(con)
        log_sayisi, log_idleri = _push_logs(con)
        _touch_device(con)
        con.commit()

        # ---- Buradan sonrasi: sunucu tarafi kesinlesti ----
        # Yerel isaretlemeler ancak simdi yapilir. Sirasi onemli:
        # once kisilerin sunucu_id'si islenir, ki hemen ardindan gelen
        # _pull_users onlari "sunucuda yok" sanip silmesin.
        for yerel_id, sunucu_id in kisi_eslesmeleri:
            store.set_server_id(yerel_id, sunucu_id)
        for yerel_id, silinmisti in yerel_degisimler:
            store.yerel_degisim_islendi(yerel_id, silinmisti)
        store.mark_logs_synced(log_idleri)

        indirilen = _pull_users(con)
        # Indirilen liste ile damgayi eslestir; hemen ardindan gelen
        # yoklama "degisti" sanip bosuna tam tur yapmasin.
        _son_damga = _server_damgasi(con)
        _last_success = datetime.now()

        gonderilen = len(kisi_eslesmeleri) + len(yerel_degisimler)
        # Bos liste korumasi devreye girdiyse bunu SESSIZ gecmeyelim:
        # "0 silindi" yazmak, sunucunun bos oldugunu gizler.
        atlanan = indirilen.get("bos_liste") or 0
        ozet = (f"{log_sayisi} kayit + {gonderilen} kisi gonderildi, "
                f"{indirilen['eklendi']} eklendi, "
                f"{indirilen['guncellendi']} guncellendi, "
                f"{indirilen['silindi']} silindi")
        if atlanan:
            ozet += (f" | UYARI: sunucu listesi BOS, {atlanan} yerel kayit"
                     " korundu (silme atlandi)")
        return {
            "ok": True,
            "tam": True,
            "mesaj": ozet,
            "kisi_yuklendi": gonderilen,
            **indirilen,
        }
    except Exception as e:  # noqa: BLE001 - ag hatasi cesitli olabilir
        if con is not None:
            try:
                con.rollback()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": False, "mesaj": f"Sunucuya ulasilamadi: {e}"}
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass


def degisiklik_var_mi() -> dict:
    """
    HAFIF YOKLAMA. Sunucuya tek bir soru sorar: liste degisti mi?

    Tam senkronizasyon yapmaz, veri tasimaz. Donen cevap tek satirdir
    (~100 bayt). Tam tur ise kayit sayisina gore kilobaytlar tasir ve
    birden fazla sorgu calistirir.

    Bu kademe sayesinde bos duran bir kapi ag uzerinde neredeyse hic
    yuk olusturmaz, ama web'den iptal edilen bir kart yine de saniyeler
    icinde cihaza ulasir. Olmasaydi iptal 12 saate kadar gecikirdi.
    """
    global _son_damga
    if cihaz_pasif_mi():
        return {"ok": False, "degisti": False, "mesaj": "cihaz hizmet disi"}
    con = None
    try:
        con = _connect()
        damga = _server_damgasi(con)
        degisti = (_son_damga is not None and damga != _son_damga)
        ilk_kez = _son_damga is None
        _son_damga = damga
        # Hayattayim damgasi BURADA da atiliyor, sadece tam turda degil.
        # Sebebi: bos duran bir kapida tam tur 12 saatte bir yapilir.
        # Damga yalniz orada atilsaydi cihaz.son_iletisim saatlerce
        # eskir, sunucudaki panel de cihazi kopuk sanip cihaz_kesinti
        # tablosuna gercekte yasanmamis kesintiler yazardi - kesinti
        # raporu boylece ise yaramaz hale gelirdi. Bu yoklama zaten
        # 30 saniyede bir ve baglanti zaten acik; tek UPDATE'in
        # maliyeti yok denecek kadar az.
        _touch_device(con)
        con.commit()
        return {"ok": True, "degisti": degisti or ilk_kez}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "degisti": False, "mesaj": str(e)}
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass


def is_stale() -> bool:
    """Liste bayat mi? (uzun suredir sunucuyla gorusulemiyor)"""
    if _last_success is None:
        return True
    return datetime.now() - _last_success > timedelta(hours=STALE_AFTER_HOURS)


def status_text() -> str:
    """Admin ekraninda gosterilecek kisa durum metni."""
    if _last_success is None:
        return "Sunucu ile hic baglanti kurulmadi"
    fark = datetime.now() - _last_success
    dakika = int(fark.total_seconds() // 60)
    if dakika < 2:
        return "Sunucu ile baglanti guncel"
    if is_stale():
        return f"DIKKAT: {dakika // 60} saattir sunucuya ulasilamiyor"
    return f"Son senkronizasyon: {dakika} dakika once"


class SyncWorker(QThread):
    """
    Uc kademeli senkronizasyon is parcacigi.

        1) OLAY   - tetikle() cagrilinca aninda TAM tur
        2) YOKLAMA- SYNC_YOKLAMA_SANIYE'de bir "degisti mi" sorusu;
                    degistiyse TAM tur
        3) BOSTA  - SYNC_BOSTA_SAAT'te bir, hicbir sey olmasa da TAM tur

    Neden sabit aralikli degil: bos duran bir kapiya 10 saniyede bir tam
    senkronizasyon yaptirmak, gunde ~8.600 gereksiz veritabani oturumu
    demektir. Yapilan ise oranli calismak hem agi hem sunucuyu korur.

    Kart dogrulamasi bu is parcacigindan TAMAMEN bagimsizdir; sunucu
    hic cevap vermese de kapi calismaya devam eder.
    """
    finished_round = pyqtSignal(bool, str)   # (basarili mi, mesaj)

    def __init__(self, parent=None, yoklama: int = SYNC_YOKLAMA_SANIYE,
                 bosta_saat: int = SYNC_BOSTA_SAAT):
        super().__init__(parent)
        self._running = False
        self._yoklama = yoklama
        self._bosta = bosta_saat * 3600
        self._hemen = False
        self._son_tam = 0.0
        self._son_budama = 0.0

    # --- Yardimcilar ---
    def _tam_tur(self, sebep: str):
        _aktife_dondu.clear()
        sonuc = sync_once()
        if sonuc["ok"]:
            self._son_tam = time.monotonic()
        self.finished_round.emit(sonuc["ok"], f"[{sebep}] {sonuc['mesaj']}")

    def _uyu(self, saniye: float) -> bool:
        """Uykuyu 100 ms'lik dilimlere boler. Erken uyandiysa False."""
        for _ in range(int(saniye * 10)):
            # _aktife_dondu: cihaz panelden yeniden hizmete alindi.
            # Beklenmezse birikmis kayitlar bir sonraki yoklamaya
            # kadar (30 sn) sunucuya gitmezdi; hizmete alan kisi
            # panele bakip "kayitlar nerede" diye sorardi.
            if not self._running or self._hemen or _aktife_dondu.is_set():
                return False
            self.msleep(100)
        return True

    def run(self):
        self._running = True
        # Hayattayim damgasi burada baslatiliyor: senkronizasyon
        # calisiyorsa cihaz da ayakta demektir. Ayri bir yerden
        # baslatilsaydi (ornegin main.py) ikisi birbirinden bagimsiz
        # olur ve "sync duruyor ama damga atiliyor" gibi yaniltici
        # bir durum cikabilirdi.
        damga_baslat()

        # Acilista bir kez tam tur: liste guncel olsun.
        self._tam_tur("acilis")

        while self._running:
            # Uyku - bu sirada tetikle() gelirse erken uyanilir.
            self._uyu(self._yoklama if self._yoklama > 0 else 60)
            if not self._running:
                return

            # Cihaz panelden yeniden hizmete alindi: birikmis kayitlar
            # beklemeden gitsin. Bayrak BURADA ele alinmali - _uyu()
            # onu gordugu icin erken donuyor; karsiligi verilmezse
            # dongu uyumadan bos yere doner.
            if _aktife_dondu.is_set():
                self._tam_tur("hizmete alindi")
                continue

            # 1. KADEME - cihazda islem oldu
            if self._hemen:
                self._hemen = False
                self._tam_tur("olay")
                continue

            gecen = time.monotonic() - self._son_tam

            # 3. KADEME - bosta guvenlik agi
            if gecen >= self._bosta:
                self._tam_tur("bosta")
            # 2. KADEME - hafif yoklama
            elif self._yoklama > 0:
                if store.bekleyen_is_var():
                    # Gonderilmemis kayit birikmis (onceki tur basarisiz
                    # olmus olabilir). Yoklamaya gerek yok, dogrudan gonder.
                    self._tam_tur("kuyruk")
                else:
                    cevap = degisiklik_var_mi()
                    if cevap["ok"] and cevap["degisti"]:
                        self._tam_tur("sunucuda degisiklik")

            self._budama_zamani_mi()

    def _budama_zamani_mi(self):
        """Gunde bir kez eski kayitlari buda (SD kart dolmasin)."""
        simdi = time.monotonic()
        if simdi - self._son_budama < 86400:
            return
        self._son_budama = simdi
        try:
            store.prune_logs()
        except Exception:  # noqa: BLE001 - budama kritik degil
            pass

    def tetikle(self):
        """
        Bir sonraki turu beklemeden hemen senkronize et.

        Kart okunduktan, kisi eklendikten veya silindikten sonra
        cagrilir: kayit sunucuya saniyeler icinde cikar. Kart
        DOGRULAMASI bu cagriyi beklemez, o zaten yapilmis olur.
        """
        self._hemen = True

    def stop(self):
        self._running = False
        self.wait(2000)
