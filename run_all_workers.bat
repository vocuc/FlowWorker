@echo off
REM Chay 2 worker cung luc, moi worker 1 cua so rieng (dong cua so hoac Ctrl+C de dung tung worker):
REM   - ImageGenerate.py : worker tao anh (Chrome profile C:\selenium-profile, cong 9222)
REM   - tiktok_worker.py : worker dang TikTok (Chrome profile C:\selenium-tiktok-profile, cong 9223)
REM Hai worker dung profile Chrome va cong khac nhau nen chay song song duoc.

chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set AI90_USE_MOCK=0
set AI90_API_BASE_URL=https://admin.a51.vn/api/v1

start "WORKER TAO ANH" cmd /k python ImageGenerate.py
start "WORKER DANG TIKTOK" cmd /k python tiktok_worker.py

echo Da mo 2 cua so: WORKER TAO ANH va WORKER DANG TIKTOK.
timeout /t 5 >nul
