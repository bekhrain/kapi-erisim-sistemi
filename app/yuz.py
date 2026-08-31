"""
Yuz TANIMA: YuNet (bulma) + SFace (kimlik vektoru).

NEDEN BU IKILI, NEDEN LBPH DEGIL
--------------------------------
OpenCV'de yuz tanima icin pratikte iki yol var:

  1) LBPH  (cv2.face.LBPHFaceRecognizer)
     - opencv-CONTRIB paketi gerektirir. Bizim ortamda kurulu degil ve
       Pi'de derlenmesi agir.
     - Piksel histogrami karsilastirir. Isik, aci ve kamera degisimine
       cok duyarlidir.
     - En onemli kusuru: HER ZAMAN bir kisi adi doner, yaninda bir
       "uzaklik" verir. "Bu kimse degil" cevabini uretmek icin esigi
       elle tutturmak gerekir ve o esik isik degisince kayar. Bir kapi
       icin yanlis kabul (baskasini iceri almak) en pahali hatadir;
       LBPH tam da bu hatayi kolay yapar.

  2) YuNet + SFace  (cv2.FaceDetectorYN / cv2.FaceRecognizerSF)
     - DUZ opencv-python icinde gelir (4.5.4+). Ek paket yok.
       Ortamda dogrulandi: opencv 4.13, ikisi de mevcut.
     - SFace her yuzu 128 boyutlu bir vektore cevirir. Iki vektorun
       kosinus benzerligi kimlik olcusudur; sabit ve isiktan buyuk
       olcude bagimsiz bir esik kullanilabilir.
     - "Hicbiri" cevabi dogaldir: en yakin kayit bile esigin altinda
       kaliyorsa kisi taninmamistir.

HIZ (olculdu, gelistirme PC'si)
-------------------------------
    YuNet, 640x480 tam kare  : 34 ms   <- her karede bu cok
    YuNet, 320 genislige kucultulmus:  ~9 ms
    SFace vektor cikarma     : 17 ms   <- karar aninda BIR KEZ

Bu yuzden arama kucultulmus karede yapilir (YUZ_BULMA_GENISLIK),
vektor ise tam cozunurluklu kareden cikarilir. Pi 5'te sureler
kabaca iki katina cikar; kucultme olmadan onizleme saniyede 12-15
kareye duser ve kullanici "kamera takiliyor" der.

Ayni model dosyalari her makinede ayni vektoru urettigi icin kayit
hangi makinede yapilirsa yapilsin kapida eslesir.

KARAR NEREDE VERILIYOR
----------------------
Kart ve PIN'de oldugu gibi: YETKILI KISI LISTESI SUNUCUDAN gelir,
karsilastirma cihazda yapilir (bkz. app/dogrula.py). Boylece web'den
yetkisi kaldirilan birinin vektoru listeye hic girmez.

Kameradan gelen yuz (probe) CIHAZDAN CIKMAZ; disari cikan tek sey
karsilastirmanin sonucudur. Kayitli vektorler ise sunucuda durur -
baska bir kapi da ayni kisiyi tanisin diye. Vektor geri cevrilip
fotograf uretilemez ama yine de biyometrik veridir: KVKK'da "ozel
nitelikli kisisel veri" sayilir, acik riza ister.

BILINEN SINIR: CANLILIK YOK
---------------------------
SFace bir fotografla gercek yuzu AYIRT ETMEZ. Telefonda acilan bir
resim kapiyi acabilir. Gercek canlilik tespiti (goz kirpma, kizilotesi
veya derinlik kamerasi) ayri bir istir; bu haliyle yuz, kart ve PIN'in
yerine degil YANINDA bir kolaylik olarak dusunulmelidir.
"""
import base64
import os
import threading

import numpy as np

from app.config import (YUZ_BULMA_GENISLIK, YUZ_ESIK, YUZ_FARK, YUZ_MODEL_DIR,
                        YUZ_SFACE_DOSYA, YUZ_YUNET_DOSYA)

try:
    import cv2
except ImportError:  # kamera olmayan ortamda modul yine de import edilebilsin
    cv2 = None


YUNET_YOLU = os.path.join(YUZ_MODEL_DIR, YUZ_YUNET_DOSYA)
SFACE_YOLU = os.path.join(YUZ_MODEL_DIR, YUZ_SFACE_DOSYA)

# Model nesneleri pahali kurulur (ONNX ayristirma), bir kez yuklenip
# saklanir. Bulucu arayuz is parcaciginda, tanici dogrulama is
# parcaciginda kullanilir; ikisi ayri nesne oldugu icin cakismazlar,
# yine de tanici bir kilitle korunur (admin ekrani da kullaniyor).
_bulucu = None
_tanici = None
_tanici_kilit = threading.Lock()
_yukleme_hatasi = ""


def _model_var_mi() -> bool:
    return os.path.exists(YUNET_YOLU) and os.path.exists(SFACE_YOLU)


def hazir_mi() -> tuple:
    """
    (hazir_mi, aciklama) doner. Ekranlar buna gore ya tanima yapar ya da
    kullaniciya neyin eksik oldugunu SOYLER - sessizce "algilama" moduna
    dusmek en tehlikelisi olurdu (herkesi iceri alan bir kapi).
    """
    if cv2 is None:
        return False, "OpenCV kurulu degil"
    if not hasattr(cv2, "FaceRecognizerSF"):
        return False, f"OpenCV {cv2.__version__} cok eski (4.5.4+ gerekir)"
    if not _model_var_mi():
        return False, f"Model dosyalari eksik: {YUZ_MODEL_DIR}"
    if _yukleme_hatasi:
        return False, _yukleme_hatasi
    return True, "hazir"


def _bulucu_al(genislik: int, yukseklik: int):
    """
    YuNet bulucuyu doner; giris boyutu her cagride guncellenir.

    setInputSize cagrilmazsa model kurulusundaki boyutu varsayar ve
    farkli cozunurlukte gelen karede yuzu ya bulamaz ya da kutuyu
    yanlis yere koyar - o kutuyla yapilan hizalama da bozuk cikar.
    """
    global _bulucu, _yukleme_hatasi
    if _bulucu is None:
        try:
            _bulucu = cv2.FaceDetectorYN.create(
                YUNET_YOLU, "", (genislik, yukseklik),
                score_threshold=0.75,   # dusuk skorlu "yuz benzeri" leke gelmesin
                nms_threshold=0.3,
                top_k=50,
            )
        except Exception as e:  # noqa: BLE001
            _yukleme_hatasi = f"YuNet yuklenemedi: {e}"
            return None
    _bulucu.setInputSize((genislik, yukseklik))
    return _bulucu


def _tanici_al():
    global _tanici, _yukleme_hatasi
    if _tanici is None:
        try:
            _tanici = cv2.FaceRecognizerSF.create(SFACE_YOLU, "")
        except Exception as e:  # noqa: BLE001
            _yukleme_hatasi = f"SFace yuklenemedi: {e}"
            return None
    return _tanici


# ---------------- Yuz bulma ----------------

def yuz_bul(kare):
    """
    Karedeki EN BUYUK yuzu doner (YuNet satiri, 15 eleman) veya None.

    Neden en buyuk: kapiya gelen kisi kameraya en yakin olandir.
    Arkadan gecen birinin yuzu de kadraja girebilir; "ilk bulunani al"
    denseydi kapiyi kimin actigi kameranin siralamasina kalirdi.

    Satirin ilk 4 degeri kutu (x, y, w, h), sonraki 10'u bes kilit
    nokta (iki goz, burun, iki agiz kosesi), sonuncusu skor. Hizalama
    icin kilit noktalar sart; kutu tek basina yetmez.
    """
    if cv2 is None or not _model_var_mi():
        return None
    y, g = kare.shape[:2]

    # Arama KUCULTULMUS karede yapilir (bkz. YUZ_BULMA_GENISLIK).
    # Kutu ve kilit noktalar sonra tam cozunurluge geri olceklenir;
    # olceklenmezse hizalama yanlis yerden kirpar ve vektor bozulur.
    olcek = 1.0
    arama = kare
    if g > YUZ_BULMA_GENISLIK:
        olcek = YUZ_BULMA_GENISLIK / float(g)
        arama = cv2.resize(kare, (YUZ_BULMA_GENISLIK,
                                  max(1, int(round(y * olcek)))))

    ay, ag = arama.shape[:2]
    bulucu = _bulucu_al(ag, ay)
    if bulucu is None:
        return None
    try:
        _, yuzler = bulucu.detect(arama)
    except Exception:  # noqa: BLE001 - bozuk kare tanimayi cokertmesin
        return None
    if yuzler is None or len(yuzler) == 0:
        return None

    # en genis kutu = en yakin kisi
    en_buyuk = max(yuzler, key=lambda s: float(s[2]) * float(s[3]))
    if olcek == 1.0:
        return en_buyuk

    geri = en_buyuk.copy()
    # Ilk 14 deger koordinat (kutu + 5 kilit nokta), 15.'si skor.
    geri[:14] = geri[:14] / olcek
    return geri


# ---------------- Vektor ----------------

def vektor_cikar(kare, yuz) -> str:
    """
    Yuzu hizalayip 128 boyutlu kimlik vektorunu base64 metin olarak doner.

    alignCrop adimi atlanamaz: SFace, gozleri sabit noktalara oturtulmus
    112x112 bir goruntu bekler. Ham kutu kirpilip verilseydi kafasini
    hafif yana egen kisi her seferinde baska bir vektor uretirdi.

    ~40-60 ms surer (Pi 5). ARAYUZ IS PARCACIGINDA CAGRILMAMALI.
    """
    if cv2 is None or yuz is None:
        return ""
    tanici = _tanici_al()
    if tanici is None:
        return ""
    try:
        with _tanici_kilit:
            hizali = tanici.alignCrop(kare, yuz)
            ozellik = tanici.feature(hizali)
    except Exception:  # noqa: BLE001
        return ""
    return kodla(ozellik)


def kodla(vektor) -> str:
    """float32 diziyi base64 metne cevirir (veritabaninda TEXT olarak durur)."""
    dizi = np.asarray(vektor, dtype=np.float32).reshape(-1)
    if dizi.size == 0:
        return ""
    return base64.b64encode(dizi.tobytes()).decode("ascii")


def coz(metin: str):
    """base64 metni float32 diziye cevirir; bozuksa None."""
    if not metin:
        return None
    try:
        ham = base64.b64decode(metin, validate=True)
    except Exception:  # noqa: BLE001
        return None
    if len(ham) % 4 != 0:
        return None
    dizi = np.frombuffer(ham, dtype=np.float32)
    return dizi if dizi.size else None


def benzerlik(a, b) -> float:
    """
    Kosinus benzerligi (-1..1). SFace'in kendi match() cagrisiyla ayni
    olcu; numpy ile hesaplamak modeli tekrar kilitlemekten hizli ve
    karsilastirmanin sunucudan gelen listeyle donmesini kolaylastirir.
    """
    if a is None or b is None or a.size != b.size:
        return -1.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return -1.0
    return float(np.dot(a, b) / (na * nb))


def en_yakin(vektor_b64: str, kayitlar) -> tuple:
    """
    Kayitli vektorler icinde eslesen kisiyi bulur.

    kayitlar: (kimlik, ad, vektor_b64) uclulerinden olusan liste
    Doner:    (eslesen_veya_None, en_iyi_skor, sebep)

    IKI KOSUL birden aranir:
      1) En iyi skor YUZ_ESIK'i asmali. Asmiyorsa kisi kayitli degildir.
      2) En iyi skor, ikinci en iyiden en az YUZ_FARK kadar buyuk
         olmali. Iki kisi birbirine cok benziyorsa (kardesler, ikizler)
         skorlar yan yana gelir; boyle bir durumda "en yuksegi al"
         demek, kapiyi yazi-tura ile acmak olur. Kararsiz kalindiginda
         ACMAMAK dogru davranistir.
    """
    en_iyi, en_iyi_skor, ikinci_skor = None, -1.0, -1.0
    probe = coz(vektor_b64)
    if probe is None:
        return None, -1.0, "vektor okunamadi"

    for kayit in kayitlar:
        aday = coz(kayit[2] or "")
        if aday is None:
            continue
        skor = benzerlik(probe, aday)
        if skor > en_iyi_skor:
            en_iyi, ikinci_skor, en_iyi_skor = kayit, en_iyi_skor, skor
        elif skor > ikinci_skor:
            ikinci_skor = skor

    if en_iyi is None:
        return None, -1.0, "kayitli yuz yok"
    if en_iyi_skor < YUZ_ESIK:
        return None, en_iyi_skor, "esik altinda"
    if ikinci_skor > -1.0 and (en_iyi_skor - ikinci_skor) < YUZ_FARK:
        return None, en_iyi_skor, "iki kayit birbirine cok yakin"
    return en_iyi, en_iyi_skor, "eslesti"
