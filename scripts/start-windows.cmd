@echo off
cd /d "%~dp0.."
curl.exe --noproxy "*" -sSf "http://127.0.0.1:8765/api/health" >nul 2>&1
if errorlevel 1 (
  start "" /b pythonw "%~dp0run_windows.py"
  timeout /t 2 /nobreak >nul
)
set "TROPOS_ACCESS="
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "$p='.local\lan-access.json'; if(Test-Path -LiteralPath $p){(Get-Content -Raw -LiteralPath $p|ConvertFrom-Json).accessToken}"`) do set "TROPOS_ACCESS=%%T"
if defined TROPOS_ACCESS (
  start "" "http://127.0.0.1:8765/demo.html?access=%TROPOS_ACCESS%"
) else (
  start "" "http://127.0.0.1:8765/demo.html"
)
