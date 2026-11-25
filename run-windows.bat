@echo off
cd /d "%~dp0"
title Chipper Studio
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python app.py
pause
