@echo off
rem Chipper Studio installer for Windows 10/11
cd /d "%~dp0"
title Installing Chipper Studio

where python >nul 2>nul || (
  echo Installing Python...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Python is installed. Close this window and run install-windows.bat again.
  pause & exit /b
)
where ffmpeg >nul 2>nul || winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
where cloudflared >nul 2>nul || winget install -e --id Cloudflare.cloudflared --accept-source-agreements --accept-package-agreements

if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium

echo.
set /p WH="Make captions on this computer with Whisper (about 2 GB download)? [y/N] "
if /i "%WH%"=="y" python -m pip install -r requirements-captions.txt

echo.
echo Done. Start Chipper Studio with run-windows.bat
pause
