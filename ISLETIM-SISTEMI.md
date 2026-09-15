# İşletim Sistemi ve Kurulum Ortamı

Sistem iki makinede çalışır. Bu belge her ikisinin işletim sistemi
düzeyindeki kurulumunu, açılış sırasını ve ağ bağlantısını tanımlar.

| Makine | İşletim sistemi | Rol | Ağ |
|---|---|---|---|
| Raspberry Pi 5 | Raspberry Pi OS (Bookworm, 64 bit, masaüstü) | Kapı cihazı: arayüz, donanım, yerel yedek | 10.40.81.4 |
| Sunucu `WIN-UCHH878J9PS` | Windows | MySQL veritabanı + Flask web paneli | 10.40.80.189 |

> Donanım bağlantıları: [`DONANIM-SEMASI.md`](DONANIM-SEMASI.md) ·
> Devre şeması: [`DEVRE-SEMASI.svg`](DEVRE-SEMASI.svg) ·
> Güvenlik gereksinimleri: [`FIZIKSEL-GUVENLIK.md`](FIZIKSEL-GUVENLIK.md)

---

## 1. Genel yapı

```
┌──────────────────────────────┐          ┌──────────────────────────────┐
│  Raspberry Pi 5 (10.40.81.4) │          │  Sunucu (10.40.80.189)       │
│  Raspberry Pi OS Bookworm    │          │  Windows                     │
│                              │  MySQL   │                              │
│  Wayland oturumu (autologin) │  3306    │  MySQL / MariaDB             │
│   └─ autostart: kapi.desktop │◄────────►│   kapi_sistemi veritabanı    │
│       └─ kapi-baslat.sh      │          │                              │
│           └─ python main.py  │          │  Zamanlanmış Görev           │
│              (PyQt5 arayüz)  │          │   KapiWebPaneli (SYSTEM)     │
│                              │          │   └─ panel-baslat.bat        │
│  SQLite: data/access.db      │          │       └─ python web/app.py   │
│  GPIO: kilit, buzzer, LED,   │          │          (Flask, port 5000)  │
│        MC38 sensör           │          │                              │
│  USB: RFID okuyucu, kamera   │          └──────────────▲───────────────┘
│  HDMI: 7" dokunmatik (dikey) │                         │ HTTP :5000
└──────────────────────────────┘                         │
                                            Yönetici tarayıcısı (LAN)
```

- Pi'de **internet yok**; yalnızca yerel ağ üzerinden sunucuya ulaşır.
  Paket ve model dosyaları PC'den `scp` / `wget` ile taşınır.
- Sunucu, yetki listesinin tek doğru kopyasıdır. Pi ağ koptuğunda
  `YEREL_YEDEK=True` ile SQLite'tan karar vermeye devam eder.

---

## 2. Raspberry Pi

### 2.1 İşletim sistemi

- **Raspberry Pi OS Bookworm (64 bit, masaüstü sürümü).** Masaüstü
  şart: uygulama PyQt5 ile grafik arayüz çizer.
- Görüntü sunucusu **Wayland** (Bookworm varsayılanı). Bu, otomatik
  başlatma yönteminin neden `systemd` değil de oturum içi autostart
  olduğunu belirler (bkz. 2.5).
- GPIO: `gpiozero` ve altındaki `lgpio` pin fabrikası Pi OS ile hazır
  gelir; ayrı kurulum gerekmez.
- Kullanıcı: `kapi`. Depo `/home/kapi/kapi` altında; sanal ortam
  `/home/kapi/rfidtest`.

### 2.2 İlk kurulum adımları

```bash
# 1) raspi-config
sudo raspi-config
#   System Options  > Boot / Auto Login > Desktop Autologin
#   Interface Options > Camera            (Pi kamera kullanılıyorsa)
#   Localisation    > Timezone > Europe/Istanbul

# 2) Kullanıcıyı input grubuna al (USB RFID okuyucu /dev/input'tan okunur)
sudo usermod -aG input kapi

# 3) Sanal ortam ve paketler (paketler PC'den taşınır, Pi'de internet yok)
python3 -m venv ~/rfidtest
source ~/rfidtest/bin/activate
pip install -r ~/kapi/requirements.txt

# 4) Yüz tanıma modelleri (git dışı, ~37 MB) -> ~/kapi/data/modeller/
#    face_detection_yunet_2023mar.onnx, face_recognition_sface_2021dec.onnx

# 5) Sırlar
cp ~/kapi/data/gizli.ornek.json ~/kapi/data/gizli.json
chmod 600 ~/kapi/data/gizli.json
#    KAPI_DB_PASSWORD, KAPI_ADMIN_PIN doldurulur
```

### 2.3 Ekran (7" dokunmatik, dikey)

Panel fiziksel olarak **dik** monte edilir; uygulama 600×1024 çizer
(`app/config.py` → `SCREEN_WIDTH/HEIGHT`). İşletim sistemi tarafında
görüntünün 90° döndürülmesi gerekir:

- Masaüstünde **Menü › Tercihler › Screen Configuration** ile çıkışı
  seçip döndürme (portrait) uygulanır ve kaydedilir; ya da
- komut satırından: `wlr-randr --output HDMI-A-1 --transform 90`
  (çıkış adı `wlr-randr` ile listelenir).

Dokunmatik giriş, ekran döndürüldüğünde Wayland tarafından ekranla
birlikte döndürülür; ayrı kalibrasyon gerekmedi.

### 2.4 USB aygıtlar

| Aygıt | Nasıl görünür | Yazılımda |
|---|---|---|
| 13,56 MHz RFID okuyucu | `lsusb`: `ffff:0035 IC Reader`; klavye taklidi | `/dev/input/by-id/*IC_Reader*event-kbd`, `EVIOCGRAB` ile özel mod (`app/hardware/rfid.py`) |
| USB / Pi kamera | `/dev/video0` | OpenCV `VideoCapture` (`app/hardware/camera.py`) |
| 7" dokunmatik | HDMI + USB (dokunma) | Wayland |

`by-id` yolu kullanılır; `/dev/input/eventN` numarası her açılışta
kayabilir.

### 2.5 Açılış sırası ve otomatik başlatma

```
Elektrik ──► Pi OS boot ──► Desktop Autologin (kapi) ──► Wayland oturumu
   ──► ~/.config/autostart/kapi.desktop ──► pi/kapi-baslat.sh
        ──► venv aktif ──► python main.py (tam ekran arayüz)
             │
             └─ çökerse: 3 sn bekle, yeniden başlat;
                art arda çökmede bekleme ikiye katlanır (en fazla 60 sn)
```

Kurulum:

```bash
mkdir -p ~/.config/autostart
cp ~/kapi/pi/kapi.desktop ~/.config/autostart/
chmod +x ~/kapi/pi/kapi-baslat.sh
```

**Neden `systemd` servisi değil:** uygulama grafik arayüzlü;
`WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR` gibi değişkenler ancak oturum
açılınca oluşur ve sistem servisine sabit yazılamaz. Autostart girdisi
zaten oturumun içinde çalışır. Ayrıntı: [`pi/kapi.desktop`](pi/kapi.desktop).

Günlük: `~/kapi/data/kapi.log` (her başlangıç / çıkış damgalı).

### 2.6 Saat

Pi'de donanım saati (RTC) **yok**; sistem saati açılışta NTP'den
gelir. Pi'de internet olmadığı için NTP kaynağı yerel ağdaki sunucu
olmalıdır (`/etc/systemd/timesyncd.conf` → `NTP=10.40.80.189` ya da
kurum NTP sunucusu). Senkronizasyon çakışma çözümü (`guncelleme`
damgası, "son yazan kazanır") saate bağlıdır; bu zayıflık bilerek
kabul edildi ve kod yorumlarına yazıldı.

### 2.7 Pi'de sık kullanılan komutlar

```bash
# Uygulamayı elle başlat (autostart dışında)
~/kapi/pi/kapi-baslat.sh

# Mock modda çalıştır (donanım sürülmez)
FORCE_MOCK=1 python ~/kapi/main.py

# RFID okuyucu görünüyor mu
lsusb | grep -i reader
ls /dev/input/by-id/

# Günlük
tail -f ~/kapi/data/kapi.log

# RFID okuyucuyu uygulamadan bağımsız dene
python ~/kapi/kart_test.py

# GPIO çıkışlarını tek tek dene (gpiozero)
python3 -c "from gpiozero import LED; import time; l=LED(13); l.on(); time.sleep(1); l.off()"
```

---

## 3. Sunucu (Windows)

### 3.1 Bileşenler

| Bileşen | Not |
|---|---|
| Python 3 | PATH'te olmalı; Zamanlanmış Görev tam yolu gömer |
| MySQL / MariaDB | Port 3306, veritabanı `kapi_sistemi` |
| Flask + waitress | Web paneli, port 5000, `0.0.0.0` (LAN'dan erişilir) |
| Zamanlanmış Görev `KapiWebPaneli` | Açılışta, SYSTEM hesabı, süre sınırı yok |

Panel çalışma kopyası: `C:\Users\Administrator\Desktop\kapi-web\`.
Bu kopya geliştirme deposundan **ayrıdır**; değişiklik elle aktarılır.

### 3.2 Veritabanı kurulumu

```sql
-- MySQL istemcisinde, sırayla:
SOURCE sunucu/000-SIFIRDAN-KURULUM.sql;
SOURCE sunucu/005-coklu-kapi.sql;
SOURCE sunucu/006-calisma-suresi.sql;
SOURCE sunucu/007-bildirimler.sql;
SOURCE sunucu/008-cihaz-durumu.sql;
SOURCE sunucu/009-kisi-rolu.sql;
SOURCE sunucu/010-kapi-yetkileri.sql;
SOURCE sunucu/011-kart-suresi.sql;
```

İki veritabanı kullanıcısı vardır: `kapi_cihaz` (Pi, kısıtlı yetki) ve
`kapi_web` (panel, tam yetki). Şifreler `data/gizli.json` ve
`KAPI_WEB_PASSWORD` ortam değişkeni ile verilir, koda yazılmaz.

### 3.3 Web panelinin açılışta başlatılması

Yönetici PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File sunucu\panel-gorev-kur.ps1
```

**Neden Başlangıç klasörü değil:** kısayol ancak biri oturum açınca
çalışır. Sunucu elektrik kesintisinden sonra kendi başına açıldığında
kimse oturum açmaz ve panel ölü kalır. Zamanlanmış Görev sistem
açılışında, oturumdan bağımsız tetiklenir.
Ayrıntı: [`sunucu/panel-gorev-kur.ps1`](sunucu/panel-gorev-kur.ps1).

Kontrol:

```powershell
Get-ScheduledTask -TaskName KapiWebPaneli
Start-ScheduledTask -TaskName KapiWebPaneli
```

### 3.4 Güvenlik duvarı

Windows Defender Güvenlik Duvarı'nda iki gelen kural gerekir:

| Port | Protokol | Kim erişir |
|---|---|---|
| 3306 | TCP | Yalnızca Pi (10.40.81.4) — kaynak IP ile kısıtlanmalı |
| 5000 | TCP | Yönetici bilgisayarları (LAN) |

```powershell
New-NetFirewallRule -DisplayName "Kapi MySQL (Pi)" -Direction Inbound `
    -Protocol TCP -LocalPort 3306 -RemoteAddress 10.40.81.4 -Action Allow
New-NetFirewallRule -DisplayName "Kapi Web Paneli" -Direction Inbound `
    -Protocol TCP -LocalPort 5000 -Action Allow
```

### 3.5 Çalışma süresi takibi

Panel dakikada bir `sunucu_oturum` tablosuna nabız atar ve işletim
sisteminin açılış damgasını yazar. Damgalar arasındaki boşluk kesinti
demektir; `acilis` sütunu "makine mi çöktü, program mı çöktü" sorusunu
ayırt eder. Pi'nin kopuk kaldığı aralıklar `cihaz_kesinti` tablosunda
tutulur (`sunucu/006-calisma-suresi.sql`, `008-cihaz-durumu.sql`).

---

## 4. Ağ

| Uç | IP | Port | Yön |
|---|---|---|---|
| Pi → Sunucu | 10.40.81.4 → 10.40.80.189 | 3306 | MySQL (senkronizasyon + doğrulama) |
| Yönetici → Sunucu | LAN → 10.40.80.189 | 5000 | Web paneli (HTTP) |
| Pi → NTP | 10.40.81.4 → NTP kaynağı | 123/UDP | Saat |

Pi ile sunucu farklı alt ağlarda (10.40.81.x / 10.40.80.x); aradaki
yönlendirmenin 3306'ya izin vermesi gerekir. Pi'den test:

```bash
nc -zv 10.40.80.189 3306
```

Senkronizasyon üç kademelidir (`app/sync.py`): olay olunca anında,
boşta 30 sn'de bir hafif "değişti mi" yoklaması, 12 saatte bir tam tur.
Sunucuya 5 sn içinde ulaşılamazsa karar yerel SQLite'tan verilir ve
sonraki 15 sn sunucu denenmez.

**Bilinen kısıt:** MySQL bağlantısı ve web paneli şifresiz (HTTP).
Aynı ağdaki biri trafiği okuyabilir. Ayrıntı: `FIZIKSEL-GUVENLIK.md`.

---

## 5. Dosya taşıma (Pi'de internet yok)

PC'den (10.40.80.159) Pi'ye ya da sunucuya dosya aktarımı:

```bash
# PC'de, depo klasöründe
python -m http.server 8000
```

```bash
# Pi'de
wget http://10.40.80.159:8000/data/modeller/face_recognition_sface_2021dec.onnx \
     -O ~/kapi/data/modeller/face_recognition_sface_2021dec.onnx
# ya da PC'den
scp -r app main.py kapi@10.40.81.4:~/kapi/
```

```powershell
# Sunucuda
Invoke-WebRequest http://10.40.80.159:8000/web/app.py -OutFile C:\Users\Administrator\Desktop\kapi-web\web\app.py
```

---

## 6. Sorun giderme

| Belirti | Bakılacak yer |
|---|---|
| Pi açıldı, arayüz gelmedi | `~/.config/autostart/kapi.desktop` var mı, `kapi-baslat.sh` çalıştırılabilir mi, `data/kapi.log` son satırlar |
| Kart okununca PIN kutusuna yazıyor | Okuyucu `EVIOCGRAB` ile alınamamış: kullanıcı `input` grubunda mı, başka bir süreç aygıtı tutuyor mu |
| Kart hiç okunmuyor | `ls /dev/input/by-id/` içinde `IC_Reader` var mı; `lsusb` |
| Yüz ekranı açılmıyor | `data/modeller/` içinde iki ONNX dosyası var mı; `opencv-python >= 4.5.4` |
| Kapı yerelden karar veriyor (admin ekranında uyarı) | Sunucu 3306 erişilebilir mi (`nc -zv`), güvenlik duvarı, `gizli.json` şifresi |
| Panel açılışta gelmiyor | `Get-ScheduledTask KapiWebPaneli`, görevin Python tam yolu doğru mu, `KAPI_WEB_PASSWORD` tanımlı mı |
| Röle ters çalışıyor (boşta açık) | `KAPI_ROLE_AKTIF_DUSUK` değerini ters çevir |
| Kapı kapalıyken "açık" görünüyor | `KAPI_SENSOR_NC` değerini ters çevir; MC38 parçaları hizalı mı |
