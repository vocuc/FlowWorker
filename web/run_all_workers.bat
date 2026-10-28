@echo off
REM Chay cac worker cung luc, moi worker 1 cua so rieng (dong cua so hoac Ctrl+C de dung tung worker):
REM   - ImageGenerate.py         : worker tao anh   (Chrome profile C:\selenium-profile, cong 9222)
REM   - worker.py tiktok         : worker dang TikTok    (C:\selenium-tiktok-profile, cong 9223)
REM   - worker.py facebook       : worker dang Facebook  (C:\selenium-facebook-profile, cong 9224)
REM   - worker.py instagram      : worker dang Instagram (C:\selenium-instagram-profile, cong 9225)
REM Moi worker dung profile Chrome va cong rieng nen chay song song duoc. Token / profile dat trong .env.
REM Worker chua co token (vd FACEBOOK_WORKER_TOKEN) se bao loi trong cua so cua no va dung.

chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set AI90_USE_MOCK=0
set AI90_API_BASE_URL=https://admin.a51.vn/api/v1

start "WORKER TAO ANH" cmd /k python ImageGenerate.py
start "WORKER DANG TIKTOK" cmd /k python worker.py tiktok
REM start "WORKER DANG FACEBOOK" cmd /k python worker.py facebook
REM start "WORKER DANG INSTAGRAM" cmd /k python worker.py instagram

echo Da mo 4 cua so: TAO ANH, DANG TIKTOK, DANG FACEBOOK, DANG INSTAGRAM.
timeout /t 5 >nul
