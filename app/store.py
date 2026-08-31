"""
Kayitli kisiler + giris kayitlari icin yerel SQLite veritabani.

Veriler proje icindeki  data/access.db  dosyasinda tutulur (internetsiz).
SQLite Python ile hazir gelir; Raspberry Pi'de ek kurulum gerektirmez.

Tablolar:
    users : kisiler. PIN'ler duz metin DEGIL, PBKDF2-SHA256 ile
            hash'lenerek saklanir (her kisiye ozel salt ile).
    logs  : her giris denemesi. Kim, hangi yontemle (sifre/kart/yuz),
            basarili mi, ne zaman.

Eski data/users.json dosyasi varsa ilk aciliste veritabanina aktarilir
(PIN'ler hash'lenir) ve dosya .migrated uzantisiyla yedeklenir.

Admin panelinden ekleme/cikarma yapilir; giris ekranlari (sifre, kart, yuz)
buradaki kayitlara gore dogrulama yapar ve log_access() ile kayit birakir.
"""
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from app.config import (DATA_DIR, DB_FILE, FACES_DIR, LOG_SAKLAMA_GUN,
                        USERS_FILE)

# PBKDF2 tekrar sayisi: Pi 4'te ~0.1 sn surer, kaba kuvveti yavaslatir.
_PBKDF2_ITERATIONS = 100_000

# Sync is parcacigi ile arayuz ayni dosyaya yazabilir. Kilit bekleme
# suresi verilmezse sqlite aninda "database is locked" firlatir ve o
# senkronizasyon turu bosa gider.
_BUSY_TIMEOUT_MS = 5000


# ---------------- Baglanti ve sema ----------------

def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FACES_DIR, exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=_BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    # WAL: okuyucu ile yazici birbirini bloklamaz. Kart okunurken arka
    # planda senkronizasyon yaziyor olabilir; ikisi cakismasin.
    con.execute("PRAGMA journal_mode = WAL")
    con.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            pin_hash   TEXT DEFAULT '',
            pin_salt   TEXT DEFAULT '',
            card       TEXT DEFAULT '',
            face       INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL,
            user_id   INTEGER,
            user_name TEXT DEFAULT '',
            method    TEXT NOT NULL,
            direction TEXT DEFAULT 'in',
            success   INTEGER NOT NULL,
            detail    TEXT DEFAULT ''
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ayar (
            anahtar TEXT PRIMARY KEY,
            deger   TEXT NOT NULL
        )
        """
    )
    # MC38 manyetik sensorden gelen fiziksel kapi olaylari. Giris
    # denemesi DEGIL (o 'logs' tablosunda); burada "kapi acildi/kapandi",
    # "acik kaldi", "izinsiz acilma" gibi donanim olaylari tutulur.
    # Simdilik yalnizca yerel; sunucu semasina baglanmasi ayri is.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS kapi_olaylari (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL,
            tur        TEXT NOT NULL,
            detay      TEXT DEFAULT '',
            senkronize INTEGER DEFAULT 0
        )
        """
    )
    _ensure_sync_columns(con)
    _ensure_indexes(con)
    return con


@contextmanager
def _db():
    """
    Baglantiyi acar, isi bitince COMMIT eder ve MUTLAKA kapatir.

    Dikkat:  with sqlite3.connect(...) as con:  kaliba commit eder ama
    baglantiyi KAPATMAZ. Eski kod bu yuzden her sorguda bir dosya
    tanimlayicisi biraktiyordu; 10 saniyede bir calisan senkronizasyonla
    birlikte gun boyu birikiyordu.
    """
    con = _connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# Sonradan eklenen sutunlar. Eski data/access.db dosyalari da calissin
# diye CREATE TABLE'a gomulmeyip ALTER TABLE ile eklenir.
_SYNC_COLUMNS = {
    # kisinin sunucudaki personel.id karsiligi; NULL ise henuz yollanmadi
    # pin_iter: bu PIN hangi PBKDF2 tur sayisiyla uretildi (0 = varsayilan).
    # Sabit varsayilirsa, sunucu ileride tur sayisini degistirdiginde
    # dogrulama hata VERMEDEN yanlis sonuc uretir.
    # guncelleme: kaydin son degistigi an. Iki yonlu senkronizasyonda
    #   ayni kisi hem web'den hem cihazdan duzenlenmis olabilir; hangi
    #   tarafin kazanacagi bu damgayla belirlenir (son yazan kazanir).
    # silindi : yumusak silme. Cihazda silinen kisi hemen yok edilmez,
    #   once sunucuya "yetkisi kaldirildi" bilgisi gonderilir. Aksi
    #   halde sunucu_id kaybolur ve sunucuya haber verilemez; kisi ilk
    #   listede geri gelirdi.
    # yerelden: kayit cihazda mi degistirildi? Sunucuya gonderilmeyi
    #   bekliyor demektir.
    # yuz_vektor: SFace 128-d kimlik vektoru, base64(float32[128]).
    #   'face' sutunu sadece "fotografi var mi" bayragiydi; karar
    #   vermek icin karsilastirilacak sayilar burada durur.
    # yuz_model : vektoru ureten model dosyasi. Model degistirilirse
    #   eski vektorler yenileriyle karsilastirilamaz ve sistem HATA
    #   VERMEDEN kimseyi tanimaz; etiket olmadan sebebi bulunamaz.
    # rol     : personel / ogrenci / misafir. Karari SUNUCU verir, cihaz
    #   yalnizca okur (sunucuda rol sutununda UPDATE yetkisi yok). Yerel
    #   kopya, ag koptugunda kapi yetkisini rolden hesaplayabilmek icin.
    #   Bilinmeyen bir deger gelirse oldugu gibi saklanir; cihazin
    #   sunucunun rol listesini onceden bilmesi gerekmiyor.
    "users": [("sunucu_id", "INTEGER"), ("pin_iter", "INTEGER DEFAULT 0"),
              ("rol", "TEXT DEFAULT 'personel'"),
              ("guncelleme", "TEXT"), ("silindi", "INTEGER DEFAULT 0"),
              ("yerelden", "INTEGER DEFAULT 0"),
              ("yuz_vektor", "TEXT DEFAULT ''"),
              ("yuz_model", "TEXT DEFAULT ''")],
    # kayit sunucuya iletildi mi (0/1)
    "logs": [("senkronize", "INTEGER DEFAULT 0")],
}
_columns_checked = False


def _ensure_sync_columns(con: sqlite3.Connection):
    """Eksik senkronizasyon sutunlarini ekler (surum yukseltme)."""
    global _columns_checked
    if _columns_checked:
        return
    for table, columns in _SYNC_COLUMNS.items():
        existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    _columns_checked = True


_indexes_checked = False


def _ensure_indexes(con: sqlite3.Connection):
    """
    Sorgu indeksleri + kart benzersizligi.

    Ayni kart iki kisiye tanimlanirsa hangisinin gectigi belirsizlesir;
    veritabani seviyesinde engelleniyor. Bos kart alanlari (kartsiz
    kisiler) indekse alinmaz, aksi halde birden fazla kartsiz kisi
    eklenemezdi.
    """
    global _indexes_checked
    if _indexes_checked:
        return
    # Eski surumdeki indeks 'silindi' sutununu bilmiyordu; silinen bir
    # kisinin karti yeni birine tanimlanamiyordu. Yenisiyle degistir.
    con.execute("DROP INDEX IF EXISTS ix_users_card")
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_kart"
        " ON users(card) WHERE card != '' AND silindi = 0"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_logs_senkronize"
        " ON logs(senkronize, id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_sunucu ON users(sunucu_id)"
    )
    _indexes_checked = True


def _now() -> str:
    # Pi'de donanim saati (RTC) YOK - takilmayacagina karar verildi.
    # Sistem saati aciliste agdan (NTP) alinir; elektrik kesintisi
    # sirasinda saat durur ve ag gelene kadar geride kalir. Bunun
    # kayitlara etkisi icin apply_server_users'a bakiniz.
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- PIN hash'leme ----------------

def _hash_pin(pin: str, salt_hex: str = "", iterations: int = 0) -> tuple:
    """PIN'i salt ile hash'ler; (hash_hex, salt_hex) doner."""
    if not salt_hex:
        salt_hex = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex),
        iterations or _PBKDF2_ITERATIONS,
    )
    return digest.hex(), salt_hex


def _verify_pin(pin: str, pin_hash: str, salt_hex: str,
                iterations: int = 0) -> bool:
    if not pin_hash or not salt_hex:
        return False
    calc, _ = _hash_pin(pin, salt_hex, iterations)
    return secrets.compare_digest(calc, pin_hash)


# ---------------- Eski users.json'dan aktarim ----------------

def _migrate_json_if_needed():
    """Eski JSON deposu varsa kisileri (PIN'leri hash'leyerek) aktarir."""
    if not os.path.exists(USERS_FILE):
        return
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            old_users = json.load(f).get("users", [])
    except (json.JSONDecodeError, OSError):
        return

    with _db() as con:
        for u in old_users:
            pin_hash, pin_salt = ("", "")
            if u.get("pin"):
                pin_hash, pin_salt = _hash_pin(str(u["pin"]))
            con.execute(
                "INSERT INTO users (name, pin_hash, pin_salt, card, face, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (u.get("name", "-"), pin_hash, pin_salt,
                 u.get("card", ""), int(bool(u.get("face"))), _now()),
            )
    # Tekrar aktarilmasin diye yedege tasi
    try:
        os.replace(USERS_FILE, USERS_FILE + ".migrated")
    except OSError:
        pass


_migrate_json_if_needed()


# ---------------- Kisi islemleri ----------------

def load_users() -> list:
    """Kayitli tum kisileri okur. 'pin' alani sadece var/yok bilgisidir."""
    with _db() as con:
        rows = con.execute(
            "SELECT * FROM users WHERE silindi = 0 ORDER BY id"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "pin": bool(r["pin_hash"]),   # hash disari verilmez
            "card": r["card"],
            "face": bool(r["face"]),
            "rol": r["rol"] if "rol" in r.keys() else "personel",
        }
        for r in rows
    ]


# Kisi turleri. Sunucudaki personel.rol ENUM'u ile AYNI anahtarlar
# (bkz. sunucu/009-kisi-rolu.sql ve web/app.py ROLLER). Uc yerde ayni
# listenin tutulmasi hos degil ama cihaz ile sunucu ayri makineler;
# tek kaynaktan okumak, o kaynak erisilemedigi anda kisi eklemeyi
# imkansiz kilardi.
ROLLER = {"personel": "Personel", "ogrenci": "Öğrenci",
          "misafir": "Misafir"}


def add_user(name: str, pin: str = "", card: str = "", face: bool = False,
             yuz_vektor: str = "", yuz_model: str = "",
             rol: str = "personel") -> dict:
    """
    Yeni kisi ekler ve olusturulan kaydi doner. PIN hash'lenerek saklanir.

    ROL, KAYIT SIRASINDA SECILEBILIR. Cihazin sunucudaki rol sutununu
    SONRADAN degistirme yetkisi yok (bkz. 009-kisi-rolu.sql) ama ilk
    kaydi olustururken rolu belirtebiliyor - INSERT yetkisi tablo
    genelinde verilmis durumda.
    #
    Neden guvenligi zayiflatmiyor: kapiya fiziksel erisimi olan biri
    zaten kisi EKLEYEBILIYOR; secim eklemek ona yeni bir yetki
    vermiyor. Tersine, secim OLMADIGI surece cihazdan eklenen herkes
    sunucu varsayilaniyla 'personel' oluyordu - yani uc rolun en
    yetkilisi, hem de kimse farkinda olmadan.
    """
    pin_hash, pin_salt, pin_iter = ("", "", 0)
    if pin.strip():
        if pin_cakisiyor_mu(pin):
            raise ValueError("Bu PIN baska bir kisiye tanimli.")
        pin_hash, pin_salt = _hash_pin(pin.strip())
        pin_iter = _PBKDF2_ITERATIONS
    # 'face' bayragi vektorden turetilir: vektor yoksa yuzle giris
    # yapilamaz, dolayisiyla "yuz tanimli" demek yaniltici olurdu.
    # Admin ekraninda "Yuz Yakalandi" yazip kapida taninmamak, hata
    # mesaji olmadigi icin en kotu turden bir arizadir.
    yuz_vektor = (yuz_vektor or "").strip()
    face = bool(face and yuz_vektor)
    with _db() as con:
        cur = con.execute(
            "INSERT INTO users (name, pin_hash, pin_salt, pin_iter, card,"
            " face, yuz_vektor, yuz_model, rol, created_at, guncelleme,"
            " yerelden) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (name.strip(), pin_hash, pin_salt, pin_iter, card.strip(),
             int(face), yuz_vektor, yuz_model or "",
             rol if rol in ROLLER else "personel", _now(), _now()),
        )
        user_id = cur.lastrowid
    return {"id": user_id, "name": name.strip(), "pin": bool(pin_hash),
            "card": card.strip(), "face": face, "rol": rol}


def remove_user(user_id: int):
    """
    Kisiyi siler.

    IKI YONLU SILME
    ---------------
    Kayit sunucuya hic gonderilmemisse (sunucu_id yok) dogrudan silinir;
    kimseye haber vermeye gerek yok.

    Sunucuda karsiligi varsa YUMUSAK silinir: satir kalir, silindi = 1
    isaretlenir. Senkronizasyon bu isareti sunucuya "yetkisi kaldirildi"
    olarak tasir; sunucu onayladiktan sonra satir gercekten silinir.

    Dogrudan silinseydi sunucu_id de yok olurdu, sunucuya hangi kisinin
    silindigi soylenemezdi ve kisi ilk liste indirmede geri gelirdi.
    """
    with _db() as con:
        satir = con.execute(
            "SELECT sunucu_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if satir is None:
            return
        if satir["sunucu_id"] is None:
            con.execute("DELETE FROM users WHERE id = ?", (user_id,))
        else:
            con.execute(
                "UPDATE users SET silindi = 1, yerelden = 1, card = '',"
                " guncelleme = ? WHERE id = ?",
                (_now(), user_id),
            )
    face_path = face_image_path(user_id)
    if os.path.exists(face_path):
        try:
            os.remove(face_path)
        except OSError:
            pass


# ---------------- Kucuk ayarlar (anahtar-deger) ----------------
#
# Cihazin sunucudan ogrendigi ve ELEKTRIK KESILSE BILE hatirlamasi
# gereken bilgiler icin. Bugunku tek kullanicisi cihaz durumu:
#
#   Sunucu "bu kapiyi kapat" dedi, ardindan Pi yeniden basladi ve o
#   sirada ag yok. Durum yalnizca bellekte tutulsaydi kapi kendini
#   'acik' sanip herkesi iceri alirdi - tam da kapatarak onlemek
#   istedigimiz sey. Diske yazilinca kapi kapali dogar ve ancak
#   sunucuya ulasip aksini duyunca acilir.
#
# Ayri bir tablo yerine users icine sutun eklenmedi: bu bilgiler
# kisiye degil CIHAZA ait.


def ayar_oku(anahtar: str, varsayilan: str = "") -> str:
    """Kayitli ayari getirir; yoksa varsayilani doner."""
    try:
        with _db() as con:
            satir = con.execute(
                "SELECT deger FROM ayar WHERE anahtar = ?", (anahtar,)
            ).fetchone()
        return satir["deger"] if satir else varsayilan
    except Exception:  # noqa: BLE001 - ayar okunamamasi kapiyi durdurmasin
        return varsayilan


def ayar_yaz(anahtar: str, deger: str):
    """Ayari yazar (varsa uzerine)."""
    try:
        with _db() as con:
            con.execute(
                "INSERT INTO ayar (anahtar, deger) VALUES (?, ?)"
                " ON CONFLICT(anahtar) DO UPDATE SET deger = excluded.deger",
                (anahtar, str(deger)),
            )
    except Exception:  # noqa: BLE001
        pass


def face_image_path(user_id: int) -> str:
    """Bir kisinin yuz fotografinin kaydedilecegi/okunacagi yol."""
    return os.path.join(FACES_DIR, f"{user_id}.jpg")


# ---------------- Giris ekranlarinin dogrulama yardimcilari ----------------

def find_by_pin(pin: str):
    """
    PIN eslesirse kisiyi, yoksa None doner (hash karsilastirmasiyla).

    Her kisinin salt'i farkli oldugu icin tek bir sorguyla bulmak
    mumkun degil; kayitli her PIN icin ayri PBKDF2 hesabi yapilir.
    Bu yuzden ARAYUZ IS PARCACIGINDA CAGRILMAZ - app/dogrula.py
    icindeki DogrulamaWorker thread'i uzerinden calisir, yoksa 50
    kisilik listede ekran saniyelerce donar.

    Bu fonksiyon artik dogrudan cagrilmaz: karar mercii app/dogrula.py
    oldu. Ag varken karari sunucu verir, ulasilamiyorsa buraya dusulur.
    """
    with _db() as con:
        rows = con.execute(
            "SELECT id, name, pin_hash, pin_salt, COALESCE(pin_iter, 0)"
            " AS pin_iter, COALESCE(rol, 'personel') AS rol"
            " FROM users WHERE pin_hash != '' AND silindi = 0"
        ).fetchall()
    for r in rows:
        if _verify_pin(pin, r["pin_hash"], r["pin_salt"], r["pin_iter"]):
            return {"id": r["id"], "name": r["name"], "rol": r["rol"]}
    return None


def pin_cakisiyor_mu(pin: str) -> bool:
    """
    Bu PIN baska bir kisiye zaten tanimli mi?

    Tanimliysa find_by_pin listede ONCE gelen kisiyi dondurur ve gecis
    yanlis kisi adina kaydedilir. Kayit sirasinda engelleniyor.
    """
    return pin.strip() != "" and find_by_pin(pin.strip()) is not None


def find_by_card(uid: str):
    """Kart UID'si eslesirse kisiyi, yoksa None doner."""
    with _db() as con:
        r = con.execute(
            "SELECT id, name, COALESCE(rol, 'personel') AS rol FROM users"
            " WHERE card = ? AND card != '' AND silindi = 0", (uid,)
        ).fetchone()
    return ({"id": r["id"], "name": r["name"], "rol": r["rol"]}
            if r else None)


def yuz_kayitlari() -> list:
    """
    Yuzu tanimli kisilerin (id, ad, vektor) listesi.

    Yalnizca YEREL YEDEK yolunda kullanilir (config.YEREL_YEDEK).
    Normal calismada bu liste sunucudan gelir; buradaki kopya tanim
    geregi gecmise aittir ve yetkisi kaldirilmis birini icerebilir.
    """
    with _db() as con:
        rows = con.execute(
            "SELECT id, name, COALESCE(yuz_vektor, '') AS yuz_vektor,"
            " COALESCE(rol, 'personel') AS rol"
            " FROM users WHERE silindi = 0"
            " AND COALESCE(yuz_vektor, '') != ''"
        ).fetchall()
    return [(r["id"], r["name"], r["yuz_vektor"], r["rol"]) for r in rows]


def has_any_pin() -> bool:
    """En az bir kisinin PIN'i var mi? (yoksa DEMO_PASSWORD'e dusulur)"""
    with _db() as con:
        r = con.execute(
            "SELECT 1 FROM users WHERE pin_hash != '' AND silindi = 0 LIMIT 1"
        ).fetchone()
    return r is not None


def has_any_user() -> bool:
    with _db() as con:
        r = con.execute("SELECT 1 FROM users WHERE silindi = 0 LIMIT 1").fetchone()
    return r is not None


# ---------------- Giris kayitlari (log) ----------------

def log_access(success: bool, method: str, user: dict = None, detail: str = ""):
    """
    Her giris denemesini kaydeder.
        method : "sifre" | "kart" | "yuz"
        user   : eslesen kisi (None ise kimlik bilinmiyor / reddedildi)
    """
    with _db() as con:
        con.execute(
            "INSERT INTO logs (ts, user_id, user_name, method, success, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_now(),
             user.get("id") if user else None,
             user.get("name", "") if user else "",
             method, int(bool(success)), detail),
        )


def kapi_olayi_kaydet(tur: str, detay: str = ""):
    """
    MC38 sensor / kilit olayini kaydeder.
        tur : "acildi" | "kapandi" | "acik_kaldi" | "izinsiz_acilma"
    """
    with _db() as con:
        con.execute(
            "INSERT INTO kapi_olaylari (ts, tur, detay) VALUES (?, ?, ?)",
            (_now(), tur, detay),
        )


def kapi_olaylari(limit: int = 100) -> list:
    """Son kapi olaylarini (en yenisi basta) doner."""
    with _db() as con:
        rows = con.execute(
            "SELECT * FROM kapi_olaylari ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_logs(limit: int = 100) -> list:
    """Son kayitlari (en yenisi basta) doner."""
    with _db() as con:
        rows = con.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def clear_logs() -> int:
    """
    Kayitlari temizler ve silinen adedi doner.

    SADECE sunucuya gonderilmis kayitlar silinir. Eski surum hepsini
    siliyordu; admin yanlis zamanda basarsa henuz gonderilmemis
    gecisler kalici olarak kayboluyordu.
    """
    with _db() as con:
        imlec = con.execute("DELETE FROM logs WHERE COALESCE(senkronize, 0) = 1")
        return imlec.rowcount


def prune_logs(gun: int = LOG_SAKLAMA_GUN) -> int:
    """
    Gonderilmis ve belirtilen gunden eski kayitlari budar.

    Aksi halde SD kart uzerinde sonsuza kadar birikirler. Sunucudaki
    arsiv kalici; buradaki sadece kuyruk ve kisa gecmistir.
    """
    sinir = (datetime.now() - timedelta(days=gun)).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as con:
        imlec = con.execute(
            "DELETE FROM logs WHERE COALESCE(senkronize, 0) = 1 AND ts < ?",
            (sinir,),
        )
        return imlec.rowcount


# ---------------- Sunucu senkronizasyonu ----------------
#
# Kart dogrulama HER ZAMAN yukaridaki find_by_card/find_by_pin ile
# yerelden yapilir. Asagidaki fonksiyonlar sadece yerel veriyi sunucuyla
# esitler; kapinin acilip acilmamasina karar vermezler.

def encode_pin(pin_hash: str, pin_salt: str, iterations: int = 0) -> str:
    """PIN ozetini sunucuda saklanacak tek metne cevirir."""
    if not pin_hash or not pin_salt:
        return ""
    return f"pbkdf2${iterations or _PBKDF2_ITERATIONS}${pin_salt}${pin_hash}"


def decode_pin(ozet: str) -> tuple:
    """
    encode_pin ciktisini (hash, salt, tur_sayisi) ucusune geri cevirir.

    Tur sayisi ozetin ICINDE tasiniyor ve okunuyor. Eski surum bu alani
    atip yerel sabiti kullaniyordu: sunucu farkli bir tur sayisiyla
    yazarsa hicbir hata gorunmeden butun PIN'ler calismaz hale gelirdi.
    """
    if not ozet:
        return "", "", 0
    parts = ozet.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2":
        return "", "", 0
    try:
        tur = int(parts[1])
    except ValueError:
        return "", "", 0
    return parts[3], parts[2], tur


def unsynced_logs(limit: int = 200) -> list:
    """Sunucuya henuz gonderilmemis kayitlar (en eskisi basta)."""
    with _db() as con:
        rows = con.execute(
            "SELECT l.*, u.sunucu_id AS personel_sunucu_id"
            " FROM logs l LEFT JOIN users u ON u.id = l.user_id"
            " WHERE COALESCE(l.senkronize, 0) = 0"
            " ORDER BY l.id LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_logs_synced(log_ids: list):
    """Sunucuya basariyla yazilan kayitlari isaretler."""
    if not log_ids:
        return
    placeholders = ",".join("?" * len(log_ids))
    with _db() as con:
        con.execute(
            f"UPDATE logs SET senkronize = 1 WHERE id IN ({placeholders})",
            list(log_ids),
        )


def pending_users() -> list:
    """Pi'de eklenmis ama sunucuya yollanmamis kisiler (PIN ozetiyle)."""
    with _db() as con:
        rows = con.execute(
            "SELECT id, name, pin_hash, pin_salt, COALESCE(pin_iter, 0)"
            " AS pin_iter, card, face,"
            " COALESCE(yuz_vektor, '') AS yuz_vektor,"
            " COALESCE(yuz_model, '') AS yuz_model,"
            " COALESCE(rol, 'personel') AS rol FROM users"
            " WHERE sunucu_id IS NULL AND silindi = 0 ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def yerel_degisenler() -> list:
    """
    Cihazda degistirilmis ve sunucuya gonderilmeyi bekleyen kisiler.

    Silinenler de buraya dahildir (silindi = 1); sunucuya
    'yetkili_mi = FALSE' olarak gonderilirler.
    """
    with _db() as con:
        rows = con.execute(
            "SELECT id, sunucu_id, name, pin_hash, pin_salt,"
            " COALESCE(pin_iter, 0) AS pin_iter, card, face, silindi,"
            " COALESCE(yuz_vektor, '') AS yuz_vektor,"
            " COALESCE(yuz_model, '') AS yuz_model,"
            " guncelleme FROM users"
            " WHERE yerelden = 1 AND sunucu_id IS NOT NULL ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def yerel_degisim_islendi(yerel_id: int, silinmisti: bool):
    """
    Sunucuya gonderimi onaylanan yerel degisikligi kapatir.

    Silinen kayit ancak BURADA gercekten yok edilir; sunucu haberdar
    oldugu icin artik geri gelme ihtimali yok.
    """
    with _db() as con:
        if silinmisti:
            con.execute("DELETE FROM users WHERE id = ?", (yerel_id,))
        else:
            con.execute(
                "UPDATE users SET yerelden = 0 WHERE id = ?", (yerel_id,)
            )


def bekleyen_is_var() -> bool:
    """Sunucuya gonderilecek bir sey var mi? (hafif kontrol)"""
    with _db() as con:
        r = con.execute(
            "SELECT 1 FROM logs WHERE COALESCE(senkronize, 0) = 0 LIMIT 1"
        ).fetchone()
        if r is not None:
            return True
        r = con.execute(
            "SELECT 1 FROM users"
            " WHERE sunucu_id IS NULL OR yerelden = 1 LIMIT 1"
        ).fetchone()
    return r is not None


def set_server_id(local_id: int, server_id: int):
    """Sunucuya yazilan kisiye sunucudaki id'sini isler."""
    with _db() as con:
        con.execute(
            "UPDATE users SET sunucu_id = ? WHERE id = ?", (server_id, local_id)
        )


def apply_server_users(server_rows: list) -> dict:
    """
    Sunucudan gelen personel listesini yerel users tablosuna uygular.

    server_rows : [{"sunucu_id", "name", "card", "pin_ozeti", "face",
                    "rol", "guncelleme"}, ...]
                  Sadece YETKILI kisiler gonderilmelidir.

    Yetkisi kaldirilan / silinen kisiler yerelde de silinir; kart iptali
    ancak bu sayede cihaza ulasir. Pi'de eklenip henuz yollanmamis
    kisiler (sunucu_id IS NULL) korunur.

    CAKISMA COZUMU (son yazan kazanir)
    ----------------------------------
    Ayni kisi hem web'den hem cihazdan degistirilmis olabilir. Iki
    taraftaki 'guncelleme' damgasi karsilastirilir:
        sunucu daha yeni  -> sunucunun degeri yazilir
        cihaz daha yeni   -> DOKUNULMAZ, bir sonraki turda cihazdaki
                             degisiklik sunucuya gonderilir
    Damga karsilastirmasi metin olarak yapilabilir cunku iki taraf da
    'YYYY-AA-GG SS:DD:SS' bicimini kullaniyor; bu bicimde alfabetik
    siralama zaman siralamasiyla aynidir.

    BILINEN ZAYIFLIK - KABUL EDILDI: cihazda donanim saati yok. Sistem
    saati aciliste agdan alinir; kayarsa cakisma karsilastirmasi da
    kayar (saat ileri giderse cihaz her cakismayi kazanir, geri kalirsa
    hicbirini). RTC modulu takilmayacagina karar verildi, dolayisiyla
    bu risk giderilmiyor.

    Pratikte etkisi sinirli: cakisma ancak AYNI kisi hem web'den hem
    cihazdan, iki senkronizasyon turu arasinda degistirilirse olusur.
    Cihazda personel duzenleme nadir bir istisna; olagan akista kayitlar
    web'den giriliyor ve tek yonlu iniyor.
    """
    gelen = {int(r["sunucu_id"]): r for r in server_rows}
    eklendi = guncellendi = silindi = cakisma = 0

    with _db() as con:
        mevcut = {
            r["sunucu_id"]: dict(r)
            for r in con.execute(
                "SELECT id, sunucu_id, name, pin_hash, pin_salt, card, face,"
                " COALESCE(yuz_vektor, '') AS yuz_vektor,"
                " COALESCE(yuz_model, '') AS yuz_model,"
                " COALESCE(rol, 'personel') AS rol,"
                " guncelleme, yerelden, silindi"
                " FROM users WHERE sunucu_id IS NOT NULL"
            )
        }

        # BOS LISTE TOPLU SILME TETIKLEMEZ.
        # Sunucudan bos liste gelmesi iki cok farkli seyin ayni
        # gorunumudur ve ayirt etmenin bir yolu yoktur:
        #   (a) gercekten kimsenin yetkisi kalmadi
        #   (b) veritabani sifirdan kuruldu / tablo bosaldi / yanlis
        #       semaya baglandik
        # (b) durumunda asagidaki silme sureci butun yerel kayitlari yok
        # eder. 17 Agustos 2026'da tam bu oldu: sunucu sifirdan kurulunca
        # Pi bir tur senkronizasyonda 3 kisiyi sessizce sildi.
        #
        # Beklemenin maliyeti sifir: YEREL_YEDEK = False iken yerel kayit
        # kimseye kapi acmiyor (karari her zaman sunucu veriyor), eskimis
        # kayit da ilk DOLU liste geldiginde normal akista temizlenir.
        if not gelen and mevcut:
            return {"eklendi": 0, "guncellendi": 0, "silindi": 0,
                    "cakisma": 0, "bos_liste": len(mevcut)}

        for sid, r in gelen.items():
            pin_hash, pin_salt, pin_iter = decode_pin(r.get("pin_ozeti", ""))
            yuz_vektor = (r.get("yuz_vektor") or "")
            yuz_model = (r.get("yuz_model") or "")
            # Bayrak vektorden turetilir: sunucu "yuz_kayitli = 1" dese
            # bile vektor yoksa kapida karsilastirilacak bir sey yok.
            face = int(bool(yuz_vektor))
            rol = (r.get("rol") or "personel")
            sunucu_damga = (r.get("guncelleme") or "")

            if sid in mevcut:
                eski = mevcut[sid]
                # Cihazdaki degisiklik henuz gonderilmediyse ve daha
                # yeniyse, sunucudan geleni uygulama - yoksa kullanicinin
                # cihazda yaptigi is sessizce geri alinir.
                if eski["yerelden"]:
                    if (eski["guncelleme"] or "") >= sunucu_damga:
                        cakisma += 1
                        continue
                if (eski["name"] != r["name"] or eski["card"] != r["card"]
                        or eski["pin_hash"] != pin_hash
                        or eski["yuz_vektor"] != yuz_vektor
                        or eski["rol"] != rol
                        or eski["face"] != face or eski["silindi"]):
                    con.execute(
                        "UPDATE users SET name = ?, pin_hash = ?, pin_salt = ?,"
                        " pin_iter = ?, card = ?, face = ?, yuz_vektor = ?,"
                        " yuz_model = ?, rol = ?, guncelleme = ?,"
                        " silindi = 0, yerelden = 0 WHERE id = ?",
                        (r["name"], pin_hash, pin_salt, pin_iter, r["card"],
                         face, yuz_vektor, yuz_model, rol,
                         sunucu_damga or _now(), eski["id"]),
                    )
                    guncellendi += 1
            else:
                con.execute(
                    "INSERT INTO users (name, pin_hash, pin_salt, pin_iter,"
                    " card, face, yuz_vektor, yuz_model, rol, created_at,"
                    " guncelleme, sunucu_id, silindi, yerelden)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
                    (r["name"], pin_hash, pin_salt, pin_iter, r["card"], face,
                     yuz_vektor, yuz_model, rol, _now(),
                     sunucu_damga or _now(), sid),
                )
                eklendi += 1

        for sid, eski in mevcut.items():
            if sid in gelen:
                continue
            # Cihazda silinmis ve henuz sunucuya bildirilmemis kayitlar
            # burada yok edilmez; bildirim gorevi senkronizasyonundur.
            if eski["yerelden"] and eski["silindi"]:
                continue
            con.execute("DELETE FROM users WHERE id = ?", (eski["id"],))
            silindi += 1

    return {"eklendi": eklendi, "guncellendi": guncellendi,
            "silindi": silindi, "cakisma": cakisma}
