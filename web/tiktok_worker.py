"""
Lối tắt cũ của worker đăng TikTok, giữ để lệnh / file .bat cũ vẫn chạy được: tương đương `python worker.py tiktok`.
Code đã chuyển sang flowkit/ (phần dùng chung) và flowkit/publishers/tiktok.py (phần riêng TikTok).
AI90_USE_MOCK=1 (cách cũ) = `python worker.py tiktok --mock`.
"""

from flowkit.config import env
from flowkit.publishers import TikTokPublisher
from flowkit.runner import main

if __name__ == "__main__":
    main(TikTokPublisher(), mock=env("AI90_USE_MOCK", "0") == "1")
