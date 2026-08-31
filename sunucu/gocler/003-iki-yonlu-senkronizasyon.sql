-- =====================================================================
--  Goc 003 : Iki yonlu senkronizasyon
--  Calistirilacak yer: SUNUCU (10.40.80.189), MySQL
--  Komut:  mysql -u root -p kapi_sistemi < 003-iki-yonlu-senkronizasyon.sql
--
--  ONEMLI: Bu goc, Pi'ye yeni sync.py atilmadan ONCE calistirilmalidir.
--          Aksi halde cihaz olmayan sutunlari guncellemeye calisir.
-- =====================================================================
--
--  NE DEGISIYOR
--  ------------
--  Simdiye kadar akis tek yonluydu: personel listesi sunucudan cihaza
--  iniyordu, cihazda yapilan silme/duzenleme sunucuya ULASMIYORDU.
--  Cihazda silinen kisi ilk liste indirmede geri geliyordu.
--
--  Artik cihaz da personel kaydini guncelleyebilir. Ancak SILEMEZ:
--  silme islemi "yetkili_mi = FALSE" olarak yazilir (yumusak silme).
--  Boylece kimin ne zaman yetkisini kaybettigi kayitli kalir.
-- =====================================================================


-- ---------------------------------------------------------------------
--  1) Cakisma cozumu icin zaman damgasi
-- ---------------------------------------------------------------------
--  Ayni kisi hem web'den hem cihazdan degistirilmis olabilir.
--  Hangi tarafin kazanacagi bu alana bakilarak belirlenir:
--  son yazan kazanir.
--
--  Sutun daha once eklendiyse asagidaki satir "Duplicate column"
--  hatasi verir; bu bir sorun degildir, atlanip devam edilebilir.

ALTER TABLE personel
    ADD COLUMN guncelleme DATETIME NULL
        COMMENT 'Kaydin son degistigi an - cakisma cozumu icin';

-- Mevcut kayitlara baslangic damgasi ver; NULL kalirsa cihaz
-- tarafindaki her degisiklik otomatik kazanirdi.
UPDATE personel SET guncelleme = NOW() WHERE guncelleme IS NULL;

-- Yoklama sorgusu (SELECT MAX(guncelleme) ...) her 30 saniyede bir
-- calisiyor; indekssiz tam tablo taramasi olurdu.
CREATE INDEX ix_personel_guncelleme ON personel (guncelleme);


-- ---------------------------------------------------------------------
--  2) Web arayuzu de damgayi guncellemeli
-- ---------------------------------------------------------------------
--  Tetikleyici kullaniliyor, cunku web tarafinda personeli guncelleyen
--  birden fazla nokta var; birini unutmak sessiz bir cakisma hatasi
--  uretirdi. Veritabani seviyesinde yapilinca unutulamaz.

DROP TRIGGER IF EXISTS trg_personel_guncelleme;
DELIMITER $$
CREATE TRIGGER trg_personel_guncelleme
BEFORE UPDATE ON personel
FOR EACH ROW
BEGIN


    -- Cihaz kendi damgasini gonderiyorsa ona dokunma; aksi halde
    -- cakisma karsilastirmasi anlamsizlasir.
    IF NEW.guncelleme <=> OLD.guncelleme THEN
        SET NEW.guncelleme = NOW();
    END IF;
END$$




DROP TRIGGER IF EXISTS trg_personel_ekleme$$
CREATE TRIGGER trg_personel_ekleme
BEFORE INSERT ON personel
FOR EACH ROW
BEGIN
    IF NEW.guncelleme IS NULL THEN
        SET NEW.guncelleme = NOW();
    END IF;
END$$
DELIMITER ;





-- ---------------------------------------------------------------------
--  3) Cihaz kullanicisinin yetkisi
-- ---------------------------------------------------------------------
--  EN AZ YETKI ILKESI KORUNUYOR:
--    - UPDATE yetkisi SADECE belirtilen sutunlarda (sutun seviyesi izin)
--    - DELETE yetkisi YOK   -> kayit silinemez
--    - DROP / ALTER yetkisi YOK
--
--  Kapi cihazi fiziksel olarak erisilebilir bir yerde duruyor. Sifresi
--  ele gecse bile saldirgan personel kaydi silemez, tablo dusuremez;
--  yapabilecegi en kotu sey yetkileri kapatmaktir ve bu, kayitlarda
--  gorulur ve web'den geri alinabilir.

GRANT UPDATE (ad_soyad, kart_uid, pin_ozeti, yuz_kayitli, yetkili_mi,
              guncelleme)
    ON kapi_sistemi.personel TO 'kapi_cihaz'@'%';

FLUSH PRIVILEGES;


-- ---------------------------------------------------------------------
--  4) Dogrulama
-- ---------------------------------------------------------------------
--  Asagidaki satirlar calistiktan sonra kontrol icin:
--
--     SHOW GRANTS FOR 'kapi_cihaz'@'%';
--         -> UPDATE (...) satiri gorunmeli, DELETE GORUNMEMELI
--
--     SELECT MAX(guncelleme), COUNT(*) FROM personel;
--         -> cihazin 30 saniyede bir sordugu yoklama sorgusu budur
