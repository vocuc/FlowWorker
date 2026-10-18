"""
Facebook: mở trang / nhóm / trang cá nhân admin khai báo (request.account.target_url), mở ô tạo bài, điền nội dung,
tải ảnh lên, chờ tải xong rồi bấm Đăng, chờ hộp thoại đóng.
Không dùng class CSS kiểu "x1lliihq" (Facebook đổi liên tục): chỉ dựa vào role, aria-label và chữ (tiếng Việt + tiếng Anh).
"""

import time

from selenium.webdriver.common.by import By

from .. import browser
from ..browser import find_button, find_visible, human_delay, is_shown, js_click
from ..config import log
from .base import Publisher

FACEBOOK_HOME = "https://www.facebook.com/"

UPLOAD_TIMEOUT = 300   # Thời gian tối đa chờ Facebook tải ảnh lên xong
POST_TIMEOUT = 120     # Thời gian tối đa chờ hộp thoại tạo bài đóng sau khi bấm Đăng

# ---- Giao diện Facebook (đổi ở đây khi Facebook đổi giao diện) ----
# Ô mở hộp thoại tạo bài: chữ bắt đầu bằng (có thể kèm tên: "Bạn đang nghĩ gì, An?")
COMPOSER_PREFIXES = [
    "Bạn viết gì đi", "Bạn đang nghĩ gì", "Tạo bài viết công khai", "Viết gì đó",
    "Write something", "What's on your mind", "What’s on your mind", "Create a public post", "Create post",
]
DIALOG_XPATH = "//div[@role='dialog'][.//div[@role='textbox' and @contenteditable='true']]"
TEXTBOX_XPATH = ".//div[@role='textbox' and @contenteditable='true']"
FILE_INPUT_XPATH = ".//input[@type='file' and contains(@accept, 'image')]"
PHOTO_BUTTON_TEXTS = ["Ảnh/video", "Photo/video", "Ảnh/Video", "Photo/Video"]
POST_BUTTON_TEXTS = ["Đăng", "Post"]


class FacebookPublisher(Publisher):
    platform = "facebook"
    label = "Facebook"
    default_profile = r"C:\selenium-facebook-profile"
    default_port = 9224

    # ---- Đăng nhập ----

    def on_platform(self, driver):
        return "facebook.com" in (driver.current_url or "")

    def open_home(self, driver):
        driver.get(FACEBOOK_HOME)
        time.sleep(5)
        human_delay()

    def is_logged_in(self, driver):
        """Facebook đặt cookie c_user (id người dùng) khi đã đăng nhập."""
        if "/login" in (driver.current_url or ""):
            return False
        return any(c.get("name") == "c_user" and c.get("value") for c in driver.get_cookies())

    def check_blocked(self, driver):
        url = driver.current_url or ""
        if "/checkpoint" in url:
            raise self.error("CHECKPOINT", f"Facebook yêu cầu xác minh tài khoản ({url})")
        if "/login" in url:
            raise self.error("LOGIN_REQUIRED", "Facebook chuyển về trang đăng nhập giữa chừng")

    # ---- Đăng bài ----

    def publish(self, driver, request, paths, guard):
        target = ((request.get("account") or {}).get("target_url") or "").strip()
        if not target:
            raise self.error("TARGET_MISSING", "Tài khoản Facebook chưa khai báo link nơi đăng bài (admin -> Tài khoản đăng bài)")

        log(f"MỞ NƠI ĐĂNG BÀI: {target}")
        driver.get(target)
        time.sleep(5)
        human_delay()
        self.check_blocked(driver)

        dialog = self.open_composer(driver)
        description = request.get("description") or ""
        if description:
            self.fill_text(driver, dialog, description)
        self.upload_images(driver, dialog, paths)
        # Thêm ảnh xong Facebook có thể vẽ lại ô soạn: kiểm tra nội dung lần cuối
        if description:
            self.assert_text(dialog, description)

        button = find_button(dialog, POST_BUTTON_TEXTS)
        if button is None or not browser.button_ready(button):
            raise self.error("POST_FAILED", "Không tìm thấy nút Đăng hoặc nút đang khoá")
        self.before_final_click(guard)
        js_click(driver, button)
        log("ĐÃ CLICK: Đăng")
        self.wait_posted(driver, dialog)
        return None

    def open_composer(self, driver):
        """Bấm ô "Bạn đang nghĩ gì?" / "Bạn viết gì đi..." rồi chờ hộp thoại tạo bài có ô soạn thảo."""
        deadline = time.time() + 30
        while time.time() < deadline:
            self.check_blocked(driver)
            dialog = find_visible(driver, By.XPATH, DIALOG_XPATH)
            if dialog is not None:
                log("ĐÃ MỞ HỘP THOẠI TẠO BÀI")
                return dialog
            trigger = find_button(driver, COMPOSER_PREFIXES, startswith=True)
            if trigger is not None:
                js_click(driver, trigger)
                time.sleep(2)
            else:
                time.sleep(2)
        raise self.error("COMPOSER_NOT_FOUND", "Không mở được hộp thoại tạo bài (không thấy ô 'Bạn đang nghĩ gì?' / 'Bạn viết gì đi...')")

    def textbox(self, dialog):
        box = find_visible(dialog, By.XPATH, TEXTBOX_XPATH)
        if box is None:
            raise self.error("COMPOSE_FAILED", "Không thấy ô nội dung trong hộp thoại tạo bài")
        return box

    def assert_text(self, dialog, text):
        actual = browser.element_text(self.textbox(dialog))
        if not browser.same_text(actual, text):
            raise self.error("COMPOSE_FAILED", f"Ô nội dung không khớp bài cần đăng: đang có {actual[:100]!r}")

    def fill_text(self, driver, dialog, text):
        browser.insert_text(driver, self.textbox(dialog), text, shift_enter=True)
        self.assert_text(dialog, text)
        log(f"ĐÃ ĐIỀN NỘI DUNG ({len(text)} ký tự)")

    def upload_images(self, driver, dialog, paths):
        """Đưa ảnh vào ô chọn file của hộp thoại (bấm "Ảnh/video" nếu ô chưa có), chờ tải xong."""
        file_input = next(iter(dialog.find_elements(By.XPATH, FILE_INPUT_XPATH)), None)
        if file_input is None:
            button = find_button(dialog, PHOTO_BUTTON_TEXTS)
            if button is None:
                raise self.error("UPLOAD_FAILED", "Không thấy nút 'Ảnh/video' trong hộp thoại tạo bài")
            js_click(driver, button)
            deadline = time.time() + 15
            while file_input is None and time.time() < deadline:
                file_input = next(iter(dialog.find_elements(By.XPATH, FILE_INPUT_XPATH)), None)
                time.sleep(1)
        if file_input is None:
            raise self.error("UPLOAD_FAILED", "Không thấy ô chọn ảnh trong hộp thoại tạo bài")

        human_delay()
        file_input.send_keys("\n".join(paths))
        log(f"ĐÃ CHỌN {len(paths)} ẢNH, đang chờ Facebook tải lên...")

        # Xong khi: đã có ảnh xem trước, không còn thanh tiến trình, nút Đăng bấm được (3 lần kiểm tra liên tiếp)
        started = time.time()
        stable = 0
        while time.time() - started < UPLOAD_TIMEOUT:
            time.sleep(2)
            self.check_blocked(driver)
            previews = driver.execute_script(
                "return [...arguments[0].querySelectorAll('img')]"
                ".filter(i => /^(blob|data):/.test(i.src) && i.complete && i.naturalWidth > 0).length;",
                dialog,
            )
            uploading = any(is_shown(e) for e in dialog.find_elements(By.XPATH, ".//*[@role='progressbar']"))
            button = find_button(dialog, POST_BUTTON_TEXTS)
            ready = previews > 0 and not uploading and button is not None and browser.button_ready(button)
            stable = stable + 1 if ready else 0
            if stable >= 3:
                log(f"ẢNH ĐÃ TẢI LÊN sau {int(time.time() - started)} giây")
                return
        raise self.error("UPLOAD_FAILED", f"Sau {UPLOAD_TIMEOUT} giây Facebook chưa tải xong ảnh")

    def wait_posted(self, driver, dialog):
        """Đăng xong hộp thoại tạo bài đóng lại."""
        started = time.time()
        while time.time() - started < POST_TIMEOUT:
            time.sleep(2)
            self.check_blocked(driver)
            if not is_shown(dialog) or find_visible(driver, By.XPATH, DIALOG_XPATH) is None:
                log(f"ĐÃ ĐĂNG BÀI sau {int(time.time() - started)} giây")
                return
        raise self.error("POST_FAILED", f"Sau {POST_TIMEOUT} giây hộp thoại tạo bài vẫn chưa đóng")
