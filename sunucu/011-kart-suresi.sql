-- =====================================================================
--  011 - SURELI KART: gecerlilik penceresi
--  Calistirilacak yer: SUNUCU (WIN-UCHH878J9PS), MySQL Workbench
--  VERI SILMEZ. Iki sutun ekler, mevcut davranisi aynen korur.
--  TEKRAR CALISTIRILABILIR: sutun varsa atlar, hata vermez.
-- =====================================================================
--
--  NE GELIYOR
--  ----------
--  Bir kartin ne zamandan ne zamana kadar gecerli oldugu.
--
--    gecerli_baslangic  bu andan ONCE kart gecmez   (NULL = sinir yok)
--    gecerli_bitis      bu andan SONRA kart gecmez  (NULL = sinir yok)
--
--  Ikisi de NULL: kart suresizdir. Mevcut butun kayitlar boyle
--  olusacagi icin bugunku davranis hic degismez. Sinir koymak
--  panelden BILEREK yapilan bir islem.
--
--  NEDEN SURE DEGIL, TARIH-SAAT
--  ----------------------------
--  "2 saatlik kart" bilgisini sure olarak saklamak, sayacin ne zaman
--  basladigi sorusunu cevapsiz birakir: kart verildiginde mi, ilk
--  okutuldugunda mi? Kayitta mutlak bir bitis ani durursa soru
--  ortadan kalkar - kart 14:30'a kadar gecerlidir, o kadar.
--
--  Panelde "2 saat / 4 saat / gun sonu" dugmeleri bu alani dolduruyor;
--  sure hesabi arayuzde yapilip sonuc buraya mutlak zaman olarak
--  yaziliyor.
--
--  NEDEN SADECE MISAFIRE DEGIL
--  ---------------------------
--  Alanlar role bagli degil, herkese acik. Bos birakilinca zaten
--  sinirsiz; role bagli olsaydi gecici personel ya da stajyer icin
--  ayni mekanizmayi ikinci kez yazmak gerekirdi.
--
--  KONTROL NEREDE YAPILIYOR
--  ------------------------
--  Hem sunucuda (dogrulama sorgusu) hem kapi cihazinda (yerel liste).
--  Yalnizca sunucuda olsaydi, suresi dolmus bir misafir karti ag
--  koptugu anda gecerli hale gelirdi - hem de kimse fark etmeden.
-- =====================================================================

USE kapi_sistemi;

-- --- gecerli_baslangic -------------------------------------------------
SET @var := (SELECT COUNT(*) FROM information_schema.COLUMNS
              WHERE TABLE_SCHEMA = 'kapi_sistemi'
                AND TABLE_NAME   = 'personel'
                AND COLUMN_NAME  = 'gecerli_baslangic');
SET @sql := IF(@var = 0,
    'ALTER TABLE personel ADD COLUMN gecerli_baslangic DATETIME NULL
       COMMENT ''Bu andan once kart gecmez; NULL = sinir yok''
       AFTER yetkili_mi',
    'SELECT ''gecerli_baslangic zaten var'' AS bilgi');
PREPARE st FROM @sql; EXECUTE st; DEALLOCATE PREPARE st;

-- --- gecerli_bitis -----------------------------------------------------
SET @var := (SELECT COUNT(*) FROM information_schema.COLUMNS
              WHERE TABLE_SCHEMA = 'kapi_sistemi'
                AND TABLE_NAME   = 'personel'
                AND COLUMN_NAME  = 'gecerli_bitis');
SET @sql := IF(@var = 0,
    'ALTER TABLE personel ADD COLUMN gecerli_bitis DATETIME NULL
       COMMENT ''Bu andan sonra kart gecmez; NULL = sinir yok''
       AFTER gecerli_baslangic',
    'SELECT ''gecerli_bitis zaten var'' AS bilgi');
PREPARE st FROM @sql; EXECUTE st; DEALLOCATE PREPARE st;

-- Suresi dolanlari panelde listelemek icin. Sadece dolu satirlar
-- aranacagi icin NULL'lar indeksi sisirmez.
SET @var := (SELECT COUNT(*) FROM information_schema.STATISTICS
              WHERE TABLE_SCHEMA = 'kapi_sistemi'
                AND TABLE_NAME   = 'personel'
                AND INDEX_NAME   = 'ix_personel_bitis');
SET @sql := IF(@var = 0,
    'CREATE INDEX ix_personel_bitis ON personel (gecerli_bitis)',
    'SELECT ''ix_personel_bitis zaten var'' AS bilgi');
PREPARE st FROM @sql; EXECUTE st; DEALLOCATE PREPARE st;


-- ---------------------------------------------------------------------
--  Dogrulama
-- ---------------------------------------------------------------------
SELECT id, ad_soyad, rol, gecerli_baslangic, gecerli_bitis
  FROM personel ORDER BY id;
