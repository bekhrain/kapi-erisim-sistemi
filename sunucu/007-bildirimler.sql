-- =====================================================================
--  007 - BILDIRIMLER ICIN AYAR TABLOSU
--  Calistirilacak yer: SUNUCU (WIN-UCHH878J9PS), MySQL Workbench
--  VERI SILMEZ. Tek bir kucuk tablo ekler.
-- =====================================================================
--
--  NE ISE YARIYOR
--  --------------
--  Panelde bir zil isareti var: sunucu durdu mu, kapiyla baglanti
--  koptu mu, kopukken kac kisi gecti - bunlari gosteriyor.
--
--  Bildirimlerin KENDISI icin yeni tablo GEREKMEDI. Hepsi zaten var
--  olan veriden turetiliyor:
--
--    - kapi kopmalari      -> cihaz_kesinti
--    - sunucu duruslari    -> sunucu_oturum satirlari arasindaki bosluk
--    - cevrimdisi girisler -> gecis_kaydi.detay icindeki "[yerel]"
--                             etiketi (bkz. app/dogrula.py, detay())
--
--  Ayri bir "bildirim" tablosu tutulsaydi ayni olay iki yerde
--  saklanmis olurdu ve ikisi zamanla ayrisirdi: kesinti kaydi silinse
--  bildirim ortada kalirdi. Turetmek boyle bir tutarsizlik uretmez.
--
--  Saklanmasi gereken TEK sey, kullanicinin bildirimlere en son ne
--  zaman baktigi. Ondan sonraki olaylar "yeni" sayilir.
--
--  NEDEN OTURUM CEREZINDE DEGIL
--  ----------------------------
--  Cerezde tutulsaydi panel her yeniden baslatildiginda (ki bugun
--  birkac kez oldu) butun eski olaylar tekrar "yeni" gorunurdu. Zil
--  boylece surekli dolu kalir ve hicbir sey ifade etmez hale gelirdi.
--
--  Tablo genel amacli anahtar-deger olarak tasarlandi; ileride baska
--  bir ayar gerekirse yeni tablo acmak yerine buraya satir eklenir.
-- =====================================================================

USE kapi_sistemi;

CREATE TABLE IF NOT EXISTS ayar (
  anahtar VARCHAR(64) PRIMARY KEY,
  deger   VARCHAR(255) NOT NULL,
  guncelleme TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
             ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Ilk deger: "su ana kadar olanlar okunmus sayilsin".
-- Bos birakilsaydi zil, sistemin kuruldugu gunden bu yana yasanmis
-- butun kesintilerle dolu acilirdi.
INSERT INTO ayar (anahtar, deger)
VALUES ('bildirim_okundu', DATE_FORMAT(NOW(), '%Y-%m-%d %H:%i:%s'))
ON DUPLICATE KEY UPDATE deger = deger;


-- ---------------------------------------------------------------------
--  Dogrulama
-- ---------------------------------------------------------------------
SELECT 'ayar tablosu' AS kontrol;
SELECT * FROM ayar;
