# Kurulum (PC / Windows)

Bu belge sistemi **tek bir bilgisayarda** ayağa kaldırmak içindir:
geliştirme, deneme ve sunum. Raspberry Pi ve gerçek sunucu kurulumu ayrı
belgededir: [`ISLETIM-SISTEMI.md`](ISLETIM-SISTEMI.md).

Üç kademe vardır. Ne yapmak istediğinize göre birinde durabilirsiniz.

| Kademe | Ne çalışır | Gereken |
|---|---|---|
| 1 | Kapı arayüzü, PIN, yerel kayıt | PyQt5, opencv-python, numpy |
| 2 | + Yüz tanıma | İki ONNX model dosyası |
| 3 | + MySQL sunucu ve web paneli | MySQL/MariaDB, Flask, mysql-connector-python |

Ön koşul: Python 3 (PATH'te olmalı). Kod Python 3.14 ile denendi.

---

## 1. Kapı arayüzü (donanımsız "mock" mod)

Bilgisayar Raspberry Pi olmadığı için `app/config.py` içindeki
`_is_raspberry_pi()` `False` döner ve uygulama kendiliğinden mock moda
geçer. Bu modda:

- GPIO sürülmez; selenoid, buzzer ve trafik lambası eylemleri terminale
  yazılır (`app/hardware/kapi_donanim.py`),
- USB RFID okuyucu yerine "Kart Simüle Et" butonu çıkar
  (`app/hardware/rfid.py`),
- pencere tam ekran değil, 600x1024 dikey açılır.

```bash
pip install PyQt5 opencv-python numpy
python main.py
```

İlk açılışta `data/access.db` (SQLite) ve `data/faces/` kendiliğinden
oluşturulur; elle bir şey hazırlamak gerekmez.

| Giriş | Değer | Nerede tanımlı |
|---|---|---|
| Demo PIN | `123456` | `KAPI_DEMO_PAROLA`, yalnızca hiç kayıtlı PIN yokken geçerli |
| Admin paneli PIN | `0000` | `KAPI_ADMIN_PIN` |
| Tam ekran / çıkış | `F11` / `ESC` | `main.py` |

**Sunucu yokken doğrulama:** karar önce sunucudan istenir, ulaşılamazsa
`YEREL_YEDEK = True` olduğu için yerel SQLite'a düşülür. Bu yüzden
sunucusuz bir PC'de ilk denemede `DOGRULAMA_TIMEOUT` (5 sn) kadar bir
bekleme olur; sonraki `DOGRULAMA_HATA_BEKLEME` (15 sn) boyunca sunucu
tekrar denenmez, doğrudan yerelden karar verilir.

Pi üzerinde de mock moda zorlamak için: `FORCE_MOCK=1 python main.py`

---

## 2. Yüz tanıma

Model dosyaları pip ile gelmez ve sürüm kontrolüne girmez. `data/modeller/`
klasörünü oluşturup iki dosyayı içine koyun:

| Dosya | Boyut |
|---|---|
| `face_detection_yunet_2023mar.onnx` | ~230 KB |
| `face_recognition_sface_2021dec.onnx` | ~37 MB |

Kaynak: <https://github.com/opencv/opencv_zoo>

Dosyalar eksikse yüz ekranı çökmez, `Model dosyalari eksik: ...` uyarısı
verir (`app/yuz.py`). Yollar `app/config.py -> YUZ_MODEL_DIR` ile
değiştirilebilir. Kamera olarak bilgisayarın kendi webcam'i kullanılır.

---

## 3. MySQL sunucu ve web paneli (aynı bilgisayarda)

```bash
pip install mysql-connector-python flask
```

### 3.1 Veritabanı

MySQL veya MariaDB kurun, şemayı **sırayla** yükleyin — dosyalar birbirinin
üzerine göç ettiği için sıra önemlidir:

```bash
mysql -u root -p < sunucu/000-SIFIRDAN-KURULUM.sql
mysql -u root -p kapi_sistemi < sunucu/005-coklu-kapi.sql
mysql -u root -p kapi_sistemi < sunucu/006-calisma-suresi.sql
mysql -u root -p kapi_sistemi < sunucu/007-bildirimler.sql
mysql -u root -p kapi_sistemi < sunucu/008-cihaz-durumu.sql
mysql -u root -p kapi_sistemi < sunucu/009-kisi-rolu.sql
mysql -u root -p kapi_sistemi < sunucu/010-kapi-yetkileri.sql
mysql -u root -p kapi_sistemi < sunucu/011-kart-suresi.sql
```

İki veritabanı kullanıcısı vardır: `kapi_cihaz` (kapı cihazı, kısıtlı
yetki) ve `kapi_web` (panel, tam yetki).

### 3.2 Ayarlar

```bash
copy data\gizli.ornek.json data\gizli.json
```

`data/gizli.json` içinde en az şunları düzeltin:

| Anahtar | Tek bilgisayarda değer |
|---|---|
| `KAPI_DB_HOST` | `127.0.0.1` (varsayılan `10.40.80.189` okuldaki sunucudur) |
| `KAPI_DB_PASSWORD` | `kapi_cihaz` kullanıcısının şifresi |
| `KAPI_ADMIN_PIN` | Yeni admin PIN'i |

Değerler şu sırayla okunur: **ortam değişkeni → `data/gizli.json` → koddaki
varsayılan**. `data/gizli.json` `.gitignore` içindedir, sürüm kontrolüne
girmez.

### 3.3 Paneli başlatma

```bash
python web/app.py
```

Tarayıcı: <http://127.0.0.1:5000>

| Ayar | Varsayılan | Ortam değişkeni |
|---|---|---|
| Panel giriş parolası | `admin1234` | `KAPI_PANEL_PAROLA` |
| Panelin MySQL şifresi | `web1234` | `KAPI_WEB_PASSWORD` |

Panel `0.0.0.0:5000` dinler, yani LAN'daki diğer makinelerden de erişilir.
Windows'ta açılışta kendiliğinden başlaması için:
`sunucu\panel-baslat.bat` ve `sunucu\panel-gorev-kur.ps1`
(bkz. [`ISLETIM-SISTEMI.md`](ISLETIM-SISTEMI.md) § 3.3).

### 3.4 Birlikte çalıştırma

İki ayrı terminal gerekir:

```bash
python web/app.py     # 1. terminal: panel
python main.py        # 2. terminal: kapı arayüzü
```

Panelden eklenen kişi/kart, senkronizasyon turu gelince (olay tetikli,
gecikme ~0; boşta yoklama 30 sn) kapıda geçerli olur.

---

## Sorun giderme

| Belirti | Sebep / çözüm |
|---|---|
| `ModuleNotFoundError: PyQt5` | `pip install -r requirements.txt` |
| Yüz ekranında "Model dosyalari eksik" | Kademe 2'deki ONNX dosyaları `data/modeller/` içinde değil |
| PIN girişi ~5 sn bekliyor | Normal: sunucuya ulaşılamıyor, yerele düşülüyor (`DOGRULAMA_TIMEOUT`) |
| Panel `Access denied for user 'kapi_web'` | `KAPI_WEB_PASSWORD` ortam değişkeni ayarlanmamış |
| Kapı arayüzü panelden ekleneni görmüyor | `KAPI_DB_HOST` hâlâ `10.40.80.189`; `data/gizli.json` içinde `127.0.0.1` yapın |
| Demo PIN `123456` çalışmıyor | Kayıtlı en az bir PIN var; demo parola yalnızca hiç PIN yokken geçerlidir |

## Bilinen kısıtlar

Tam liste: [`FIZIKSEL-GUVENLIK.md`](FIZIKSEL-GUVENLIK.md). Kurulum
açısından önemli olanlar: MySQL bağlantısı ve web paneli şifresizdir
(HTTP); canlılık tespiti olmadığı için yüz tanıma kart/PIN yerine değil
yanında kullanılmalıdır.
