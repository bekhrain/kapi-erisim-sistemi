-- =====================================================================
--  010 - KAPI YETKILERI: hangi kapiya hangi rol girer
--  Calistirilacak yer: SUNUCU (WIN-UCHH878J9PS), MySQL Workbench
--  VERI SILMEZ. Tek sutun ekler, mevcut davranisi aynen korur.
-- =====================================================================
--
--  NE GELIYOR
--  ----------
--  Her kapi, hangi KISI TURLERINI kabul ettigini kendi satirinda
--  tutuyor. Ornek:
--
--    kapi-01 (Bilgi Islem)  -> personel
--    kapi-02 (Ana giris)    -> personel, ogrenci
--    kapi-03 (Toplanti)     -> personel, ogrenci, misafir
--
--  NEDEN CIHAZ TABLOSUNDA, KISI TABLOSUNDA DEGIL
--  ---------------------------------------------
--  Yetki kisiye tek tek verilseydi 200 ogrencinin her biri icin ayri
--  bir satir tutmak ve yeni bir kapi eklendiginde 200 satiri elle
--  guncellemek gerekirdi. Kural aslinda kapiya ait: "buraya sunlar
--  girer". Kisi tarafinda tutulan tek sey rolu.
--
--  NEDEN ARA TABLO (cihaz_rol) DEGIL
--  ---------------------------------
--  Ara tablo dogru cozum olurdu EGER roller veriden gelseydi. Ama rol
--  listesi ENUM ile sabitlenmis uc degerden ibaret (bkz. 009) ve
--  buyumesi kod degisikligi gerektiriyor. Ara tablo bu haliyle her
--  yetki kontroluna bir JOIN, her kapi duzenlemesine ayri INSERT/DELETE
--  eklerdi. SET sutunu ayni bilgiyi tek okumada veriyor.
--
--  VARSAYILAN: HERKES
--  ------------------
--  Mevcut kapilar bugune kadar rol ayrimi yapmiyordu. Varsayilan dar
--  olsaydi (ornegin yalnizca 'personel'), bu dosya calistirildigi anda
--  o ana kadar giren ogrenciler kapida kalirdi - kimse bir sey
--  degistirmemis olmasina ragmen. Gecis sessiz olmali; daraltma
--  panelden BILEREK yapilir.
-- =====================================================================

USE kapi_sistemi;

ALTER TABLE cihaz
    ADD COLUMN izinli_roller SET('personel','ogrenci','misafir')
        NOT NULL DEFAULT 'personel,ogrenci,misafir'
        COMMENT 'Bu kapidan gecebilen kisi turleri';

-- Mevcut satirlar da acik varsayilanla dolsun. ALTER'in DEFAULT'u yeni
-- satirlar icindir; mevcut satirlar bos SET ile kalabilir ve bos SET
-- "hic kimse giremez" demektir - kapilar sessizce kilitlenirdi.
UPDATE cihaz
   SET izinli_roller = 'personel,ogrenci,misafir'
 WHERE izinli_roller = '' OR izinli_roller IS NULL;


-- ---------------------------------------------------------------------
--  Dogrulama
-- ---------------------------------------------------------------------
SELECT id, cihaz_adi, durum, izinli_roller FROM cihaz ORDER BY id;
