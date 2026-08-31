@echo off
REM ====================================================================
REM  Web yonetim panelini baslatir.
REM  Calistirilacak yer: SUNUCU (WIN-UCHH878J9PS)
REM
REM  Elle calistirmak icin cift tiklamak yeterli. Acilista kendiliginden
REM  calismasi icin panel-gorev-kur.ps1 betigi bir Zamanlanmis Gorev
REM  olusturur ve bu dosyayi cagirir.
REM ====================================================================

REM --- Panelin bulundugu klasor -----------------------------------
REM  Bu .bat dosyasinin kendi konumundan turetiliyor; klasor tasinirsa
REM  yol elle duzeltilmek zorunda kalmasin.
set "KOK=%~dp0"
if not exist "%KOK%web\app.py" (
    REM  Depo icinden calistiriliyorsa bir ust klasore bak
    set "KOK=%~dp0..\"
)

REM --- Python yorumlayicisi ---------------------------------------
REM  Zamanlanmis Gorev SYSTEM olarak calisirsa kullanicinin PATH'i
REM  gecerli olmaz; bu yuzden tam yol PYTHON_EXE ile verilebilir.
REM  Verilmemisse PATH'ten aranir.
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=python"

REM --- Veritabani sifresi -----------------------------------------
REM  Kaynak koda YAZILMAZ. Gercek kurulumda asagidaki satiri acip
REM  gercek sifreyi yazin ya da sistem ortam degiskeni tanimlayin.
REM  Bu dosya surum kontrolune giriyor - buraya gercek sifre KOYMAYIN.
REM set "KAPI_WEB_PASSWORD=gercek-sifre"

cd /d "%KOK%web"
echo Panel baslatiliyor: %CD%
"%PYTHON_EXE%" app.py
