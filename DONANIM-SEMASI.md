# Kapı Donanımı — Ana Şema

Selenoid kilit + buzzer + 3'lü trafik lambası + **MC38 manyetik kapı
sensörü** eklendi. Bu belge bağlantı şemasını, yazılım mimarisini ve
davranış kurallarını tanımlar.

> Kod: [`app/hardware/kapi_donanim.py`](app/hardware/kapi_donanim.py) ·
> Ayarlar: [`app/config.py`](app/config.py) (KAPI DONANIMI bölümü) ·
> Bağlama: [`main.py`](main.py) (`MainWindow`)

---

## 1. Pin haritası (Raspberry Pi 40 pin başlık, BCM numaraları)

| İşlev | BCM | Fiziksel pin | Bağlantı notu |
|---|---|---|---|
| Selenoid kilit rölesi | GPIO17 | 11 | Röle kartının `IN` bacağı |
| Buzzer (aktif) | GPIO27 | 13 | `+` uç; `-` uç GND |
| LED kırmızı | GPIO5 | 29 | 220–330 Ω seri direnç |
| LED sarı | GPIO6 | 31 | 220–330 Ω seri direnç |
| LED yeşil | GPIO13 | 33 | 220–330 Ω seri direnç |
| MC38 manyetik sensör | GPIO26 | 37 | Diğer uç GND; iç pull-up açık |
| Ortak GND | — | 6/9/14/20/25/30/34/39 | Röle, buzzer, LED, MC38 hepsi paylaşır |

Pin numaraları `config.py` üzerinden değiştirilebilir (env değişkeni veya
`data/gizli.json`): `KAPI_ROLE_PIN`, `KAPI_BUZZER_PIN`,
`KAPI_LED_KIRMIZI_PIN`, `KAPI_LED_SARI_PIN`, `KAPI_LED_YESIL_PIN`,
`KAPI_SENSOR_PIN`.

---

## 2. Bağlantı şeması

Çizili sürüm: [`DEVRE-SEMASI.svg`](DEVRE-SEMASI.svg)

![Devre şeması](DEVRE-SEMASI.svg)

Metin sürümü:

```
                        +5V ── (Pi'den ÇEKME) ──╳
   ┌───────────────┐
   │ Raspberry Pi  │
   │               │        ┌──────────────┐        ┌───────────────┐
   │  GPIO17 ●─────┼────────│ IN  Röle 1ch │────────│ Selenoid kilit│
   │               │        │ VCC ──── 5V  │        │  (12V, ayrı   │
   │               │        │ GND ──── GND │  NO/COM│   adaptör)    │
   │               │        └──────────────┘        └──────┬────────┘
   │               │                                  12V+ ─┘   12V- ─ adaptör GND
   │               │
   │  GPIO27 ●─────┼──────────────[+ Buzzer -]──── GND
   │               │
   │  GPIO5  ●─────┼──[330Ω]──▶|──── GND     (kırmızı LED)
   │  GPIO6  ●─────┼──[330Ω]──▶|──── GND     (sarı LED)
   │  GPIO13 ●─────┼──[330Ω]──▶|──── GND     (yeşil LED)
   │               │
   │  GPIO26 ●─────┼───────────[MC38]──────── GND
   │  (iç pull-up) │        kapalıyken kontak kapalı (NC)
   │               │
   │  GND   ●──────┼──── ortak GND bara
   └───────────────┘
```

**Selenoid neden ayrı 12 V adaptörden:** selenoid ~500 mA çeker; Pi'nin
5 V hattından beslenirse Pi resetlenir. Röle kartı sadece sinyal alır,
gücü adaptörden anahtarlar. Röle ile selenoid arasına **flyback diyot**
(1N4007) konur — bobin kesilince oluşan ters gerilim röleyi yakar.

**Röle aktif-düşük:** ucuz 1 kanallı kartların çoğu `IN = GND` olunca
çeker. `KAPI_ROLE_AKTIF_DUSUK = 1` (varsayılan) bunu ayarlar. Kapı ters
çalışıyorsa (boşta açık) bu değeri `0` yapın.

**Kilit tipi:** şu an **fail-secure** varsayıldı (elektrik kesilince
kilitli kalır). Kapı bir kaçış yolu üzerindeyse
[`FIZIKSEL-GUVENLIK.md`](FIZIKSEL-GUVENLIK.md) uyarınca **fail-safe kilit
+ içeriden çıkış butonu** gerekir; çıkış butonu röleye paralel, Pi'den
bağımsız bağlanır.

---

## 3. MC38 manyetik sensör

İki telli reed switch. Bir parça kapıya, mıknatıslı parça kasaya monte
edilir. Kapı kapalıyken iki parça yan yana.

- **NC (normalde kapalı) tip** — MC38'in yaygın hâli: kapı **kapalı**
  iken kontak kapalı → GPIO26 GND'ye çekilir → `LOW`. Kapı açılınca iç
  pull-up pini `HIGH` yapar.
- `KAPI_SENSOR_NC = 0` yaparsanız NO (normalde açık) sensöre göre durum
  ters çevrilir.
- **Debounce:** `KAPI_SENSOR_DEBOUNCE` (0.15 sn) — kapı çarpması / titreşim
  tek okuma üretmesin.

---

## 4. Yazılım mimarisi

```
                 ┌──────────────────────────────────────────┐
                 │              main.py                      │
                 │           MainWindow (GUI thread)         │
                 │                                           │
   ekranlar ────▶│  _show_result(success)                    │
  (rfid/pin/yüz) │     ├─ success ─▶ kapi.giris_onaylandi()  │
                 │     └─ fail    ─▶ kapi.giris_reddedildi()  │
                 │  _open_method() ─▶ kapi.beklemede()        │
                 │  _go_menu()     ─▶ kapi.bosta()            │
                 └───────┬───────────────────────▲────────────┘
                         │ metod çağrısı         │ sinyal (queued)
                         ▼                       │
        ┌────────────────────────────────────────┴───────────┐
        │      app/hardware/kapi_donanim.py                   │
        │      KapiDonanim(QThread)  —  donanımın TEK sahibi  │
        │                                                    │
        │  Çıkışlar (GUI thread'den, anında):                 │
        │    kilit_ac / kilitle .............. GPIO17 röle    │
        │    _sadece_led(renk) ............... GPIO5/6/13     │
        │    _bip_deseni([...]) .............. GPIO27 buzzer  │
        │    QTimer: otomatik kilitle, buzzer deseni, sarı    │
        │           yanıp sönme  (hepsi GUI thread event loop)│
        │                                                    │
        │  run()  (worker thread): MC38'i 100 ms'de bir okur  │
        │    durum değişti  ─▶ kapi_durumu_degisti(bool)      │
        │    çok uzun açık  ─▶ kapi_acik_kaldi()              │
        │    onaysız açıldı ─▶ izinsiz_acilma()               │
        └────────────────────────┬───────────────────────────┘
                                 │ olay
                                 ▼
                    app/store.py  →  data/access.db
                    kapi_olaylari (ts, tur, detay)   [yalnızca yerel]
```

**Neden tek sınıf / tek sahip:** RFID okuyucudaki desenin aynısı
(bkz. [`app/hardware/rfid.py`](app/hardware/rfid.py)). Bütün GPIO
çıkışları ve sensör girişi tek `KapiDonanim` nesnesinde toplanır;
ekranlar donanıma dokunmaz, `MainWindow` üzerinden çağırır.

**Neden sinyal:** MC38 döngüsü worker thread'de koşar; QTimer'lar GUI
thread'e aittir. Worker yalnızca sinyal yayar, buzzer/LED tepkisi
sinyale bağlı slot'ta GUI thread'de verilir (başka thread'den
`QTimer.start/stop` güvenli değil).

**Mock mod:** `gpiozero` yoksa veya Raspberry Pi değilse hiçbir pin
sürülmez; eylemler `[kapi] ...` satırlarıyla terminale yazılır. Sensör
`simule_kapi(acik)` ile elle değiştirilir.

---

## 5. Trafik lambası durum makinesi

| Durum | Tetikleyen | Kırmızı | Sarı | Yeşil | Kilit | Buzzer |
|---|---|:-:|:-:|:-:|---|---|
| Boşta | `bosta()` / menü | ● | | | kapalı | — |
| Doğrulama sürüyor | `beklemede()` / metod açıldı | | ● | | kapalı | — |
| Giriş onaylandı | `giris_onaylandi()` | | | ● | **açık** (`KAPI_ACIK_SANIYE`=5 sn) | kısa bip |
| Giriş reddedildi | `giris_reddedildi()` | ● | | | kapalı | uzun bip |
| Kapı açık kaldı | sensör > `KAPI_ACIK_KALDI_SANIYE` (20 sn) | | ✦ yanıp söner | | — | aralıklı bip |
| İzinsiz açılma | onaysız / onay penceresi (`8 sn`) dışında açıldı | ● | | | — | hızlı alarm bip |

Onaydan sonra sonuç ekranı 3 sn'de menüye döner ama kilit 5 sn açık
kalır; `bosta()` kilit zamanlayıcısı çalışırken kilide **dokunmaz**,
süre dolunca `_otomatik_kilitle()` kilitler ve lambayı kırmızıya alır.

---

## 6. Ayar özeti (`config.py` → env / `data/gizli.json`)

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `KAPI_ROLE_PIN` | 17 | Selenoid röle pini |
| `KAPI_ROLE_AKTIF_DUSUK` | 1 | Röle `IN=GND` ile mi çeker |
| `KAPI_ACIK_SANIYE` | 5 | Onaydan sonra kilit ne kadar açık |
| `KAPI_BUZZER_PIN` | 27 | Buzzer pini |
| `KAPI_LED_KIRMIZI_PIN` / `_SARI_` / `_YESIL_` | 5 / 6 / 13 | Trafik lambası |
| `KAPI_SENSOR_PIN` | 26 | MC38 giriş pini |
| `KAPI_SENSOR_NC` | 1 | MC38 normalde-kapalı mı |
| `KAPI_SENSOR_DEBOUNCE` | 0.15 | Sensör titreşim filtresi (sn) |
| `KAPI_ACIK_KALDI_SANIYE` | 20 | "Açık kaldı" uyarı eşiği |
| `KAPI_ONAY_PENCERE_SANIYE` | 8 | Onaydan sonra meşru açılma penceresi |

---

## 7. Yapılacaklar

- [ ] `kapi_olaylari` tablosunu sunucu şemasına ve web paneline bağlamak
      (şu an yalnızca `data/access.db` içinde yerel).
- [ ] Flyback diyot + röle/selenoid devresini fiziksel kurmak (12 V
      adaptör bekleniyor — bkz. `FIZIKSEL-GUVENLIK.md`).
- [ ] Kaçış yolu kararı: fail-safe kilit + içeriden çıkış butonu mu?
- [ ] Pi'de pin doğrulaması: `gpiozero` ile her çıkışı tek tek test et.
