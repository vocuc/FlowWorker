"""
Publisher: phần riêng của từng nền tảng (đăng nhập, kiểm tra tài khoản, thao tác đăng bài trên web).
Vòng lặp nhận job / báo kết quả / ảnh / logo dùng chung ở flowkit.runner.

Cấu hình theo nền tảng (PREFIX = TIKTOK / FACEBOOK / INSTAGRAM), đặt trong .env:
  {PREFIX}_WORKER_TOKEN               token worker (php artisan workers:create {tên}), admin gắn với tài khoản nền tảng
  {PREFIX}_CHROME_PROFILE             thư mục profile Chrome riêng của worker
  {PREFIX}_CHROME_PROFILE_DIRECTORY   profile con, mặc định Default
  {PREFIX}_CHROME_PORT                cổng remote debugging (mỗi worker một cổng để chạy song song)
  {PREFIX}_AUTO_POST                  1 (mặc định) = bấm nút đăng; 0 = chạy thử: điền xong bài, không bấm, báo lỗi AUTO_POST_DISABLED
  {PREFIX}_LOGO_*                     logo shop (xem flowkit.media.load_logo_config)
"""

import os
import time

from ..config import JOB_DIR, LOGIN_CHECK_INTERVAL, env, env_first, log
from ..errors import JobError


class Publisher:
    platform = ""            # tiktok | facebook | instagram (khớp platform của job)
    label = ""               # Tên hiển thị
    default_profile = ""
    default_port = 0
    supports_auto_post = True  # TikTok chỉ lưu nháp: không có bước "đăng"

    def __init__(self):
        self.prefix = self.platform.upper()
        # AI90_TIKTOK_WORKER_TOKEN: tên cũ của tiktok_worker.py
        self.token = env_first([f"{self.prefix}_WORKER_TOKEN", f"AI90_{self.prefix}_WORKER_TOKEN"], "")
        self.profile_dir = env(f"{self.prefix}_CHROME_PROFILE", self.default_profile)
        self.profile_directory = env(f"{self.prefix}_CHROME_PROFILE_DIRECTORY", "Default")
        self.port = int(env(f"{self.prefix}_CHROME_PORT", str(self.default_port)))
        self.auto_post = env(f"{self.prefix}_AUTO_POST", "1") != "0"
        self.work_dir = os.path.join(JOB_DIR, self.platform)
        self.error_dir = os.path.join(self.work_dir, "errors")

    def error(self, suffix, message):
        """JobError với mã {PREFIX}_{suffix}, vd FACEBOOK_UPLOAD_FAILED."""
        return JobError(f"{self.prefix}_{suffix}", message)

    # ---- Đăng nhập ----

    def is_logged_in(self, driver):
        raise NotImplementedError

    def open_home(self, driver):
        """Mở trang của nền tảng (để kiểm tra đăng nhập / người dùng đăng nhập tay)."""
        raise NotImplementedError

    def on_platform(self, driver):
        raise NotImplementedError

    def wait_until_logged_in(self, driver, deadline=None):
        """
        Mở trang của nền tảng, chưa đăng nhập thì DỪNG LẠI chờ người dùng đăng nhập tay trên cửa sổ Chrome.
        deadline: thời điểm (time.time()) phải đăng nhập xong, quá hạn -> {PREFIX}_LOGIN_REQUIRED. None = chờ mãi.
        """
        if not self.on_platform(driver):
            self.open_home(driver)
        if self.is_logged_in(driver):
            return

        limit = f" (tối đa {int((deadline - time.time()) // 60)} phút)" if deadline else ""
        log(f"CHƯA ĐĂNG NHẬP {self.label.upper()}! Hãy đăng nhập trên cửa sổ Chrome vừa mở{limit}...")
        waited = 0
        while not self.is_logged_in(driver):
            if deadline and time.time() >= deadline:
                raise self.error("LOGIN_REQUIRED", f"Hết thời gian chờ đăng nhập {self.label}")
            time.sleep(LOGIN_CHECK_INTERVAL)
            waited += LOGIN_CHECK_INTERVAL
            if waited % 60 == 0:
                log(f"Vẫn đang chờ đăng nhập {self.label} ({waited // 60} phút)...")
        log(f"ĐÃ ĐĂNG NHẬP {self.label.upper()}, TIẾP TỤC...")
        time.sleep(3)

    def check_account(self, driver, account):
        """Đang đăng nhập đúng tài khoản của job (request.account). Mặc định: không kiểm tra được, chỉ ghi log."""
        log(f"Không kiểm tra được tài khoản {self.label} đang đăng nhập, bỏ qua kiểm tra")

    # ---- Đăng bài ----

    def prepare_images(self, paths):
        """Sửa ảnh cho hợp nền tảng (vd Instagram: chèn viền về tỉ lệ cho phép). Mặc định giữ nguyên."""

    def publish(self, driver, request, paths, guard):
        """
        Đăng bài: ảnh paths (đúng thứ tự, ảnh đầu là bìa), nội dung request['description'].
        Gọi self.before_final_click(guard) ngay trước nút cuối (Đăng / Chia sẻ / Lưu nháp).
        Trả về link bài nếu lấy được, không thì None. Lỗi -> JobError.
        """
        raise NotImplementedError

    def before_final_click(self, guard):
        """Sắp hết hạn job thì không bấm (tránh bài trùng); chạy thử (AUTO_POST=0) thì dừng trước nút đăng."""
        guard()
        if self.supports_auto_post and not self.auto_post:
            raise self.error("AUTO_POST_DISABLED", f"Chạy thử ({self.prefix}_AUTO_POST=0): đã điền xong bài, không bấm đăng")

    def mock_account(self):
        """account trong job giả lập (chế độ --mock)."""
        return {"id": 0, "name": "Thử", "username": env(f"{self.prefix}_MOCK_USERNAME", ""),
                "target_url": env(f"{self.prefix}_MOCK_TARGET_URL", "")}
