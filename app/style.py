"""
Uygulama temasi (renkler + QSS stil sayfasi).
Dokunmatik ekran icin buyuk, parmakla basilabilir hedefler hedeflenir.
"""

# Renk paleti
BG = "#1a1d24"          # arka plan
CARD = "#262b34"        # kart / panel
CARD_HOVER = "#2f3540"
ACCENT = "#3b82f6"      # mavi (birincil)
ACCENT_DARK = "#2563eb"
GREEN = "#22c55e"       # basari
RED = "#ef4444"         # hata
TEXT = "#e5e7eb"        # ana metin
TEXT_DIM = "#9ca3af"    # soluk metin

QSS = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "DejaVu Sans", Arial, sans-serif;
}}

/* Baslik */
QLabel#title {{
    font-size: 34px;
    font-weight: bold;
    color: {TEXT};
}}
QLabel#subtitle {{
    font-size: 16px;
    color: {TEXT_DIM};
}}
QLabel#screenTitle {{
    font-size: 26px;
    font-weight: bold;
    color: {TEXT};
}}
QLabel#hint {{
    font-size: 18px;
    color: {TEXT_DIM};
}}

/* Ana menu buyuk secim butonlari */
QPushButton#menuButton {{
    background-color: {CARD};
    border: 2px solid #333a45;
    border-radius: 18px;
    font-size: 22px;
    font-weight: bold;
    padding: 20px;
}}
QPushButton#menuButton:hover {{
    background-color: {CARD_HOVER};
    border: 2px solid {ACCENT};
}}
QPushButton#menuButton:pressed {{
    background-color: {ACCENT_DARK};
}}

/* Numpad tuslari */
QPushButton#keypad {{
    background-color: {CARD};
    border: 1px solid #333a45;
    border-radius: 12px;
    font-size: 28px;
    font-weight: bold;
    min-width: 90px;
    min-height: 70px;
}}
QPushButton#keypad:hover {{ background-color: {CARD_HOVER}; }}
QPushButton#keypad:pressed {{ background-color: {ACCENT_DARK}; }}

QPushButton#keyOk {{
    background-color: {GREEN};
    border: none;
    border-radius: 12px;
    font-size: 22px;
    font-weight: bold;
    color: #08130a;
    min-height: 70px;
}}
QPushButton#keyOk:pressed {{ background-color: #16a34a; }}

QPushButton#keyDel {{
    background-color: {RED};
    border: none;
    border-radius: 12px;
    font-size: 22px;
    font-weight: bold;
    color: #180707;
    min-height: 70px;
}}
QPushButton#keyDel:pressed {{ background-color: #b91c1c; }}

/* Geri butonu */
QPushButton#backButton {{
    background-color: transparent;
    border: 1px solid #333a45;
    border-radius: 10px;
    font-size: 16px;
    padding: 8px 18px;
}}
QPushButton#backButton:hover {{ background-color: {CARD}; }}

/* Yardimci (simulasyon) butonu */
QPushButton#simButton {{
    background-color: {ACCENT};
    border: none;
    border-radius: 10px;
    font-size: 16px;
    font-weight: bold;
    padding: 12px 22px;
}}
QPushButton#simButton:pressed {{ background-color: {ACCENT_DARK}; }}

/* Kisi turu secim dugmeleri (admin - yeni kisi).
   Secili olan DOLGU rengiyle isaretleniyor, sadece kenarlikla degil:
   7 inclik parlak bir ekranda ince kenarlik farki, ayakta duran
   birinin bakisiyla secilemiyordu. */
QPushButton#rolButton {{
    background-color: {CARD};
    border: 2px solid #333a45;
    border-radius: 10px;
    font-size: 16px;
    padding: 10px 8px;
}}
QPushButton#rolButton:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    font-weight: bold;
}}
QPushButton#rolButton:pressed {{ background-color: {ACCENT_DARK}; }}

/* Sifre gosterge alani */
QLabel#pinDisplay {{
    background-color: {CARD};
    border: 2px solid #333a45;
    border-radius: 12px;
    font-size: 40px;
    font-weight: bold;
    letter-spacing: 14px;
    padding: 10px;
}}

/* Kamera onizleme cercevesi */
QLabel#cameraView {{
    background-color: #000000;
    border: 2px solid #333a45;
    border-radius: 12px;
}}

/* Ana menu sag alt: Admin butonu */
QPushButton#adminButton {{
    background-color: transparent;
    border: 1px solid #333a45;
    border-radius: 10px;
    color: {TEXT_DIM};
    font-size: 15px;
    padding: 8px 18px;
}}
QPushButton#adminButton:hover {{
    background-color: {CARD};
    border: 1px solid {ACCENT};
    color: {TEXT};
}}

/* Admin: kisi listesi kaydirma alani */
QScrollArea#adminScroll {{ border: none; }}

/* Admin: tek kisi karti */
QWidget#userCard {{
    background-color: {CARD};
    border: 1px solid #333a45;
    border-radius: 12px;
}}
QLabel#userInfo {{
    font-size: 18px;
    font-weight: bold;
    color: {TEXT};
}}

/* Admin: form alan basligi */
QLabel#fieldLabel {{
    font-size: 15px;
    color: {TEXT_DIM};
}}

/* Ana menu: canli saat */
QLabel#clock {{
    font-size: 17px;
    font-weight: bold;
    color: {TEXT_DIM};
    letter-spacing: 1px;
}}

/* Ana menu: kurum adi alt basligi */
QLabel#orgSubtitle {{
    font-size: 21px;
    font-weight: bold;
    color: {ACCENT};
}}

/* Admin: log satiri */
QLabel#logInfo {{
    font-size: 15px;
    color: {TEXT};
}}
QLabel#logOk {{
    font-size: 24px;
    font-weight: bold;
    color: {GREEN};
}}
QLabel#logFail {{
    font-size: 24px;
    font-weight: bold;
    color: {RED};
}}

/* Admin: metin girisleri */
QLineEdit#adminInput {{
    background-color: {CARD};
    border: 1px solid #333a45;
    border-radius: 10px;
    font-size: 20px;
    padding: 12px 14px;
    color: {TEXT};
}}
QLineEdit#adminInput:focus {{ border: 1px solid {ACCENT}; }}
"""
