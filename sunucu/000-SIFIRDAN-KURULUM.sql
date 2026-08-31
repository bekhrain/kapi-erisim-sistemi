-- =====================================================================
--  000 - SIFIRDAN KURULUM  (tek dosya, son hali)
--  Calistirilacak yer: SUNUCU (WIN-UCHH878J9PS / 10.40.80.189), MySQL
-- =====================================================================
--
--  NEDEN TEK DOSYA
--  ---------------
--  Once 001 (GUN-03 icindeki betik), sonra 002, 003, 004 sirayla
--  calistiriliyordu. Bir goc yarim kalinca veya iki kez calisinca
--  "ERROR 1060 Duplicate column" gibi hatalar cikiyor ve veritabaninin
--  hangi asamada oldugu belirsizlesiyor. Bu dosya o zinciri bitirir:
--  sonucta olusan sema, 001+002+003+004'un toplamiyla AYNIDIR.
--
--  sunucu/gocler/ altindaki 002, 003, 004 dosyalari ARTIK
--  CALISTIRILMAZ. Staj defterindeki gecmisi belgeledikleri icin
--  duruyorlar.
--
--  !!! DIKKAT - VERI SILER !!!
--  Bu betik kapi_sistemi veritabanini DUSURUP yeniden kurar. Mevcut
--  personel ve gecis kayitlari GIDER. Once mysqldump ile yedek alin
--  (adim adim kurulum notlarindaki 0. adim).
-- =====================================================================


-- ---------------------------------------------------------------------
--  1) Veritabani
-- ---------------------------------------------------------------------
DROP DATABASE IF EXISTS kapi_sistemi;

CREATE DATABASE kapi_sistemi
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_turkish_ci;

USE kapi_sistemi;


-- ---------------------------------------------------------------------
--  2) cihaz
-- ---------------------------------------------------------------------
--  Birden fazla kapi olacaksa her biri burada bir satir. Cihaz kendi
--  son_iletisim damgasini gunceller; panelde "cihaz ayakta mi"
--  bilgisi bundan okunur.
CREATE TABLE cihaz (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  cihaz_adi     VARCHAR(50)  NOT NULL,
  konum         VARCHAR(100),
  son_iletisim  DATETIME     NULL
) ENGINE=InnoDB;


-- ---------------------------------------------------------------------
--  3) personel
-- ---------------------------------------------------------------------
--  kart_uid UNIQUE : ayni kart iki kisiye tanimlanamaz, yoksa gecis
--                    yanlis kisiye yazilir. NULL birden fazla olabilir
--                    (karti olmayan, sadece PIN kullanan personel).
--  guncelleme      : cakisma cozumu - son yazan kazanir. Cihaz da
--                    personel guncelleyebiliyor.
--  yuz_kayitli     : panelde gosterilen bayrak.
--  yuz_vektor      : SFace 128-d kimlik vektoru, base64(float32[128]).
--                    128 x 4 = 512 bayt -> base64 684 karakter.
--                    BLOB yerine base64: kayitlar zaman zaman elle
--                    (Workbench, mysqldump) inceleniyor, ikili veri
--                    o araclarda bozulabiliyor.
--  yuz_model       : vektoru ureten model dosyasinin adi. Model surumu
--                    degisirse eski vektorler yenilerle
--                    KARSILASTIRILAMAZ; uzunluk ayni oldugu icin hata
--                    da vermez, sessizce kimse taninmaz. Etiket olmadan
--                    bu arizanin sebebi bulunamaz.
--
--  KVKK: yuz_vektor BIYOMETRIK VERIDIR, 6698 sayili kanunda "ozel
--  nitelikli kisisel veri". Ilgili kisinin ACIK RIZASI olmadan
--  islenemez. Vektorden geriye fotograf uretilemez ama bu onu anonim
--  yapmaz: ayni kisiyi baska kayitlarda eslestirmeye yarar. Yedekler
--  de bu kapsamdadir.
CREATE TABLE personel (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  ad_soyad      VARCHAR(100) NOT NULL,
  kart_uid      VARCHAR(32)  NULL UNIQUE,
  pin_ozeti     VARCHAR(255) NULL,
  yetkili_mi    BOOLEAN      NOT NULL DEFAULT TRUE,
  kayit_tarihi  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  foto_yolu     VARCHAR(255) NULL,
  yuz_kayitli   BOOLEAN      NOT NULL DEFAULT FALSE,
  guncelleme    DATETIME     NULL,
  yuz_vektor    TEXT         NULL
                COMMENT 'SFace 128-d kimlik vektoru, base64(float32[128])',
  yuz_model     VARCHAR(64)  NULL
                COMMENT 'Vektoru ureten model dosyasi',
  INDEX ix_personel_kart (kart_uid),
  -- Yoklama sorgusu (SELECT MAX(guncelleme) FROM personel) 30 saniyede
  -- bir calisiyor; indekssiz tam tablo taramasi olurdu.
  INDEX ix_personel_guncelleme (guncelleme)
) ENGINE=InnoDB;


-- ---------------------------------------------------------------------
--  4) gecis_kaydi
-- ---------------------------------------------------------------------
--  personel_id NULL olabilir: yetkisiz bir kart okutuldugunda karsilik
--  gelen personel yoktur, ama kayit yine de tutulmalidir.
--
--  yerel_log_id + UNIQUE(cihaz_id, yerel_log_id):
--  Cihaz kaydi once sunucuya yazar, sonra kendi SQLite'inda
--  "gonderildi" isaretler. Arada elektrik kesilirse ayni kayit bir
--  sonraki turda tekrar gonderilir. Cihaz INSERT IGNORE kullandigi
--  icin tekrar sessizce reddedilir, ayni gecis raporda iki kez
--  gorunmez. cihaz_id de anahtara giriyor: ikinci kapi cihazi kendi
--  1, 2, 3... numaralarini kullanabilsin.
CREATE TABLE gecis_kaydi (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  personel_id  INT      NULL,
  cihaz_id     INT      NOT NULL,
  yerel_log_id INT      NULL
               COMMENT 'Kaydin Pi uzerindeki SQLite id degeri',
  zaman        DATETIME NOT NULL,
  yontem       ENUM('kart','sifre','yuz') NOT NULL,
  sonuc        BOOLEAN  NOT NULL,
  detay        VARCHAR(255),
  senkronize   BOOLEAN  NOT NULL DEFAULT FALSE,
  UNIQUE KEY ux_gecis_cihaz_yerel (cihaz_id, yerel_log_id),
  -- Rapor sorgulari tarih araligina gore filtreliyor; indeks olmadan
  -- kayit sayisi buyudukce panel yavaslar.
  INDEX ix_gecis_zaman (zaman),
  FOREIGN KEY (personel_id) REFERENCES personel(id) ON DELETE SET NULL,
  FOREIGN KEY (cihaz_id)    REFERENCES cihaz(id)
) ENGINE=InnoDB;


-- ---------------------------------------------------------------------
--  4b) sunucu_oturum ve cihaz_kesinti  (bkz. 006-calisma-suresi.sql)
-- ---------------------------------------------------------------------
--  "Sunucu gece coktu mu, Pi ne kadar kopuk kaldi" sorusunun cevabi.
--  cihaz.son_iletisim yalnizca EN SON goruldugu ani tutar; arada
--  yasanan bir kesinti oradan asla okunamaz. Panel dakikada bir kendi
--  damgasini atar, damgalar arasindaki BOSLUK kesinti demektir.
--
--  sunucu_oturum.acilis, isletim sisteminin acilis saatidir. Iki ardisik
--  oturumda AYNI ise Windows hic yeniden baslamamis, sadece panel olup
--  dirilmistir; farkli ise makine gercekten kapanip acilmistir. Bu
--  ayrim olmadan "sunucu mu coktu program mi" bilinemez.
CREATE TABLE sunucu_oturum (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  baslangic  DATETIME NOT NULL,
  son_nabiz  DATETIME NOT NULL,
  acilis     DATETIME NULL COMMENT 'Isletim sisteminin acilis saati',
  INDEX ix_oturum_nabiz (son_nabiz)
) ENGINE=InnoDB;

--  bitis NULL = kesinti HALA SURUYOR.
CREATE TABLE cihaz_kesinti (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  cihaz_id   INT      NOT NULL,
  baslangic  DATETIME NOT NULL,
  bitis      DATETIME NULL COMMENT 'NULL ise kesinti hala suruyor',
  INDEX ix_kesinti_cihaz (cihaz_id, baslangic),
  FOREIGN KEY (cihaz_id) REFERENCES cihaz(id)
) ENGINE=InnoDB;


-- ---------------------------------------------------------------------
--  5) guncelleme damgasi tetikleyicileri
-- ---------------------------------------------------------------------
--  Tetikleyici kullaniliyor cunku personeli guncelleyen birden fazla
--  nokta var (web panelinin uc sayfasi + cihazin admin ekrani);
--  birini unutmak sessiz bir cakisma hatasi uretirdi. Veritabani
--  seviyesinde yapilinca unutulamaz.
DELIMITER $$

CREATE TRIGGER trg_personel_ekleme
BEFORE INSERT ON personel
FOR EACH ROW
BEGIN
    IF NEW.guncelleme IS NULL THEN
        SET NEW.guncelleme = NOW();
    END IF;
END$$

CREATE TRIGGER trg_personel_guncelleme
BEFORE UPDATE ON personel
FOR EACH ROW
BEGIN
    -- Cihaz kendi damgasini gonderiyorsa (NEW <> OLD) ona dokunma;
    -- aksi halde cakisma karsilastirmasi anlamsizlasir.
    IF NEW.guncelleme <=> OLD.guncelleme THEN
        SET NEW.guncelleme = NOW();
    END IF;
END$$

DELIMITER ;


-- ---------------------------------------------------------------------
--  6) Kullanicilar ve yetkiler
-- ---------------------------------------------------------------------
--  EN AZ YETKI ILKESI:
--    kapi_cihaz -> kapida, fiziksel olarak erisilebilir bir yerde.
--                  SELECT + INSERT var. DELETE / DROP / ALTER YOK.
--                  personel tablosunda UPDATE'i SUTUN BAZINDA.
--                  Sifresi ele gecse bile saldirgan kayit silemez,
--                  tablo dusuremez; yapabilecegi en kotu sey yetkileri
--                  kapatmaktir, o da kayitlarda gorulur ve web'den
--                  geri alinir.
--    kapi_web   -> sunucuda, kilitli odada. Yonetim islemleri tam
--                  yetki gerektirir.
--
--  SIFRELER GELISTIRME VARSAYILANLARIDIR. Gercek kurulumda degistir ve
--  cihaz tarafinda data/gizli.json'a yaz (koda YAZILMAZ).

DROP USER IF EXISTS 'kapi_cihaz'@'%';
CREATE USER 'kapi_cihaz'@'%' IDENTIFIED BY 'kapi1234';

GRANT SELECT, INSERT ON kapi_sistemi.* TO 'kapi_cihaz'@'%';
GRANT UPDATE ON kapi_sistemi.cihaz TO 'kapi_cihaz'@'%';
GRANT UPDATE (ad_soyad, kart_uid, pin_ozeti, yuz_kayitli, yetkili_mi,
              guncelleme, yuz_vektor, yuz_model)
    ON kapi_sistemi.personel TO 'kapi_cihaz'@'%';

--  Web paneli ayni makinede (127.0.0.1) calisiyor, disari acilmiyor.
--  Iki satir birden: MySQL, TCP ile gelen 127.0.0.1'i normalde
--  'localhost' olarak cozer, ama skip_name_resolve acikken cozmez ve
--  "Access denied" alinir. Ikisini de tanimlamak bu tuzagi kapatir.
DROP USER IF EXISTS 'kapi_web'@'localhost';
CREATE USER 'kapi_web'@'localhost' IDENTIFIED BY 'web1234';
GRANT SELECT, INSERT, UPDATE, DELETE ON kapi_sistemi.* TO 'kapi_web'@'localhost';

DROP USER IF EXISTS 'kapi_web'@'127.0.0.1';
CREATE USER 'kapi_web'@'127.0.0.1' IDENTIFIED BY 'web1234';
GRANT SELECT, INSERT, UPDATE, DELETE ON kapi_sistemi.* TO 'kapi_web'@'127.0.0.1';

FLUSH PRIVILEGES;


-- ---------------------------------------------------------------------
--  7) Baslangic verisi
-- ---------------------------------------------------------------------
--  Cihaz kaydi SART: gecis_kaydi.cihaz_id buna yabanci anahtarla bagli
--  ve config.DEVICE_ID = 1. Bu satir olmadan cihaz hicbir kayit
--  gonderemez.
INSERT INTO cihaz (id, cihaz_adi, konum)
VALUES (1, 'kapi-01', 'Bilgi Islem Giris');


-- ---------------------------------------------------------------------
--  8) Dogrulama
-- ---------------------------------------------------------------------
SELECT 'tablolar' AS kontrol;
SHOW TABLES;

SELECT 'personel sutunlari' AS kontrol;
SHOW COLUMNS FROM personel;

SELECT 'tetikleyiciler' AS kontrol;
SELECT TRIGGER_NAME, EVENT_MANIPULATION FROM information_schema.TRIGGERS
 WHERE TRIGGER_SCHEMA = 'kapi_sistemi';

SELECT 'cihaz kaydi' AS kontrol;
SELECT * FROM cihaz;
