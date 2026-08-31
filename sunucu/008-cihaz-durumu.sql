-- =====================================================================
--  008 - CIHAZ DURUMU: iki degil UC hal
--  Calistirilacak yer: SUNUCU (WIN-UCHH878J9PS), MySQL Workbench
--  VERI SILMEZ. Tek sutun ekler, mevcut degerden doldurur.
-- =====================================================================
--
--  SORUN
--  -----
--  Bugune kadar cihazin tek bir 'aktif' bayragi vardi ve panelde
--  "Hizmet disi" dugmesi bunu kapatiyordu. Ama o dugme aslinda TEK bir
--  sey yapiyor: Pi'nin sunucuyla olan bagini kesiyor. Kapi calismaya
--  devam ediyor, kararini kendi yerel listesinden veriyor.
--
--  Bu cogu zaman istenen sey (bakim, ag calismasi, sunucu tasima).
--  Ama "bu kapiyi tamamen kapat, kimse girmesin" demenin bir yolu
--  YOKTU. Iki farkli ihtiyac tek bayraga sigmiyor.
--
--  COZUM
--  -----
--  Tek 'durum' sutunu, uc deger:
--
--    acik   -> normal calisma. Sunucuya danisir, yetkisi olan girer.
--    yerel  -> sunucu baglantisi KESIK. Kapi kendi yerel listesinden
--              karar verir; gecisler Pi'de birikir, hizmete alininca
--              sunucuya aktarilir. (Eskiden 'aktif = 0' buydu.)
--    kapali -> KIMSE GIREMEZ. Kart da sifre de yuz de reddedilir.
--              Sunucu baglantisi acik kalir; boylece panelden geri
--              acilabilir ve denemeler kayda gecer.
--
--  NEDEN 'kapali' HALDE BAGLANTI ACIK KALIYOR
--  ------------------------------------------
--  Kapatilan bir kapinin nasil geri acilacagi sorusu. Baglanti da
--  kesilseydi kapiyi ancak birinin fiziksel olarak yanina gidip
--  Pi'yi yeniden baslatmasiyla acabilirdik. Ayrica kapali kapida
--  kart okutan biri olursa bunun kayda gecmesi ISE YARAR: "kapi
--  kapaliyken 14 kisi girmeye calismis" bilgisi, kapinin yanlis
--  zamanda kapatildigini gosterir.
--
--  NEDEN 'aktif' SUTUNU SIMDILIK DURUYOR
--  -------------------------------------
--  Bu dosya calistirildiginda Pi'deki kod hala 'aktif' sutununu
--  okuyor olacak. Sutun ayni anda silinseydi Pi bir sonraki
--  damgasinda hata alir ve KAPI CALISMAYI BIRAKIRDI.
--
--  Sira: (1) bu dosya, (2) Pi kodu, (3) web kodu, (4) 010 ile
--  'aktif' sutunu dusurulur. Guncelleme suresince kapi calisir.
-- =====================================================================

USE kapi_sistemi;

ALTER TABLE cihaz
    ADD COLUMN durum ENUM('acik','yerel','kapali')
        NOT NULL DEFAULT 'acik'
        COMMENT 'acik=normal, yerel=sunucu bagi kesik, kapali=kimse giremez';

-- Mevcut bayragi yeni sutuna tasi. Eski 'aktif = 0' hicbir zaman
-- "kapali" anlamina gelmiyordu; kapi o haldeyken de calisiyordu.
-- Bu yuzden karsiligi 'kapali' degil 'yerel'.
UPDATE cihaz SET durum = IF(aktif, 'acik', 'yerel');


-- ---------------------------------------------------------------------
--  Dogrulama
-- ---------------------------------------------------------------------
SELECT id, cihaz_adi, konum, aktif, durum, son_iletisim
  FROM cihaz ORDER BY id;
