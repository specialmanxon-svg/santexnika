@echo off
chcp 65001 > nul
title Diyor Group — Telegram GPS Bot
echo ===================================================
echo   Diyor Group — Telegram GPS Attendance Bot
echo   @Diyor_santexnika_2004_bot
echo ===================================================
echo.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" bot.py
) else (
    python bot.py
)
pause
