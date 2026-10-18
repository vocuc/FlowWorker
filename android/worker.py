"""
Worker đăng bài A51 trên điện thoại Android thật (app đăng nhập sẵn, nối ADB qua WiFi). Mỗi máy một tiến trình,
nhận job mọi nền tảng có token của máy đó. Nghiệp vụ: note/dang-bai-da-nen-tang.txt ở repo a90-fe.
Bản đăng bằng Chrome (cách cũ) ở thư mục web/.

Chạy (trong thư mục android/):
  python worker.py PHONE1                              vòng lặp nhận job của PHONE1
  python worker.py PHONE1 --once                       làm 1 job (hoặc chưa có job) rồi dừng
  python worker.py PHONE1 --mock instagram             không gọi API: 1 job thử với ảnh trong job/output (VẪN đăng thật;
                                                       đặt PHONE1_INSTAGRAM_AUTO_POST=0 để điền xong bài mà không bấm Chia sẻ)
  python worker.py PHONE1 --dump                       lưu màn hình + cây giao diện hiện tại của máy (để sửa selector)

Cấu hình trong .env (xem .env.example).
"""

import argparse

from flowkit.phone_runner import main
from flowkit.publishers import PUBLISHERS


def parse_args():
    parser = argparse.ArgumentParser(description="Worker đăng bài A51 trên điện thoại Android")
    parser.add_argument("phone", help="tên máy trong .env (PHONE1, PHONE2...)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", choices=sorted(PUBLISHERS), metavar="NỀN_TẢNG",
                      help=f"không gọi API, chạy 1 job thử với ảnh trong job/output ({', '.join(sorted(PUBLISHERS))})")
    mode.add_argument("--once", action="store_true", help="làm 1 job rồi dừng")
    mode.add_argument("--dump", action="store_true", help="lưu màn hình + cây giao diện của máy rồi dừng")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args.phone, mock_platform=args.mock, once=args.once, dump=args.dump)
