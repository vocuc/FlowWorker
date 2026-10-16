"""
Instagram: mở instagram.com, bấm Tạo -> Bài viết, đưa ảnh vào ô chọn file, giữ tỉ lệ gốc, Tiếp -> Tiếp,
điền chú thích, bấm Chia sẻ, chờ Instagram báo đã chia sẻ.
Instagram chỉ nhận tỉ lệ từ 4:5 đến 1.91:1: ảnh được chèn viền trắng trước khi tải lên (không cắt mất ảnh).
"""

import time

from selenium.webdriver.common.by import By

from .. import browser
from ..browser import find_button, find_visible, human_delay, js_click
from ..config import log
from ..media import pad_to_aspect
from .base import Publisher

INSTAGRAM_HOME = "https://www.instagram.com/"
# API nội bộ của Instagram web: thông tin tài khoản đang đăng nhập (gọi bằng fetch trong trang, kèm cookie)
INSTAGRAM_ACCOUNT_URL = "https://www.instagram.com/api/v1/accounts/edit/web_form_data/"
INSTAGRAM_APP_ID = "936619743392459"

UPLOAD_TIMEOUT = 120   # Thời gian tối đa chờ Instagram nhận ảnh (hiện bước cắt ảnh)
SHARE_TIMEOUT = 180    # Thời gian tối đa chờ Instagram báo đã chia sẻ

MIN_RATIO, MAX_RATIO = 4 / 5, 1.91

# ---- Giao diện Instagram (đổi ở đây khi Instagram đổi giao diện) ----
CREATE_LABELS = ["New post", "Bài viết mới", "Create", "Tạo"]
POST_MENU_TEXTS = ["Post", "Bài viết"]
DIALOG_XPATH = "//div[@role='dialog']"
FILE_INPUT_XPATH = "//div[@role='dialog']//input[@type='file']"
NEXT_TEXTS = ["Next", "Tiếp"]
CROP_LABELS = ["Select crop", "Chọn khung cắt", "Chọn kích thước cắt"]
ORIGINAL_TEXTS = ["Original", "Gốc", "Ảnh gốc"]
CAPTION_XPATH = "//div[@role='dialog']//div[@role='textbox' and @contenteditable='true']"
SHARE_TEXTS = ["Share", "Chia sẻ"]
SHARED_TEXTS = ["your post has been shared", "post shared", "đã chia sẻ bài viết", "bài viết của bạn đã được chia sẻ"]
SHARE_FAILED_TEXTS = ["couldn't be shared", "could not be shared", "không thể chia sẻ", "không chia sẻ được"]


class InstagramPublisher(Publisher):
    platform = "instagram"
    label = "Instagram"
    default_profile = r"C:\selenium-instagram-profile"
    default_port = 9225

    # ---- Đăng nhập ----

    def on_platform(self, driver):
        return "instagram.com" in (driver.current_url or "")

    def open_home(self, driver):
        driver.get(INSTAGRAM_HOME)
        time.sleep(5)
        human_delay()

    def is_logged_in(self, driver):
        """Instagram đặt cookie sessionid khi đã đăng nhập."""
        if "/accounts/login" in (driver.current_url or ""):
            return False
        return any(c.get("name") == "sessionid" and c.get("value") for c in driver.get_cookies())

    def current_username(self, driver):
        try:
            data = driver.execute_async_script(
                "const done = arguments[arguments.length - 1];"
                "fetch(arguments[0], {credentials: 'include', headers: {'X-IG-App-ID': arguments[1]}})"
                ".then(r => r.json()).then(done).catch(() => done(null));",
                INSTAGRAM_ACCOUNT_URL, INSTAGRAM_APP_ID,
            )
            username = ((data or {}).get("form_data") or {}).get("username")
            return username.strip().lstrip("@").lower() if username else None
        except Exception:
            return None

    def check_account(self, driver, account):
        expected = ((account or {}).get("username") or "").strip().lstrip("@").lower()
        if not self.is_logged_in(driver):
            raise self.error("LOGIN_REQUIRED", "Tài khoản Instagram bị đăng xuất")
        if not expected:
            log("Job không có username Instagram, bỏ qua kiểm tra tài khoản")
            return
        actual = self.current_username(driver)
        if actual is None:
            log("CẢNH BÁO: không đọc được username Instagram đang đăng nhập, bỏ qua kiểm tra tài khoản")
            return
        if actual != expected:
            raise self.error("ACCOUNT_MISMATCH", f"Chrome đang đăng nhập @{actual}, job cần @{expected}")
        log(f"Đang đăng nhập đúng tài khoản @{actual}")

    def check_blocked(self, driver):
        url = driver.current_url or ""
        if "/challenge" in url:
            raise self.error("CHECKPOINT", f"Instagram yêu cầu xác minh tài khoản ({url})")
        if "/accounts/login" in url:
            raise self.error("LOGIN_REQUIRED", "Instagram chuyển về trang đăng nhập giữa chừng")

    # ---- Đăng bài ----

    def prepare_images(self, paths):
        padded = sum(1 for path in paths if pad_to_aspect(path, MIN_RATIO, MAX_RATIO))
        if padded:
            log(f"ĐÃ CHÈN VIỀN {padded} ảnh về tỉ lệ Instagram nhận (4:5 - 1.91:1)")

    def publish(self, driver, request, paths, guard):
        if len(paths) > 10:
            raise self.error("TOO_MANY_IMAGES", f"Instagram tối đa 10 ảnh mỗi bài, bài có {len(paths)} ảnh")
        driver.get(INSTAGRAM_HOME)
        time.sleep(5)
        human_delay()
        self.check_blocked(driver)

        self.open_create_dialog(driver)
        self.upload_images(driver, paths)
        self.keep_original_ratio(driver)
        caption = self.go_to_caption(driver)

        description = request.get("description") or ""
        if description:
            browser.insert_text(driver, caption, description, shift_enter=True)
            actual = browser.element_text(caption)
            if not browser.same_text(actual, description):
                raise self.error("COMPOSE_FAILED", f"Ô chú thích không khớp bài cần đăng: đang có {actual[:100]!r}")
            log(f"ĐÃ ĐIỀN CHÚ THÍCH ({len(description)} ký tự)")

        dialog = find_visible(driver, By.XPATH, DIALOG_XPATH)
        button = find_button(dialog, SHARE_TEXTS, by_label=False) if dialog is not None else None
        if button is None:
            raise self.error("SHARE_FAILED", "Không tìm thấy nút Chia sẻ")
        self.before_final_click(guard)
        js_click(driver, button)
        log("ĐÃ CLICK: Chia sẻ")
        self.wait_shared(driver)
        return None

    def open_create_dialog(self, driver):
        """Bấm "Tạo" (thanh bên trái), có menu thì chọn "Bài viết", chờ hộp thoại có ô chọn file."""
        deadline = time.time() + 30
        while time.time() < deadline:
            self.check_blocked(driver)
            if driver.find_elements(By.XPATH, FILE_INPUT_XPATH):
                log("ĐÃ MỞ HỘP THOẠI TẠO BÀI")
                return
            menu_item = find_button(driver, POST_MENU_TEXTS, by_label=False)
            if menu_item is not None:
                js_click(driver, menu_item)
                time.sleep(2)
                continue
            # Nút Tạo là <svg aria-label="New post"> trong một liên kết / nút
            clicked = driver.execute_script(
                "const svg = [...document.querySelectorAll('svg[aria-label]')]"
                ".find(s => arguments[0].includes(s.getAttribute('aria-label')));"
                "const target = svg && svg.closest('a, [role=link], [role=button]');"
                "if (target) { target.click(); return true; } return false;",
                CREATE_LABELS,
            )
            time.sleep(2 if clicked else 3)
        raise self.error("COMPOSER_NOT_FOUND", "Không mở được hộp thoại tạo bài (không thấy nút Tạo / ô chọn ảnh)")

    def upload_images(self, driver, paths):
        file_input = next(iter(driver.find_elements(By.XPATH, FILE_INPUT_XPATH)), None)
        if file_input is None:
            raise self.error("UPLOAD_FAILED", "Không thấy ô chọn ảnh trong hộp thoại tạo bài")
        human_delay()
        file_input.send_keys("\n".join(paths))
        log(f"ĐÃ CHỌN {len(paths)} ẢNH, đang chờ Instagram nhận ảnh...")

        started = time.time()
        while time.time() - started < UPLOAD_TIMEOUT:
            time.sleep(2)
            self.check_blocked(driver)
            dialog = find_visible(driver, By.XPATH, DIALOG_XPATH)
            if dialog is not None and find_button(dialog, NEXT_TEXTS, by_label=False) is not None:
                log(f"INSTAGRAM ĐÃ NHẬN ẢNH sau {int(time.time() - started)} giây")
                return
        raise self.error("UPLOAD_FAILED", f"Sau {UPLOAD_TIMEOUT} giây Instagram chưa nhận ảnh")

    def keep_original_ratio(self, driver):
        """Mặc định Instagram cắt vuông: chọn "Gốc" (ảnh đã được chèn viền về tỉ lệ cho phép). Không thấy thì bỏ qua."""
        dialog = find_visible(driver, By.XPATH, DIALOG_XPATH)
        crop = find_button(dialog, CROP_LABELS) if dialog is not None else None
        if crop is None:
            crop_svg = driver.execute_script(
                "const svg = [...document.querySelectorAll('[role=dialog] svg[aria-label]')]"
                ".find(s => arguments[0].includes(s.getAttribute('aria-label')));"
                "return svg ? svg.closest('button, [role=button]') : null;",
                CROP_LABELS,
            )
            crop = crop_svg
        if crop is None:
            log("Không thấy nút chọn khung cắt, giữ mặc định")
            return
        js_click(driver, crop)
        original = find_button(driver, ORIGINAL_TEXTS)
        if original is None:
            original = driver.execute_script(
                "const el = [...document.querySelectorAll('[role=dialog] *')]"
                ".find(e => e.children.length === 0 && arguments[0].includes((e.textContent || '').trim()));"
                "return el ? (el.closest('button, [role=button]') || el) : null;",
                ORIGINAL_TEXTS,
            )
        if original is None:
            log("Không thấy lựa chọn 'Gốc', giữ mặc định")
            return
        js_click(driver, original)
        log("ĐÃ CHỌN TỈ LỆ GỐC")

    def go_to_caption(self, driver):
        """Bấm Tiếp (cắt ảnh -> chỉnh sửa -> chú thích) tới khi có ô chú thích."""
        for _ in range(4):
            caption = find_visible(driver, By.XPATH, CAPTION_XPATH)
            if caption is not None:
                return caption
            dialog = find_visible(driver, By.XPATH, DIALOG_XPATH)
            next_button = find_button(dialog, NEXT_TEXTS, by_label=False) if dialog is not None else None
            if next_button is None:
                break
            js_click(driver, next_button)
            time.sleep(2)
        caption = find_visible(driver, By.XPATH, CAPTION_XPATH)
        if caption is None:
            raise self.error("COMPOSE_FAILED", "Không tới được bước viết chú thích")
        return caption

    def wait_shared(self, driver):
        started = time.time()
        while time.time() - started < SHARE_TIMEOUT:
            time.sleep(2)
            self.check_blocked(driver)
            text = browser.page_text(driver)
            if any(t in text for t in SHARE_FAILED_TEXTS):
                raise self.error("SHARE_FAILED", "Instagram báo không chia sẻ được bài viết")
            if any(t in text for t in SHARED_TEXTS) or driver.find_elements(By.XPATH, "//img[@alt='Animated checkmark']"):
                log(f"ĐÃ CHIA SẺ BÀI VIẾT sau {int(time.time() - started)} giây")
                return
        raise self.error("SHARE_FAILED", f"Sau {SHARE_TIMEOUT} giây Instagram chưa báo đã chia sẻ")
