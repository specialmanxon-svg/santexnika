@echo off
chcp 65001 > nul
echo ===================================================
echo   Diyor Group Dashboard - FastAPI & MoySklad
echo ===================================================
echo.
cd /d "%~dp0\backend"
echo [1/2] Backend сервер ишга туширилмоқда (http://127.0.0.1:8000)...
start "" http://127.0.0.1:8000/dashboard
"..\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
pause
