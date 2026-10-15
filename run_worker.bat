@echo off
REM Chay worker dang TikTok voi API THAT cua A51 (https://admin.a51.vn/api/v1).
REM Token worker lay tu AI90_TIKTOK_WORKER_TOKEN neu co, khong thi dung token mac dinh trong tiktok_worker.py.
REM Worker lay job, luu nhap len TikTok, xong dung 2 giay roi tat Chrome. Ctrl+C de dung.

chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set AI90_USE_MOCK=0
set AI90_API_BASE_URL=https://admin.a51.vn/api/v1
python tiktok_worker.py
pause
