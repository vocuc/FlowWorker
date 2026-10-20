"""
Publisher: phần riêng của từng nền tảng trên điện thoại Android thật (app TikTok / Facebook / Instagram đăng nhập sẵn).
Phiên làm việc = điện thoại (flowkit.device), dùng chung cho mọi nền tảng của máy đó; không có đăng nhập tay:
app bị đăng xuất thì job báo {PREFIX}_LOGIN_REQUIRED ngay. Vòng xử lý 1 job (ảnh, báo kết quả) ở flowkit.runner.

Cấu hình theo máy (NAME = PHONE1, PHONE2...) + nền tảng (PREFIX = TIKTOK / FACEBOOK / INSTAGRAM), trong .env:
  {NAME}_{PREFIX}_TOKEN       token worker của tài khoản nền tảng đăng nhập trên máy này
  {NAME}_{PREFIX}_AUTO_POST   (hoặc {PREFIX}_AUTO_POST) 1 (mặc định) = bấm nút đăng;
                              0 = chạy thử: điền xong bài, không bấm, báo lỗi AUTO_POST_DISABLED
"""

import os

from ..config import JOB_DIR, env, env_first, log
from ..errors import JobError


class Publisher:
    platform = ""            # tiktok | facebook | instagram (khớp platform của job)
    label = ""               # Tên hiển thị
    package = ""             # Tên gói app trên Android
    permissions = []         # Quyền cấp sẵn (pm grant) để app không hỏi giữa chừng
    supports_auto_post = True

    def __init__(self, phone):
        self.prefix = self.platform.upper()
        self.phone = phone
        self.token = env(f"{phone.name}_{self.prefix}_TOKEN", "")
        self.auto_post = env_first([f"{phone.name}_{self.prefix}_AUTO_POST", f"{self.prefix}_AUTO_POST"], "1") != "0"
        self.work_dir = os.path.join(JOB_DIR, phone.name.lower(), self.platform)
        self.error_dir = os.path.join(self.work_dir, "errors")

    def error(self, suffix, message):
        """JobError với mã {PREFIX}_{suffix}, vd INSTAGRAM_UPLOAD_FAILED."""
        return JobError(f"{self.prefix}_{suffix}", message)

    # ---- Phiên làm việc: điện thoại ----

    def start_session(self):
        return self.phone.get()

    def session_alive(self, device):
        return device.alive()

    def close_session(self, device):
        """Đăng xong: xoá ảnh của job khỏi máy, về màn hình chính (máy vẫn kết nối cho job sau)."""
        try:
            device.clear_images()
            device.home()
        except Exception as e:
            log(f"Lỗi khi dọn máy sau khi đăng (bỏ qua): {e}")

    def screenshot(self, device, name):
        if device is None:
            return
        try:
            log(f"Đã lưu màn hình lỗi: {device.capture(self.error_dir, name)}.png / .xml")
        except Exception as e:
            log(f"Không chụp được màn hình (bỏ qua): {e}")

    def describe_error(self, e):
        """Nội dung lỗi lạ (không phải JobError) gửi về server."""
        return str(e)

    # ---- Đăng nhập ----

    def open_app(self, device):
        """Bật màn hình, mở app từ đầu, đóng popup."""
        device.wake()
        device.grant(self.package, self.permissions)
        device.start_app(self.package, self.prefix)
        log(f"ĐÃ MỞ APP {self.label.upper()} (bản {device.app_version(self.package)}) trên {self.phone.name}")
        device.dismiss_popups()

    def is_logged_in(self, device):
        raise NotImplementedError

    def wait_until_logged_in(self, device, deadline=None):
        """Không chờ đăng nhập tay: app chưa đăng nhập -> báo lỗi luôn (đăng nhập lại trên máy rồi đăng lại bài)."""
        self.open_app(device)
        self.check_blocked(device)
        if not self.is_logged_in(device):
            raise self.error("LOGIN_REQUIRED", f"App {self.label} trên {self.phone.name} chưa đăng nhập / bị đăng xuất")

    def check_blocked(self, device):
        """Nền tảng yêu cầu xác minh / khoá tài khoản -> {PREFIX}_CHECKPOINT."""

    def check_account(self, device, account):
        """Đăng bằng tài khoản đang đăng nhập trên máy, không so với request.account. Mặc định: chỉ ghi log."""
        log(f"Đăng bằng tài khoản {self.label} đang đăng nhập trên máy")

    # ---- Đăng bài ----

    def prepare_images(self, paths):
        """Sửa ảnh cho hợp nền tảng (vd Instagram: chèn viền thành hình vuông). Mặc định giữ nguyên."""

    def publish(self, device, request, paths, guard):
        """
        Đăng bài: ảnh paths (đúng thứ tự, ảnh đầu là bìa), nội dung request['description'].
        Gọi self.before_final_click(guard) ngay trước nút cuối (Đăng / Chia sẻ).
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
        return {"id": 0, "name": "Thử", "username": "", "target_url": ""}
