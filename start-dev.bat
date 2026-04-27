@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" %*
endlocal
