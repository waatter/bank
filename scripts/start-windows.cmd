@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_tropos.ps1"
if errorlevel 1 (
  echo.
  echo TROPOS 启动失败。请查看上面的原因，或打开 .local\server.err.log。
  pause
  exit /b 1
)
endlocal
