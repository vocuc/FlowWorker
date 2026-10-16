"""
Worker đăng bài A51: mỗi nền tảng một worker (một cửa sổ, một profile Chrome, một token).
Nghiệp vụ: note/dang-bai-da-nen-tang.txt ở repo a90-fe.

Chạy:
  python worker.py tiktok             vòng lặp nhận job TikTok (lưu nháp TikTok Studio)
  python worker.py facebook           vòng lặp nhận job Facebook
  python worker.py instagram          vòng lặp nhận job Instagram
  python worker.py facebook --once    làm 1 job (hoặc chưa có job) rồi dừng
  python worker.py facebook --mock    không gọi API: chạy 1 job thử với ảnh trong job/output (VẪN thao tác thật;
                                      đặt FACEBOOK_AUTO_POST=0 để điền xong bài mà không bấm Đăng)

Cấu hình trong .env (xem .env.example).
"""

import argparse

from flowkit.publishers import PUBLISHERS
from flowkit.runner import main


def parse_args():
    parser = argparse.ArgumentParser(description="Worker đăng bài A51 (TikTok / Facebook / Instagram)")
    parser.add_argument("platform", choices=sorted(PUBLISHERS), help="nền tảng worker đăng bài")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="không gọi API, chạy 1 job thử với ảnh trong job/output")
    mode.add_argument("--once", action="store_true", help="làm 1 job rồi dừng")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(PUBLISHERS[args.platform](), mock=args.mock, once=args.once)
