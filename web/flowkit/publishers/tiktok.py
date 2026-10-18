"""
TikTok: mở TikTok Studio, tải ảnh lên, điền tiêu đề + mô tả, bấm LƯU NHÁP (không bao giờ bấm Đăng).
Nhân viên A51 vào TikTok đăng các bài nháp. Nghiệp vụ: note/dang-tiktok-qua-worker.txt ở repo a90-fe.
"""

import time

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

import pyperclip

from .. import browser
from ..browser import find_button, find_visible, human_delay, is_shown, js_click
from ..config import log
from .base import Publisher

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp&tab=photo"
# API nội bộ của TikTok web, trả thông tin tài khoản đang đăng nhập (gọi bằng fetch trong trang, kèm cookie)
TIKTOK_ACCOUNT_INFO_URL = "https://www.tiktok.com/passport/web/account/info/"

UPLOAD_TIMEOUT = 300   # Thời gian tối đa chờ TikTok tải ảnh lên xong
DRAFT_TIMEOUT = 60     # Thời gian tối đa chờ TikTok báo đã lưu nháp

# ---- Giao diện TikTok Studio (đổi ở đây khi TikTok đổi giao diện) ----
# Khung tải ảnh: nút "Select photos" + ô <input type="file" accept="image/..." multiple> ẩn cùng khung.
# KHÔNG bấm nút (mở hộp thoại chọn file của Windows, Selenium không điều khiển được): đưa đường dẫn thẳng vào ô ẩn.
SELECT_PHOTOS_TEXTS = ["Select photos", "Chọn ảnh"]
FILE_INPUT_CSS = "input[type='file'][accept*='image']"
# Trang soạn bài ảnh (/tiktokstudio/upload/post/photo), chọn ảnh xong TikTok tự chuyển sang:
#   ô tiêu đề <input class="titleInput-..." placeholder="Add a catchy title">,
#   ô mô tả DraftJS <div class="public-DraftEditor-content" contenteditable="true">,
#   lưới ảnh <div class="photoItem-..." data-id="file_..."><img src="blob:...">, nút Post / Save draft / Discard.
CAPTION_EDITOR_CSS = "div.public-DraftEditor-content[contenteditable='true'], div[contenteditable='true']"
TITLE_INPUT_XPATH = ("//input[contains(@class, 'titleInput')]"
                     " | //input[contains(@placeholder, 'tiêu đề') or contains(@placeholder, 'Tiêu đề')"
                     " or contains(@placeholder, 'title') or contains(@placeholder, 'Title')]")
PHOTO_ITEM_CSS = "div[class*='photoItem'][data-id]"
SAVE_DRAFT_TEXTS = ["Save draft", "Lưu bản nháp", "Lưu nháp"]
DRAFT_SAVED_TEXTS = ["draft saved", "saved to drafts", "đã lưu bản nháp", "đã lưu nháp", "đã lưu vào bản nháp"]
CAPTCHA_CSS = "#captcha-verify-container, #captcha_container, .captcha-verify-container, iframe[src*='captcha']"


class TikTokPublisher(Publisher):
    platform = "tiktok"
    label = "TikTok"
    # Profile / cổng như tiktok_worker.py cũ: không phải đăng nhập lại
    default_profile = r"C:\selenium-tiktok-profile"
    default_port = 9223
    supports_auto_post = False  # chỉ lưu nháp

    # ---- Đăng nhập ----

    def on_platform(self, driver):
        return "tiktok.com" in (driver.current_url or "")

    def open_home(self, driver):
        driver.get(TIKTOK_UPLOAD_URL)
        time.sleep(5)
        human_delay()

    def is_logged_in(self, driver):
        """TikTok đặt cookie sessionid khi đã đăng nhập. Trang đăng nhập (/login) = chưa đăng nhập."""
        if "/login" in (driver.current_url or ""):
            return False
        return any(c.get("name") == "sessionid" and c.get("value") for c in driver.get_cookies())

    def current_username(self, driver):
        """Username TikTok đang đăng nhập (gọi API nội bộ của TikTok web trong trang). Không lấy được -> None."""
        try:
            data = driver.execute_async_script(
                "const done = arguments[arguments.length - 1];"
                "fetch(arguments[0], {credentials: 'include'}).then(r => r.json()).then(done).catch(() => done(null));",
                TIKTOK_ACCOUNT_INFO_URL,
            )
            username = ((data or {}).get("data") or {}).get("username")
            return username.strip().lstrip("@").lower() if username else None
        except Exception:
            return None

    def check_account(self, driver, account):
        """Đang đăng nhập đúng tài khoản TikTok của job (request.account.username)."""
        expected = ((account or {}).get("username") or "").strip().lstrip("@").lower()
        if not self.is_logged_in(driver):
            raise self.error("LOGIN_REQUIRED", "Tài khoản TikTok bị đăng xuất")
        if not expected:
            log("Job không có username TikTok, bỏ qua kiểm tra tài khoản")
            return
        actual = self.current_username(driver)
        if actual is None:
            log("CẢNH BÁO: không đọc được username TikTok đang đăng nhập, bỏ qua kiểm tra tài khoản")
            return
        if actual != expected:
            raise self.error("ACCOUNT_MISMATCH", f"Chrome đang đăng nhập @{actual}, job cần @{expected}")
        log(f"Đang đăng nhập đúng tài khoản @{actual}")

    def check_captcha(self, driver):
        if any(is_shown(e) for e in driver.find_elements(By.CSS_SELECTOR, CAPTCHA_CSS)):
            raise self.error("CAPTCHA", "TikTok bắt giải captcha")

    def check_still_logged_in(self, driver):
        if "/login" in (driver.current_url or ""):
            raise self.error("LOGIN_REQUIRED", "TikTok chuyển về trang đăng nhập giữa chừng")

    # ---- Đăng bài ----

    def publish(self, driver, request, paths, guard):
        self.upload_images(driver, paths)
        log("ĐIỀN NỘI DUNG")
        self.fill_content(driver, request)
        self.before_final_click(guard)
        log("LƯU NHÁP")
        self.save_draft(driver)
        return None

    def loaded_photo_count(self, driver):
        """Số ảnh đã hiện trong lưới ảnh của trang soạn bài (ảnh đã load xong)."""
        return driver.execute_script(
            "return [...document.querySelectorAll(arguments[0])]"
            ".filter(el => { const img = el.querySelector('img'); return img && img.complete && img.naturalWidth > 0; })"
            ".length;",
            PHOTO_ITEM_CSS,
        )

    def upload_images(self, driver, paths):
        """Mở trang tải lên, đưa toàn bộ ảnh (đúng thứ tự) vào ô chọn file, chờ TikTok tải xong."""
        driver.get(TIKTOK_UPLOAD_URL)
        time.sleep(5)
        human_delay()
        self.check_still_logged_in(driver)
        self.check_captcha(driver)

        # Chờ nút "Select photos" hiện (trang tải ảnh đã sẵn sàng), lấy ô chọn file ẩn nằm cùng khung với nút
        deadline = time.time() + 60
        file_input = None
        while time.time() < deadline and file_input is None:
            button = find_button(driver, SELECT_PHOTOS_TEXTS, by_label=False)
            if button is not None:
                file_input = next(iter(button.find_elements(
                    By.XPATH, "./ancestor::div[.//input[@type='file']][1]//input[@type='file']")), None)
                if file_input is None:
                    file_input = next(iter(driver.find_elements(By.CSS_SELECTOR, FILE_INPUT_CSS)), None)
            if file_input is None:
                self.check_captcha(driver)
                time.sleep(2)
        if file_input is None:
            raise self.error("UPLOAD_FAILED", "Không thấy nút 'Select photos' / ô chọn ảnh trên TikTok Studio")
        log("ĐÃ THẤY NÚT 'Select photos', đưa ảnh vào ô chọn file")

        # Ô chọn file nhận nhiều đường dẫn cách nhau bằng xuống dòng, giữ thứ tự (ảnh đầu là ảnh bìa)
        human_delay()
        file_input.send_keys("\n".join(paths))
        human_delay()
        log(f"ĐÃ CHỌN {len(paths)} ẢNH, đang chờ TikTok tải lên...")

        # TikTok chuyển sang trang soạn bài ảnh: chờ đủ ảnh trong lưới, có ô mô tả và nút Save draft bấm được
        started = time.time()
        last_count = -1
        while time.time() - started < UPLOAD_TIMEOUT:
            time.sleep(3)
            self.check_still_logged_in(driver)
            self.check_captcha(driver)
            count = self.loaded_photo_count(driver)
            if count != last_count:
                log(f"Đã hiện {count}/{len(paths)} ảnh trên trang soạn bài")
                last_count = count
            if count < len(paths):
                continue
            draft = find_button(driver, SAVE_DRAFT_TEXTS, by_label=False)
            editor = find_visible(driver, By.CSS_SELECTOR, CAPTION_EDITOR_CSS)
            if draft is not None and editor is not None and browser.button_ready(draft):
                time.sleep(3)  # Chờ ảnh cuối xử lý xong
                log(f"ẢNH ĐÃ TẢI LÊN sau {int(time.time() - started)} giây ({driver.current_url})")
                return
        raise self.error("UPLOAD_FAILED",
                         f"Sau {UPLOAD_TIMEOUT} giây TikTok mới hiện {max(last_count, 0)}/{len(paths)} ảnh hoặc chưa sẵn sàng lưu nháp")

    def paste_into(self, driver, element, text, what):
        """
        Điền text vào ô: dán (Ctrl+V) trước, không vào thì chèn từng dòng bằng CDP Input.insertText (Enter giữa các dòng).
        Dán / chèn không gõ từng phím nên ít bật gợi ý hashtag; cuối cùng Esc để đóng gợi ý nếu có. Kiểm tra lại nội dung.
        """
        browser.focus_and_clear(driver, element)
        pyperclip.copy(text)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
        human_delay()

        if not browser.same_text(browser.element_text(element), text):
            log(f"Dán {what} không vào, chuyển sang chèn trực tiếp")
            browser.insert_text(driver, element, text, shift_enter=False)

        # Đóng gợi ý hashtag / nhắc tên nếu có, để không chọn nhầm
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        human_delay()
        if not browser.same_text(browser.element_text(element), text):
            raise self.error("UPLOAD_FAILED", f"Không điền được {what}: ô đang có {browser.element_text(element)[:100]!r}")

    def fill_content(self, driver, request):
        title = (request.get("title") or "").strip()
        if title:
            title_input = find_visible(driver, By.XPATH, TITLE_INPUT_XPATH)
            if title_input is not None:
                self.paste_into(driver, title_input, title, "tiêu đề")
                log("ĐÃ ĐIỀN TIÊU ĐỀ")
            else:
                log("Trang không có ô tiêu đề, bỏ qua tiêu đề")

        description = request.get("description") or ""
        editor = find_visible(driver, By.CSS_SELECTOR, CAPTION_EDITOR_CSS)
        if editor is None:
            raise self.error("UPLOAD_FAILED", "Không tìm thấy ô mô tả trên TikTok Studio")
        if description:
            self.paste_into(driver, editor, description, "mô tả")
            log(f"ĐÃ ĐIỀN MÔ TẢ ({len(description)} ký tự)")
        else:
            log("Mô tả rỗng, giữ nguyên ô mô tả")

    def save_draft(self, driver):
        """Bấm LƯU NHÁP (không bao giờ bấm Đăng) rồi chờ TikTok báo đã lưu / rời trang soạn bài."""
        self.check_captcha(driver)
        button = find_button(driver, SAVE_DRAFT_TEXTS, by_label=False)
        if button is None or not browser.button_ready(button):
            raise self.error("DRAFT_FAILED", "Không tìm thấy nút Lưu nháp hoặc nút đang khoá")

        # Đọc chữ nút TRƯỚC khi bấm: bấm xong TikTok chuyển trang / vẽ lại, nút cũ mất (stale element)
        label = " ".join(button.text.split())
        url_before = driver.current_url
        js_click(driver, button)
        log(f"ĐÃ CLICK: {label}")

        started = time.time()
        while time.time() - started < DRAFT_TIMEOUT:
            time.sleep(1)
            self.check_captcha(driver)
            # Lưu xong TikTok rời trang soạn bài (sang trang quản lý nội dung / trang tải lên mới) hoặc báo "draft saved"
            url = driver.current_url
            left_editor = url != url_before or find_visible(driver, By.CSS_SELECTOR, CAPTION_EDITOR_CSS) is None
            saved_toast = any(t in browser.page_text(driver) for t in DRAFT_SAVED_TEXTS)
            if left_editor or saved_toast:
                log(f"ĐÃ LƯU NHÁP sau {int(time.time() - started)} giây ({url})")
                return
        raise self.error("DRAFT_FAILED", f"Sau {DRAFT_TIMEOUT} giây TikTok chưa báo đã lưu nháp")
