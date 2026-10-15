@echo off
REM Chay thu worker dang TikTok voi API gia lap (khong can server A51).
REM Mo API gia lap o cua so rieng, roi chay worker o cua so nay.
REM CHU Y: worker VAN luu nhap THAT len tai khoan TikTok dang dang nhap trong Chrome.
REM Doi so job / so anh / username: sua dong "start" ben duoi, vd: --jobs 2 --images 3 --username a51.vn

chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1

start "MOCK API A51" cmd /k python mock\mock_publish_server.py --jobs 1 --images 2

REM Cho API gia lap khoi dong
timeout /t 2 /nobreak >nul

set AI90_API_BASE_URL=http://127.0.0.1:8765/api/v1
set AI90_TIKTOK_WORKER_TOKEN=mock-token
python tiktok_worker.py
pause
