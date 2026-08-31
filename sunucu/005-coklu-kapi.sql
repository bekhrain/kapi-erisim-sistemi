-- =====================================================================
--  005 - Coklu kapi hazirligi
--  Calistirilacak yer: SUNUCU (10.40.80.189), MySQL Workbench
-- =====================================================================
--
--  NE DEGISIYOR
--  ------------
--  Veritabani coklu kapiya zaten hazirdi: 'cihaz' tablosu birden fazla
--  satir alir, her gecis kaydi hangi cihazdan geldigini tutar ve
--  UNIQUE(cihaz_id, yerel_log_id) her cihazin kendi numara dizisini
--  kullanmasina izin verir.
--
--  Eksik olan tek sey, bir cihazi hizmetten CIKARABILMEKTI.
--
--  NEDEN SILME YOK
--  ---------------
--  gecis_kaydi.cihaz_id bu tabloya yabanci anahtarla bagli. Kayitlari
--  olan bir cihaz silinemez - silinebilseydi gecmis kayitlarin hangi
--  kapidan geldigi kaybolurdu ve denetim izi bozulurdu. Personelde de
--  ayni tercih yapilmisti: silme yerine yetkiyi kapatmak.
--
--  Bu yuzden 'aktif' bayragi ekleniyor. Pasif cihaz panelde ayri
--  gosterilir, kayitlari yerinde kalir.
-- =====================================================================

USE kapi_sistemi;

ALTER TABLE cihaz
    ADD COLUMN aktif BOOLEAN NOT NULL DEFAULT TRUE
    COMMENT 'Cihaz hizmette mi? Silme yerine bu bayrak kapatilir';

-- Cihaz adi benzersiz olsun: iki kapiya ayni adi vermek, raporlarda
-- hangi kapiya baktigini anlamayi imkansiz kilar.
ALTER TABLE cihaz
    ADD CONSTRAINT ux_cihaz_adi UNIQUE (cihaz_adi);

-- Kontrol
SELECT id, cihaz_adi, konum, aktif, son_iletisim FROM cihaz ORDER BY id;
