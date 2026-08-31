-- =====================================================================
--  Goc 002 : Gecis kayitlarinda cift kayit onleme
--  Calistirilacak yer: SUNUCU (10.40.80.189), MySQL
--  Komut:  mysql -u root -p kapi_sistemi < 002-cift-kayit-onleme.sql
-- =====================================================================
--
--  SORUN
--  -----
--  Pi, kayitlari once sunucuya yaziyor (MySQL commit), sonra kendi
--  SQLite'inda "gonderildi" isareti koyuyor. Bu iki adim arasinda
--  elektrik kesilirse veya uygulama cokerse, ayni kayitlar bir sonraki
--  turda TEKRAR gonderiliyor. Veritabaninda engel olmadigi icin ayni
--  gecis raporlarda iki kez gorunuyordu.
--
--  COZUM
--  -----
--  Her kaydin Pi'deki yerel id'si de saklanir ve (cihaz_id, yerel_log_id)
--  ikilisi benzersiz yapilir. Pi tarafi INSERT IGNORE kullandigi icin
--  tekrar gonderilen kayit sessizce reddedilir, hata olusmaz.
--
--  NOT: cihaz_id sutunu da anahtara giriyor; ilerideki ikinci kapi
--  cihazi kendi 1, 2, 3... numaralarini kullanabilsin diye.
-- =====================================================================

ALTER TABLE gecis_kaydi
    ADD COLUMN yerel_log_id INT NULL
        COMMENT 'Kaydin Pi uzerindeki SQLite id degeri (tekrari onlemek icin)';

-- Mevcut satirlarda yerel_log_id NULL. MySQL'de UNIQUE indeks birden
-- fazla NULL'a izin verir, dolayisiyla eski kayitlar sorun cikarmaz.
CREATE UNIQUE INDEX ux_gecis_cihaz_yerel
    ON gecis_kaydi (cihaz_id, yerel_log_id);

-- Rapor sorgulari tarih araligina gore filtreliyor; bu indeks olmadan
-- kayit sayisi buyudukce panel yavaslar.
CREATE INDEX ix_gecis_zaman ON gecis_kaydi (zaman);

-- Kart numarasi iki kisiye tanimlanamasin. Ayni kartla iki kisi varsa
-- hangisinin gectigi belirsiz kalir ve gecis yanlis kisiye yazilir.
-- (Bos/NULL kartlar birden fazla olabilir; UNIQUE NULL'a karismaz.)
ALTER TABLE personel
    ADD CONSTRAINT ux_personel_kart UNIQUE (kart_uid);
