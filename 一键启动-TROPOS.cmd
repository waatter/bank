@echo off
setlocal
chcp 65001 >nul
call "%~dp0scripts\start-windows.cmd"
exit /b %errorlevel%
