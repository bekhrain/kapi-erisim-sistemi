# Fiziksel Güvenlik Gereksinimi: Kaçış Yolu

**Durum: KARŞILANMADI.** Sistem şu anda tek yollu. Bu belge neyin
eksik olduğunu ve nasıl kapatılacağını tanımlar.

## Sorun

Kapıyı açan tek mekanizma yazılım. Şunlardan **herhangi biri** olursa
kapı açılmaz:

| Arıza | Sonuç |
|---|---|
| Pi'ye elektrik gelmiyor | Kapı ölü |
| SD kart bozuldu | Kapı ölü |
| Röle veya kilit devresi arızalandı | Kapı ölü |
| Uygulama çöktü ve yeniden başlayamadı | Kapı ölü |
| Ağ koptu **ve** `YEREL_YEDEK = False` | Kapı kimseyi almaz |

İlk dördü tek bir bileşenin arızasıyla oluyor. Yedeği yok.

## Neden bu sadece bir kolaylık meselesi değil

Kapı bir **çıkış** yolu üzerindeyse, elektrik kesintisinde insanların
içeride kalması yangın güvenliği sorunudur. Binaların yangın
yönetmeliklerinde kaçış yolundaki kapıların, güç olmadan da elle
açılabilmesi beklenir. Bu, projenin "güzel olurdu" listesinde değil,
kurulum öncesi karşılanması gereken bir koşuldur.

Kapı sadece bir **giriş** kontrolüyse (içeriden kolla serbestçe
açılıyorsa) risk yalnızca dışarıda kalmaktır; yine de çözülmeli ama
aciliyeti farklıdır.

**Bu proje kapsamında hangisi olduğu henüz netleşmedi — kurulum
yerindeki kapının içeriden nasıl açıldığı ölçülmeli.**

## Çözüm seçenekleri

### A) Mekanik anahtar (en basit, önerilen)

Elektrikli kilidin yanında geleneksel bir silindir. Elektrik olmasa da
anahtarla açılır.

- **Artısı:** hiçbir şeye bağımlı değil, ek arıza noktası yok
- **Eksisi:** anahtarın kimde olduğu ayrı bir yönetim işi

### B) İçeriden çıkış butonu (talep butonu)

Kapının iç tarafında, röleyi doğrudan besleyen bir buton. Pi'den
**geçmez** — kabloyla röleye paralel bağlanır.

- **Artısı:** içeriden çıkış her koşulda çalışır
- **Eksisi:** dışarıdan girişi çözmez; ayrıca butonun Pi'den bağımsız
  olması şart, aksi halde aynı arızadan etkilenir

### C) Kilit tipi seçimi: fail-safe / fail-secure

| Tip | Elektrik kesilince | Uygun olduğu yer |
|---|---|---|
| **Fail-safe** (güç kesilince açılır) | Kapı **açılır** | Kaçış yolları |
| **Fail-secure** (güç kesilince kilitli kalır) | Kapı **kilitli** | Değerli eşya odaları |

Kaçış yolu üzerindeki bir kapıda fail-safe kilit, kaçış sorununun
büyük kısmını donanım seviyesinde çözer. Bunun bedeli, elektrik
kesintisinin aynı zamanda güvenlik kesintisi olmasıdır.

## Öneri

1. Kurulum yerindeki kapının kaçış yolu üzerinde olup olmadığı
   netleştirilsin (bina sorumlusuna sorulacak).
2. Kaçış yolundaysa: **fail-safe kilit + içeriden çıkış butonu**.
3. Değilse: **mekanik anahtar** yeterli, ama yine de olmalı.
4. Seçilen çözüm sunumda açıkça anlatılsın — "arıza durumunda ne olur"
   sorusu gelecektir ve cevabın "hiç düşünmedik" olmaması gerekir.

## İlgili diğer açıklar

Bu belge kaçış yolunu kapsıyor; aşağıdakiler ayrı ve hâlâ açık:

- **Canlılık tespiti yok.** Telefonda açılan bir fotoğraf yüz tanımayı
  geçebilir. Yüz, kart ve PIN'in **yerine değil yanında** bir kolaylık
  olarak sunulmalı.
- **MySQL bağlantısı şifresiz.** Kapı cihazı ile sunucu arasındaki
  trafik aynı ağdaki biri tarafından okunabilir.
- **Web paneli HTTP.** Panel şifresi ağda düz metin geçiyor.
- **Röle/kilit devresi henüz kurulmadı.** 12 V adaptör bekleniyor;
  bu belgedeki kararlar devre kurulmadan önce verilmeli, sonra
  değiştirmek kablolamayı sökmek demek.
