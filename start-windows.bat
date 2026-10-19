@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 hub.py menu
) else (
  python hub.py menu
)
pause
