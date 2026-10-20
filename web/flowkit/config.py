"""
Cấu hình worker: biến môi trường, đọc thêm từ file .env cạnh worker.py (không ghi đè biến đã đặt sẵn).
Token / mật khẩu đặt trong .env (đã có trong .gitignore), KHÔNG ghi thẳng vào code.
"""

import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Console Windows (cp1252) không in được tiếng Việt -> ép stdout/stderr sang UTF-8
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def load_env_file(path=os.path.join(BASE_DIR, ".env")):
    """Đọc KEY=VALUE từ file .env (bỏ dòng trống / dòng #). Biến đã có trong môi trường được giữ nguyên."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            os.environ.setdefault(key, value)


load_env_file()


def log(message=""):
    """In ra console kèm giờ, flush ngay để thấy log theo thời gian thực."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def env(name, default=None):
    value = os.environ.get(name)
    return default if value is None else value


def env_first(names, default=None):
    """Giá trị của biến đầu tiên có đặt trong names (tên mới trước, tên cũ sau)."""
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def env_float(name, default):
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return float(default)
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise SystemExit(f"Cấu hình {name}={raw!r} không phải số.")


API_BASE_URL = env("AI90_API_BASE_URL", "https://admin.a51.vn/api/v1").rstrip("/")
HTTP_TIMEOUT = 120           # Timeout mỗi request tới API / tải ảnh

POLL_INTERVAL = 5            # Giây nghỉ khi chưa có job
IDLE_LOG_INTERVAL = 60       # Khi chưa có job: cứ mỗi bấy nhiêu giây in 1 dòng "vẫn đang chờ"
NOT_ASSIGNED_DELAY = 60      # Worker chưa được gắn tài khoản: nghỉ lâu rồi hỏi lại
API_ERROR_DELAY = 15         # Giây nghỉ khi không gọi được API
SUBMIT_RETRIES = 3           # Gửi kết quả lỗi mạng: thử lại bấy nhiêu lần

LEASE_SECONDS = 15 * 60      # Server cho 15 phút để trả kết quả
LEASE_SAFETY = 90            # Còn ít hơn bấy nhiêu giây thì KHÔNG bấm bước cuối (tránh bài trùng khi job đã bị cho thất bại)
LOGIN_CHECK_INTERVAL = 10    # Đang chờ đăng nhập: kiểm tra lại mỗi bấy nhiêu giây
LOGIN_WAIT_RESERVE = 5 * 60  # Đang giữ job mà chưa đăng nhập: chờ tối đa (15 phút - bấy nhiêu), chừa thời gian đăng bài
CLOSE_AFTER_SUCCESS_DELAY = 1  # Đăng xong: dừng bấy nhiêu giây rồi tắt Chrome
HUMAN_DELAY_MIN = 1          # Mỗi thao tác trên trình duyệt nghỉ ngẫu nhiên từ HUMAN_DELAY_MIN
HUMAN_DELAY_MAX = 3          # đến HUMAN_DELAY_MAX giây

JOB_DIR = os.path.join(BASE_DIR, "job")
JPEG_QUALITY = 95
