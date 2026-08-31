# =====================================================================
#  Web panelini Windows acilisinda otomatik baslatan Zamanlanmis Gorev.
#  Calistirilacak yer: SUNUCU, YONETICI olarak acilmis PowerShell
#
#      powershell -ExecutionPolicy Bypass -File panel-gorev-kur.ps1
#
#  NEDEN ZAMANLANMIS GOREV, "Baslangic" KLASORU DEGIL
#  --------------------------------------------------
#  Baslangic klasorundeki kisayol ancak biri OTURUM ACINCA calisir.
#  Sunucu elektrik kesintisinden sonra kendi basina acildiginda kimse
#  oturum acmaz ve panel olu kalir - tam ihtiyac duyuldugu anda.
#  Zamanlanmis Gorev "sistem acilisinda" tetiklenir, oturumdan bagimsiz.
# =====================================================================

$GorevAdi = "KapiWebPaneli"

# --- Panelin yeri -----------------------------------------------------
# Betigin bulundugu klasorden turetiliyor.
$Kok = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bat = Join-Path $Kok "panel-baslat.bat"

if (-not (Test-Path $Bat)) {
    Write-Error "panel-baslat.bat bulunamadi: $Bat"
    exit 1
}

# --- Python'un TAM yolu ----------------------------------------------
# Gorev SYSTEM hesabiyla calisacak; o hesabin PATH'inde kullaniciya
# kurulmus Python bulunmayabilir. Sadece "python" yazmak, gorevin
# sessizce basarisiz olmasi demek olurdu: panel acilmaz, hata da
# gorunmez. Bu yuzden tam yol simdi bulunup goreve gomuluyor.
$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    Write-Error "python PATH'te bulunamadi. Once Python'u kurun."
    exit 1
}
Write-Host "Python  : $Python"
Write-Host "Panel   : $Bat"

# --- Gorev tanimi -----------------------------------------------------
$Eylem = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c set `"PYTHON_EXE=$Python`" && `"$Bat`"" `
    -WorkingDirectory $Kok

$Tetik = New-ScheduledTaskTrigger -AtStartup

# SYSTEM: oturum acilmasini beklemez, masaustu gerektirmez.
$Kimlik = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
    -LogonType ServiceAccount -RunLevel Highest

# Panel surekli calisan bir sunucu; sure siniri OLMAMALI.
# Varsayilan 3 gunluk sinir, panelin uc gunde bir sessizce olmesi
# demek olurdu.
$Ayar = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Unregister-ScheduledTask -TaskName $GorevAdi -Confirm:$false `
    -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $GorevAdi -Action $Eylem `
    -Trigger $Tetik -Principal $Kimlik -Settings $Ayar `
    -Description "Kapi erisim sistemi web yonetim paneli (port 5000)" | Out-Null

Write-Host ""
Write-Host "Gorev kuruldu: $GorevAdi"
Write-Host "Simdi baslatmak icin:  Start-ScheduledTask -TaskName $GorevAdi"
Write-Host "Durumu gormek icin  :  Get-ScheduledTask -TaskName $GorevAdi"
