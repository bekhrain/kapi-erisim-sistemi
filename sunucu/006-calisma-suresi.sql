-- =====================================================================
--  006 - CALISMA SURESI VE KESINTI TAKIBI
--  Calistirilacak yer: SUNUCU (WIN-UCHH878J9PS), MySQL Workbench
--  VERI SILMEZ. Sadece iki yeni tablo ekler.
-- =====================================================================
--
--  NEDEN GEREKLI
--  -------------
--  Sunucu ve Pi gece boyunca acik birakiliyor. Sabah gelindiginde
--  "gece coktu mu, elektrik gitti mi, Pi ne kadar kopuk kaldi"
--  sorusunun cevabi HICBIR YERDE yoktu:
--
--    - cihaz.son_iletisim sadece EN SON goruldugu ani tutar. Pi gece
--      uc saat kopup sonra geri gelirse bu sutun bugunku saati gosterir
--      ve kesinti hic yasanmamis gibi olur.
--    - Windows'un kendi calisma suresi sadece "su an ne kadardir acik"
--      der; ne zaman kapandigini soylemez.
--
--  Bu iki tablo o gecmisi tutar. Web paneli ayakta oldugu surece
--  dakikada bir kendi damgasini atar; damgalar arasindaki BOSLUK
--  kesinti demektir.
--
--  OLCUM SINIRI (bilerek kabul edildi): kesintiyi kaydeden program
--  sunucunun kendisinde calisiyor. Sunucu kapaliyken kimse kayit
--  tutamaz. Bu yuzden sunucunun kapali kaldigi sure DOGRUDAN degil,
--  iki oturum arasindaki boslugundan HESAPLANIR - ve bu bosluk en
--  fazla bir nabiz araligi (60 saniye) kadar sisebilir. Saatlerce suren
--  bir kesintiyi tespit etmek icin bu hassasiyet fazlasiyla yeterli.
-- =====================================================================

USE kapi_sistemi;


-- ---------------------------------------------------------------------
--  sunucu_oturum : panelin her calisma dilimi
-- ---------------------------------------------------------------------
--  baslangic : panel surecinin ayaga kalktigi an
--  son_nabiz : panelin en son "ayaktayim" dedigi an. Panel duzgun
--              kapansa da cokse de elektrik kesilse de bu sutun oldugu
--              yerde kalir; dolayisiyla "en gec ne zaman hayattaydi"
--              sorusunun cevabidir.
--  acilis    : ISLETIM SISTEMININ acilis saati. Neden ayrica tutuluyor:
--              yeni bir oturumun acilis degeri bir oncekiyle AYNIYSA
--              Windows hic yeniden baslamamis, sadece panel olup
--              dirilmis demektir. Ikisi farkliysa makine gercekten
--              kapanip acilmistir (elektrik / reboot). Bu ayrim
--              olmadan "sunucu mu coktu, program mi coktu"
--              birbirinden ayirt edilemez.
CREATE TABLE IF NOT EXISTS sunucu_oturum (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  baslangic  DATETIME NOT NULL,
  son_nabiz  DATETIME NOT NULL,
  acilis     DATETIME NULL COMMENT 'Isletim sisteminin acilis saati',
  INDEX ix_oturum_nabiz (son_nabiz)
) ENGINE=InnoDB;


-- ---------------------------------------------------------------------
--  cihaz_kesinti : Pi ile baglantinin koptugu araliklar
-- ---------------------------------------------------------------------
--  baslangic : cihazdan gelen SON iletisim ani (kopusun basladigi an)
--  bitis     : baglanti geri geldiginde doldurulur.
--              NULL = kesinti HALA SURUYOR. Rapor "su an kopuk" ile
--              "kopmustu, duzeldi" arasindaki farki buradan okur.
--
--  ON DELETE CASCADE degil, cihaz zaten silinmiyor (bkz. 005). Yine de
--  yabanci anahtar konuyor: olmayan bir cihaza kesinti yazilamasin.
CREATE TABLE IF NOT EXISTS cihaz_kesinti (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  cihaz_id   INT      NOT NULL,
  baslangic  DATETIME NOT NULL,
  bitis      DATETIME NULL COMMENT 'NULL ise kesinti hala suruyor',
  INDEX ix_kesinti_cihaz (cihaz_id, baslangic),
  FOREIGN KEY (cihaz_id) REFERENCES cihaz(id)
) ENGINE=InnoDB;


-- ---------------------------------------------------------------------
--  Dogrulama
-- ---------------------------------------------------------------------
SELECT 'tablolar' AS kontrol;
SHOW TABLES;

SELECT 'sunucu_oturum sutunlari' AS kontrol;
SHOW COLUMNS FROM sunucu_oturum;

SELECT 'cihaz_kesinti sutunlari' AS kontrol;
SHOW COLUMNS FROM cihaz_kesinti;
