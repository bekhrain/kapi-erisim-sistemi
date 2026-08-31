-- =====================================================================
--  009 - KISI ROLU: personel / ogrenci / misafir
--  Calistirilacak yer: SUNUCU (WIN-UCHH878J9PS), MySQL Workbench
--  VERI SILMEZ. Tek sutun ekler, mevcut herkesi 'personel' sayar.
-- =====================================================================
--
--  NEDEN GEREKLI
--  -------------
--  Bugune kadar sistemde tek tip kisi vardi ve tablonun adi da bunu
--  soyluyor: 'personel'. Ama kapidan gecen herkes personel degil -
--  ogrenciler ve misafirler de var ve HEPSININ AYNI YETKIYE SAHIP
--  OLMASI dogru degil: bir kapiya yalnizca personel girebilirken
--  digerine ogrenciler de girebilmeli (bkz. 010 - kapi yetkileri).
--
--  Ayrica misafir kartlari sureli olacak (bkz. 011). Sureyi kimin
--  kartina uygulayacagimizi bilmek icin once "bu kisi kim" sorusunun
--  cevabi lazim.
--
--  NEDEN AYRI TABLO DEGIL
--  ----------------------
--  Rol basina ayri tablo (personel / ogrenci / misafir) acmak ilk
--  bakista duzenli gorunur ama her sorguyu uce katlardi: gecis_kaydi
--  hangi tabloya baglanacakti? Kart numarasi uc tabloda birden
--  aranacak ve benzersizlik veritabani seviyesinde korunamayacakti -
--  ayni kart hem bir ogrenciye hem bir misafire tanimlanabilirdi.
--
--  Tek tablo + rol sutunu, mevcut yabanci anahtarlari ve UNIQUE
--  kisitini oldugu gibi korur.
--
--  NEDEN ENUM
--  ----------
--  Uc deger sabit ve kisa. Ayri bir 'rol' tablosu + yabanci anahtar,
--  uc satir icin bir JOIN daha demekti. Dorduncu bir rol gerekirse
--  ALTER TABLE ... MODIFY ile eklenir; panelde de kod degisecegi
--  icin bu zaten tek basina yeterli bir islem degil.
--
--  TABLO ADI DEGISMIYOR
--  --------------------
--  'personel' tablosu artik ogrenci ve misafir de tutuyor; adi
--  yaniltici. Ama tabloyu yeniden adlandirmak butun sorgulari,
--  yabanci anahtarlari ve sutun bazindaki GRANT'leri etkiler.
--  Staj suresi icinde bunun karsiligi yok; ad birakildi, anlami
--  bu yorumda kayitli.
-- =====================================================================

USE kapi_sistemi;

ALTER TABLE personel
    ADD COLUMN rol ENUM('personel','ogrenci','misafir')
        NOT NULL DEFAULT 'personel'
        COMMENT 'Kisi turu; kapi yetkisi ve kart suresi buna gore'
        AFTER ad_soyad;

-- Rol'e gore listeleme panelde en sik yapilacak filtre olacak.
CREATE INDEX ix_personel_rol ON personel (rol);

-- Cihaz, kendi yerel listesini kurarken rolu de okumali. Sutun
-- bazindaki UPDATE yetkisine rol EKLENMIYOR: kim personel kim
-- misafir karari sunucuda verilir. Kapidaki cihaz bunu degistirebilseydi,
-- fiziksel olarak erisilebilir bir makine kendi yetkisini yukseltebilirdi.
-- (SELECT yetkisi zaten tablo genelinde verilmis durumda.)


-- ---------------------------------------------------------------------
--  Dogrulama
-- ---------------------------------------------------------------------
SELECT rol, COUNT(*) AS kisi FROM personel GROUP BY rol;
SELECT id, ad_soyad, rol, yetkili_mi FROM personel ORDER BY id;
