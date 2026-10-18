"""
TikTok trên app Android (bản Việt Nam / châu Á com.ss.android.ugc.trill, bản quốc tế com.zhiliaoapp.musically):
mở app, bấm Quay (+) -> Tải lên (thư viện), tab Tất cả, bấm nút tròn chọn ảnh 1..N (ô đầu = ảnh 01 vừa đẩy vào),
Tiếp (N) -> màn chỉnh sửa (TikTok tự gắn nhạc) Tiếp -> điền tiêu đề + mô tả -> ĐĂNG THẲNG, chờ app đăng xong.
Dò trên TikTok 47.1.4 (HyperOS, tiếng Việt). Resource-id của TikTok bị làm rối và đổi theo phiên bản: chỉ dùng chữ / mô tả.
"""

import re
import time
import xml.etree.ElementTree as ET

from ..config import log
from ..device import normalize
from .base import Publisher

SHARE_TIMEOUT = 300    # Thời gian tối đa chờ app đăng xong (tải ảnh lên)
QUIET_SECONDS = 12     # Không thấy tiến độ tải lên bấy nhiêu giây liền (đã về trang chủ) = đã đăng xong
MAX_IMAGES = 35        # TikTok nhận tối đa 35 ảnh mỗi bài

# ---- Giao diện app TikTok (đổi ở đây khi app đổi giao diện; xem file .xml cạnh ảnh lỗi) ----
PACKAGES = ["com.ss.android.ugc.trill", "com.zhiliaoapp.musically"]
HOME_TAB_DESCS = ["Trang chủ", "Home"]
PROFILE_TAB_DESCS = ["Hồ sơ", "Profile"]
CREATE_DESCS = ["Quay", "Tạo", "Create", "Post"]
LOGIN_TEXTS = ["Đăng nhập vào TikTok", "Log in to TikTok", "Đăng ký TikTok", "Sign up for TikTok"]
RESUME_DRAFT_TEXTS = ["Tiếp tục chỉnh sửa bài đăng này?", "Continue editing this post?"]
UPLOAD_AREA_RES = r".*:id/upload_hot_area"       # Ô "Tải lên" góc trái dưới màn camera (id không bị làm rối)
ALL_TAB_DESCS = ["Tất cả", "All"]
MULTI_SELECT_TEXTS = ["Chọn nhiều", "Select multiple"]
NEXT_TEXTS = ["Tiếp", "Next"]
STORY_TEXTS = ["Nhật ký", "Story"]               # Nút đăng tin 24h ở màn chỉnh sửa ("Nhật ký 24h", "Nhật ký của bạn"): KHÔNG bấm
POST_TEXTS = ["Đăng", "Post"]
DRAFT_TEXTS = ["Nháp", "Drafts"]
UPLOADING_REGEX = r"^\s*\d{1,3}\s*%\s*$"            # Tiến độ tải lên (vd 45%) trên trang chủ sau khi bấm Đăng
UPLOADING_TEXTS = ["đang đăng", "đang tải lên", "posting", "uploading"]
FAILED_TEXTS = ["không thể đăng", "đăng không thành công", "tải lên không thành công", "couldn't post", "upload failed"]
CHECKPOINT_TEXTS = ["xác minh để tiếp tục", "verify to continue", "tài khoản của bạn đã bị cấm", "account banned",
                    "tài khoản bị khóa", "your account was permanently banned"]


class TikTokPublisher(Publisher):
    platform = "tiktok"
    label = "TikTok"
    package = PACKAGES[0]
    permissions = ["android.permission.CAMERA", "android.permission.RECORD_AUDIO",
                   "android.permission.READ_MEDIA_IMAGES", "android.permission.READ_MEDIA_VIDEO",
                   "android.permission.READ_MEDIA_VISUAL_USER_SELECTED", "android.permission.READ_EXTERNAL_STORAGE",
                   "android.permission.POST_NOTIFICATIONS"]

    def open_app(self, device):
        # Bản Việt Nam / châu Á và bản quốc tế khác tên gói: dùng bản nào máy đang cài
        installed = [p for p in PACKAGES if device.shell(["pm", "path", p]).strip()]
        if installed:
            self.package = installed[0]
        super().open_app(device)
        self.dismiss_resume_banner(device)

    def dismiss_resume_banner(self, device):
        """Bài soạn dở của lần trước (chạy thử AUTO_POST=0, job lỗi): vuốt bỏ thanh "Tiếp tục chỉnh sửa", KHÔNG lưu nháp."""
        banner = device.find(texts=RESUME_DRAFT_TEXTS, timeout=3)
        if banner is None:
            return
        left, top, right, bottom = banner.bounds()
        device.d.swipe((left + right) // 2, bottom + 40, (left + right) // 2, max(top - 120, 10), 0.15)
        time.sleep(2)
        log("ĐÃ BỎ bài soạn dở của lần trước")

    # ---- Đăng nhập ----

    def is_logged_in(self, device):
        if device.find(descs=CREATE_DESCS, timeout=10) is None:
            return False
        return not device.screen_has(LOGIN_TEXTS)

    def check_blocked(self, device):
        if device.screen_has(CHECKPOINT_TEXTS):
            raise self.error("CHECKPOINT", "TikTok yêu cầu xác minh / khoá tài khoản: xử lý tay trên điện thoại")

    def current_username(self, device):
        element = device.d(textStartsWith="@")
        if not element.wait(timeout=5):
            return None
        return (element.get_text() or "").strip().lstrip("@").lower() or None

    def check_account(self, device, account):
        """
        Đăng bằng tài khoản đang đăng nhập trên máy (không so với job): chỉ mở tab Hồ sơ để ghi log @username.
        Hồ sơ đòi đăng nhập -> LOGIN_REQUIRED.
        """
        if not device.tap("tab Hồ sơ", descs=PROFILE_TAB_DESCS, timeout=5):
            raise self.error("LOGIN_REQUIRED", "Không thấy tab Hồ sơ của TikTok")
        time.sleep(2)
        if device.screen_has(LOGIN_TEXTS):
            raise self.error("LOGIN_REQUIRED", f"App TikTok trên {self.phone.name} chưa đăng nhập / bị đăng xuất")
        username = self.current_username(device)
        log(f"Đăng bằng tài khoản đang đăng nhập trên máy: @{username or '(không đọc được username)'}")
        device.tap(descs=HOME_TAB_DESCS)

    # ---- Đăng bài ----

    def publish(self, device, request, paths, guard):
        if len(paths) > MAX_IMAGES:
            raise self.error("TOO_MANY_IMAGES", f"TikTok tối đa {MAX_IMAGES} ảnh mỗi bài, bài có {len(paths)} ảnh")
        device.push_images(paths)

        self.open_gallery(device)
        self.select_images(device, len(paths))
        self.go_to_post_screen(device)
        self.fill_content(device, request)

        post = device.find(texts=POST_TEXTS, className="android.widget.Button", timeout=5)
        if post is None:
            raise self.error("POST_FAILED", "Không tìm thấy nút Đăng")
        self.before_final_click(guard)
        device.click(post, "Đăng")
        self.wait_posted(device)
        return None

    def open_gallery(self, device):
        """Quay (+) -> màn camera -> ô Tải lên -> thư viện (tab Tất cả)."""
        device.tap(descs=HOME_TAB_DESCS)
        deadline = time.time() + 40
        while time.time() < deadline:
            self.check_blocked(device)
            if device.find(descs=ALL_TAB_DESCS) is not None and device.find(texts=MULTI_SELECT_TEXTS) is not None:
                tab = device.find(descs=ALL_TAB_DESCS)
                if not tab.info.get("selected"):
                    device.click(tab, "tab Tất cả")
                log("ĐÃ MỞ THƯ VIỆN ẢNH CỦA TIKTOK")
                return
            if device.tap("ô Tải lên", res=UPLOAD_AREA_RES):
                time.sleep(2)
                continue
            if not device.tap("Quay (+)", descs=CREATE_DESCS) and not device.dismiss_popups():
                self.dismiss_resume_banner(device)
            time.sleep(3)
        raise self.error("COMPOSER_NOT_FOUND", "Không mở được thư viện ảnh của TikTok (Quay -> Tải lên)")

    def select_circles(self, device):
        """
        Nút tròn chọn ảnh trên các ô thư viện (Button nhỏ, không mô tả, chữ rỗng hoặc số thứ tự đã chọn), nằm giữa hàng tab
        và nút Tiếp, theo thứ tự ô: trái -> phải, trên -> dưới. Trả về [(x, y, chữ)].
        """
        tab = device.find(descs=ALL_TAB_DESCS)
        grid_top = tab.bounds()[3] if tab is not None else 0
        grid_bottom = device.d.window_size()[1]
        circles = []
        for node in ET.fromstring(device.d.dump_hierarchy()).iter("node"):
            numbers = [int(n) for n in re.findall(r"\d+", node.get("bounds") or "")]
            if len(numbers) != 4 or node.get("class") != "android.widget.Button":
                continue
            left, top, right, bottom = numbers
            text = (node.get("text") or "").strip()
            if re.fullmatch(rf"(?i)(?:{'|'.join(NEXT_TEXTS)}).*", text):
                grid_bottom = min(grid_bottom, top)
                continue
            if (node.get("clickable") == "true" and not node.get("content-desc") and (text == "" or text.isdigit())
                    and 0 < right - left < 150 and 0 < bottom - top < 150 and top > grid_top):
                circles.append((top // 50, left, (left + right) // 2, (top + bottom) // 2, text, bottom))
        circles = [c for c in circles if c[5] < grid_bottom]
        circles.sort(key=lambda c: (c[0], c[1]))
        return [(x, y, text) for _, _, x, y, text, _ in circles]

    def select_images(self, device, count):
        """Bấm nút tròn của ô 1..N (ô đầu = ảnh 01), kiểm tra số thứ tự hiện trên từng ô, rồi nút "Tiếp (N)"."""
        circles = self.select_circles(device)
        if not circles:
            # Chưa bật Chọn nhiều: các ô chưa có nút tròn
            device.tap("Chọn nhiều", texts=MULTI_SELECT_TEXTS)
            circles = self.select_circles(device)
        if len(circles) < count:
            raise self.error("UPLOAD_FAILED", f"Thư viện TikTok chỉ thấy {len(circles)} ô chọn được, cần {count} ảnh")
        if any(text for _, _, text in circles):
            raise self.error("UPLOAD_FAILED", "Thư viện TikTok đang có ảnh được chọn sẵn, không chọn tiếp để tránh sai ảnh")
        for i in range(count):
            for _ in range(3):
                x, y, text = self.select_circles(device)[i]
                if text == str(i + 1):
                    break
                device.d.click(x, y)
                time.sleep(1.5)
            else:
                raise self.error("UPLOAD_FAILED", f"Không chọn được ảnh {i + 1} trong thư viện TikTok")
        next_button = device.d(textMatches=rf"(?i)\s*(?:{'|'.join(NEXT_TEXTS)})\s*\(\s*{count}\s*\)\s*")
        if not next_button.exists:
            raise self.error("UPLOAD_FAILED", f"Nút Tiếp không báo đã chọn {count} ảnh")
        log(f"ĐÃ CHỌN {count} ẢNH")
        device.click(next_button, f"Tiếp ({count})")

    def go_to_post_screen(self, device):
        """Màn chỉnh sửa ảnh -> bấm Tiếp (KHÔNG bấm "Nhật ký" = đăng tin 24h) -> màn đăng bài (2 ô nhập + nút Đăng)."""
        deadline = time.time() + 60
        while time.time() < deadline:
            self.check_blocked(device)
            if (device.d(className="android.widget.EditText").count >= 2
                    and device.find(texts=POST_TEXTS, className="android.widget.Button") is not None):
                log("ĐÃ TỚI MÀN ĐĂNG BÀI")
                return
            if device.find(texts=STORY_TEXTS, contains=True) is not None:
                next_button = device.find(texts=NEXT_TEXTS)
                if next_button is not None:
                    device.click(next_button, "Tiếp (màn chỉnh sửa)")
                    time.sleep(3)
                    continue
            device.dismiss_popups()
            time.sleep(2)
        raise self.error("COMPOSE_FAILED", "Không tới được màn đăng bài (sau màn chỉnh sửa ảnh)")

    def fill_content(self, device, request):
        """Ô nhập 1 = tiêu đề, ô 2 = mô tả. Điền xong đọc lại, không khớp thì báo lỗi."""
        fields = [("tiêu đề", 0, (request.get("title") or "").strip()), ("mô tả", 1, request.get("description") or "")]
        for what, index, text in fields:
            if not text:
                log(f"Không có {what}, giữ nguyên ô {what}")
                continue
            element = device.d(className="android.widget.EditText")[index]
            actual = device.input_text(element, text)
            if normalize(actual) != normalize(text):
                raise self.error("COMPOSE_FAILED", f"Ô {what} không khớp bài cần đăng: đang có {actual[:100]!r}")
            log(f"ĐÃ ĐIỀN {what.upper()} ({len(text)} ký tự)")
        device.hide_keyboard()

    def wait_posted(self, device):
        """
        Sau khi bấm Đăng app về trang chủ, hiện tiến độ tải lên (vd 45%): chờ tới khi hết.
        App báo không đăng được -> POST_FAILED. Ảnh màn hình sau khi đăng lưu vào thư mục errors/ (tên posted-*) để đối chiếu.
        """
        started = time.time()
        quiet_since = None
        while time.time() - started < SHARE_TIMEOUT:
            time.sleep(3)
            self.check_blocked(device)
            text = device.screen_text()
            if any(t in text for t in FAILED_TEXTS) and device.find(texts=POST_TEXTS, className="android.widget.Button") is None:
                raise self.error("POST_FAILED", "TikTok báo không đăng được bài")
            if device.find(texts=POST_TEXTS, className="android.widget.Button") is not None and \
                    device.d(className="android.widget.EditText").count >= 2:
                quiet_since = None  # Vẫn ở màn đăng bài (đang xử lý / có hộp thoại)
                device.dismiss_popups()
                continue
            uploading = any(t in text for t in UPLOADING_TEXTS) or \
                any(re.match(UPLOADING_REGEX, line) for line in text.splitlines())
            if uploading or device.find(descs=HOME_TAB_DESCS) is None:
                quiet_since = None
                device.dismiss_popups()
                continue
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= QUIET_SECONDS:
                log(f"ĐÃ ĐĂNG BÀI sau {int(time.time() - started)} giây")
                self.screenshot(device, "posted")
                return
        raise self.error("POST_FAILED", f"Sau {SHARE_TIMEOUT} giây TikTok chưa đăng xong")
