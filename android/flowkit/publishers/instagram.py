"""
Instagram trên app Android: mở app, Tạo -> Bài viết, chọn ảnh vừa đẩy vào máy (ô đầu tiên của thư viện = ảnh 01;
nhiều ảnh thì bật Chọn nhiều), Tiếp -> Tiếp, điền chú thích, bấm Chia sẻ, chờ app đăng xong.
Ảnh được chèn viền trắng thành hình vuông: khung cắt mặc định nào của app cũng không cắt mất ảnh.
"""

import time

from ..config import log
from ..device import normalize
from ..media import pad_to_aspect
from .base import Publisher

SHARE_TIMEOUT = 300    # Thời gian tối đa chờ app đăng xong (tải ảnh lên)
QUIET_SECONDS = 12     # Không thấy "Đang đăng" bấy nhiêu giây liền (đã về trang chủ) = đã đăng xong

# ---- Giao diện app Instagram (đổi ở đây khi app đổi giao diện; xem file .xml cạnh ảnh lỗi) ----
HOME_TAB_DESCS = ["Home", "Trang chủ"]
PROFILE_TAB_DESCS = ["Profile", "Trang cá nhân"]
LOGIN_TEXTS = ["Log in", "Đăng nhập", "Create new account", "Tạo tài khoản mới"]
CREATE_DESCS = ["Create", "New post", "Tạo", "Bài viết mới", "Tạo bài viết"]
POST_MODE_TEXTS = ["POST", "Post", "BÀI VIẾT", "Bài viết"]
MULTI_SELECT_DESCS = ["Select multiple", "Chọn nhiều", "Chọn nhiều mục", "Chọn nhiều ảnh"]
MULTI_SELECT_TEXTS = ["SELECT", "Select", "CHỌN", "Chọn"]
GRID_ITEM_RES = r".*gallery_grid_item.*"
NEXT_TEXTS = ["Next", "Tiếp", "Tiếp tục"]
CAPTION_RES = r".*caption_input_text_view.*"
SHARE_TEXTS = ["Share", "Chia sẻ"]
POSTING_TEXTS = ["posting", "đang đăng", "finishing up", "đang hoàn tất", "uploading", "đang tải lên", "sharing", "đang chia sẻ"]
FAILED_TEXTS = ["couldn't post", "couldn't upload", "couldn't share", "không thể đăng", "không đăng được",
                "không thể chia sẻ", "tap to retry", "nhấn để thử lại"]
CHECKPOINT_TEXTS = ["confirm it's you", "xác nhận đó là bạn", "help us confirm", "we suspended your account",
                    "đã đình chỉ tài khoản", "your account has been disabled", "tài khoản của bạn đã bị vô hiệu hóa"]


class InstagramPublisher(Publisher):
    platform = "instagram"
    label = "Instagram"
    package = "com.instagram.android"
    permissions = ["android.permission.READ_MEDIA_IMAGES", "android.permission.READ_MEDIA_VISUAL_USER_SELECTED",
                   "android.permission.READ_EXTERNAL_STORAGE", "android.permission.POST_NOTIFICATIONS"]

    # ---- Đăng nhập ----

    def is_logged_in(self, device):
        if device.find(descs=HOME_TAB_DESCS, timeout=10) is not None:
            return True
        return not device.screen_has(LOGIN_TEXTS) and device.find(descs=PROFILE_TAB_DESCS) is not None

    def check_blocked(self, device):
        if device.screen_has(CHECKPOINT_TEXTS):
            raise self.error("CHECKPOINT", "Instagram yêu cầu xác minh / khoá tài khoản: xử lý tay trên điện thoại")

    def check_account(self, device, account):
        """Đăng bằng tài khoản đang đăng nhập trên máy (không so với job)."""
        log("Đăng bằng tài khoản Instagram đang đăng nhập trên máy")

    # ---- Đăng bài ----

    def prepare_images(self, paths):
        padded = sum(1 for path in paths if pad_to_aspect(path, 1, 1))
        if padded:
            log(f"ĐÃ CHÈN VIỀN {padded} ảnh thành hình vuông")

    def publish(self, device, request, paths, guard):
        if len(paths) > 10:
            raise self.error("TOO_MANY_IMAGES", f"Instagram tối đa 10 ảnh mỗi bài, bài có {len(paths)} ảnh")
        device.push_images(paths)

        self.open_composer(device)
        self.select_images(device, len(paths))
        caption = self.go_to_caption(device)

        description = request.get("description") or ""
        if description:
            actual = device.input_text(caption, description)
            if normalize(actual) != normalize(description):
                raise self.error("COMPOSE_FAILED", f"Ô chú thích không khớp bài cần đăng: đang có {actual[:100]!r}")
            log(f"ĐÃ ĐIỀN CHÚ THÍCH ({len(description)} ký tự)")
        device.hide_keyboard()  # Bàn phím che nút Chia sẻ trên một số máy

        share = device.find(texts=SHARE_TEXTS, descs=SHARE_TEXTS, timeout=5)
        if share is None:
            raise self.error("SHARE_FAILED", "Không tìm thấy nút Chia sẻ")
        self.before_final_click(guard)
        device.click(share, "Chia sẻ")
        self.wait_shared(device)
        return None

    def open_composer(self, device):
        """Bấm nút Tạo (+), chọn chế độ Bài viết, chờ thư viện ảnh hiện ra."""
        device.tap(descs=HOME_TAB_DESCS)
        deadline = time.time() + 30
        while time.time() < deadline:
            self.check_blocked(device)
            if device.find(res=GRID_ITEM_RES) is not None:
                mode = device.find(texts=POST_MODE_TEXTS)
                if mode is not None and not mode.info.get("selected"):
                    device.click(mode, "chế độ Bài viết")
                log("ĐÃ MỞ MÀN HÌNH TẠO BÀI")
                return
            if not device.tap("Tạo", descs=CREATE_DESCS) and not device.dismiss_popups():
                time.sleep(2)
            time.sleep(2)
        raise self.error("COMPOSER_NOT_FOUND", "Không mở được màn hình tạo bài (không thấy nút Tạo / thư viện ảnh)")

    def select_images(self, device, count):
        """Ô 1 = ảnh 01 (mới nhất). Nhiều ảnh: bấm ô 1 để xem trước, bật Chọn nhiều (ô 1 thành số 1), bấm tiếp ô 2..N."""
        items = device.d(resourceIdMatches=GRID_ITEM_RES)
        if items.count < count:
            raise self.error("UPLOAD_FAILED", f"Thư viện ảnh trong app chỉ có {items.count} ô, cần {count} ảnh")
        device.click(items[0], "ảnh 1")
        if count > 1:
            if not device.tap("Chọn nhiều", descs=MULTI_SELECT_DESCS, texts=MULTI_SELECT_TEXTS, timeout=5):
                raise self.error("UPLOAD_FAILED", "Không thấy nút Chọn nhiều ảnh")
            for i in range(1, count):
                device.click(device.d(resourceIdMatches=GRID_ITEM_RES)[i], f"ảnh {i + 1}")
            if device.find(texts=[str(count)]) is None:
                log(f"CẢNH BÁO: không thấy số thứ tự {count} trên ảnh đã chọn, kiểm tra ảnh lỗi nếu bài sai")
        log(f"ĐÃ CHỌN {count} ẢNH")

    def go_to_caption(self, device):
        """Bấm Tiếp (chọn ảnh -> chỉnh sửa -> chú thích) tới khi có ô chú thích."""
        for _ in range(4):
            caption = device.find(res=CAPTION_RES, timeout=3)
            if caption is not None:
                return caption
            device.dismiss_popups()
            if not device.tap("Tiếp", texts=NEXT_TEXTS, descs=NEXT_TEXTS, timeout=5):
                break
            time.sleep(3)
        caption = device.find(res=CAPTION_RES, timeout=5)
        if caption is None and device.d(className="android.widget.EditText").exists:
            caption = device.d(className="android.widget.EditText")
        if caption is None:
            raise self.error("COMPOSE_FAILED", "Không tới được bước viết chú thích")
        return caption

    def wait_shared(self, device):
        """Sau khi bấm Chia sẻ app về trang chủ và hiện "Đang đăng...": chờ tới khi hết, báo lỗi nếu app báo không đăng được."""
        started = time.time()
        quiet_since = None
        while time.time() - started < SHARE_TIMEOUT:
            time.sleep(3)
            self.check_blocked(device)
            if device.screen_has(FAILED_TEXTS):
                raise self.error("SHARE_FAILED", "Instagram báo không đăng được bài viết")
            device.dismiss_popups()
            if device.screen_has(POSTING_TEXTS) or device.find(descs=HOME_TAB_DESCS) is None:
                quiet_since = None
                continue
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= QUIET_SECONDS:
                log(f"ĐÃ ĐĂNG BÀI VIẾT sau {int(time.time() - started)} giây")
                return
        raise self.error("SHARE_FAILED", f"Sau {SHARE_TIMEOUT} giây Instagram chưa đăng xong")
