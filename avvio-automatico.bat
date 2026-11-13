@echo off
rem Doppio clic: chiede l'IP della stampante e attiva l'avvio automatico all'accensione del PC.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0avvio-automatico.ps1" %*
pause
