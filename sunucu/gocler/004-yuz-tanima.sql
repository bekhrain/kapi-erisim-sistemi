-- ============================================================
-- 004 - Yuz TANIMA icin kimlik vektoru
-- ============================================================
-- Onceki durumda 'yuz_kayitli' diye bir BAYRAK vardi; sadece "bu
-- kisinin fotografi cekildi mi" bilgisini tutuyordu. Kapida karar
-- vermek icin ise karsilastirilacak bir SAYI dizisi gerekiyor.
--
-- SFace her yuzu 128 boyutlu float32 bir vektore cevirir:
--     128 x 4 bayt = 512 bayt  ->  base64 ile 684 karakter
-- TEXT sutunu fazlasiyla yeter. BLOB yerine base64 secildi cunku
-- kayitlar zaman zaman elle (phpMyAdmin, mysqldump) incelenip
-- tasiniyor; ikili veri o araclarda bozulabiliyor.
--
-- ONEMLI - KVKK: yuz vektoru BIYOMETRIK VERIDIR ve 6698 sayili
-- kanunda "ozel nitelikli kisisel veri" sinifina girer. Ilgili
-- kisinin ACIK RIZASI alinmadan islenemez. Vektorden geriye
-- fotograf uretilemez ama bu onu anonim yapmaz: ayni kisiyi
-- baska kayitlarda eslestirmeye yarar. Yedekler de bu kapsamdadir.
--
-- Once 001, 002 ve 003 calistirilmis olmali.
-- ============================================================

USE kapi_sistemi;

-- --------------------------------------------------------------
-- 1) Vektor sutunu
-- --------------------------------------------------------------
ALTER TABLE personel
    ADD COLUMN yuz_vektor TEXT NULL
    COMMENT 'SFace 128-d kimlik vektoru, base64(float32[128])';

-- --------------------------------------------------------------
-- 2) Hangi model uretti?
-- --------------------------------------------------------------
-- Model dosyasi degistirilirse (yeni surum SFace) eski vektorler
-- yeni vektorlerle KARSILASTIRILAMAZ - sayilar ayni uzunlukta
-- oldugu icin hata da vermez, sessizce sacma benzerlik skorlari
-- uretir ve kimse iceri giremez. Etiket olmadan bu arizanin
-- sebebini bulmak cok zordur.
ALTER TABLE personel
    ADD COLUMN yuz_model VARCHAR(64) NULL
    COMMENT 'Vektoru ureten model dosyasi';

-- --------------------------------------------------------------
-- 3) Cihazin yazma yetkisi
-- --------------------------------------------------------------
-- Kayit hem web panelinden hem cihazin admin ekranindan yapilabilir;
-- cihazin bu iki sutunu guncelleyebilmesi gerekir. Yetki yine
-- SUTUN BAZINDA veriliyor: kapi_cihaz butun tabloyu degil sadece
-- bu alanlari degistirebilir.
GRANT UPDATE (ad_soyad, kart_uid, pin_ozeti, yuz_kayitli, yetkili_mi,
              guncelleme, yuz_vektor, yuz_model)
    ON kapi_sistemi.personel TO 'kapi_cihaz'@'%';

FLUSH PRIVILEGES;

-- --------------------------------------------------------------
-- 4) Kontrol
-- --------------------------------------------------------------
SELECT COUNT(*) AS toplam_personel,
       SUM(yuz_vektor IS NOT NULL AND yuz_vektor <> '') AS yuzu_kayitli
FROM personel;
