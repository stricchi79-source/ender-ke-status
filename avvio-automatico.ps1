# Fa partire lo schermino da solo all'accensione del PC (Windows), senza finestre aperte.
#
#   Attivare:  powershell -ExecutionPolicy Bypass -File avvio-automatico.ps1 -Stampante 192.168.1.12
#   Con notifiche ntfy:  ... -Stampante 192.168.1.12 -Ntfy ender-mario-7f3k
#   Togliere:  powershell -ExecutionPolicy Bypass -File avvio-automatico.ps1 -Rimuovi
param(
    [string]$Stampante = "",
    [string]$Ntfy = "",
    [int]$Porta = 80,
    [switch]$Rimuovi
)
$ErrorActionPreference = "Stop"
$link = Join-Path ([Environment]::GetFolderPath("Startup")) "Ender KE Status.lnk"

if ($Rimuovi) {
    if (Test-Path $link) { Remove-Item $link; Write-Host "Avvio automatico tolto." }
    else { Write-Host "L'avvio automatico non era attivo." }
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*$(Join-Path $PSScriptRoot 'server.py')*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Server fermato." }
    exit 0
}

if (-not $Stampante) { $Stampante = Read-Host "IP della stampante (Impostazioni -> Rete sul display)" }
if ($Stampante -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw "IP non valido: $Stampante" }

# pythonw = Python senza finestra nera
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($py) { $pyw = Join-Path (Split-Path $py) "pythonw.exe" }
}
if (-not $pyw -or -not (Test-Path $pyw)) { throw "Python non trovato. Installalo da python.org (spunta 'Add to PATH')." }

$cartella = $PSScriptRoot
& (Join-Path (Split-Path $pyw) "python.exe") -m pip install -q -r (Join-Path $cartella "requirements.txt")

$argomenti = "`"$(Join-Path $cartella 'server.py')`" $Stampante $Porta"
if ($Ntfy) { $argomenti += " --ntfy $Ntfy" }

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($link)
$s.TargetPath = $pyw
$s.Arguments = $argomenti
$s.WorkingDirectory = $cartella
$s.Description = "Schermino di stato Ender-3 V3 KE"
$s.Save()

# Lo avvio subito, senza aspettare il prossimo riavvio
Start-Process -FilePath $pyw -ArgumentList $argomenti -WorkingDirectory $cartella -WindowStyle Hidden

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.PrefixOrigin -in 'Dhcp','Manual' -and $_.IPAddress -notlike '169.*' } |
       Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Fatto. Parte da solo a ogni accensione del PC."
if ($ip) { Write-Host "Dal telefono apri: http://$ip$(if ($Porta -ne 80) { ":$Porta" })" }
Write-Host "Per toglierlo: powershell -ExecutionPolicy Bypass -File avvio-automatico.ps1 -Rimuovi"
