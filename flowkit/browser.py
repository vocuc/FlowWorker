"""Chrome (Selenium) dùng chung cho các worker: mở / tắt, tìm nút theo chữ, click bằng JS, điền chữ, chụp màn hình lỗi."""

import os
import random
import time
from datetime import datetime

from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

from .config import HUMAN_DELAY_MAX, HUMAN_DELAY_MIN, log


def human_delay():
    """Nghỉ ngẫu nhiên giữa các thao tác như người thật (HUMAN_DELAY_MIN..HUMAN_DELAY_MAX giây)."""
    time.sleep(random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX))


def chrome_options(profile_dir, profile_directory, debug_port):
    options = Options()
    options.add_argument("--start-maximized")
    # Profile RIÊNG cho từng worker (đăng nhập sẵn tài khoản của nền tảng), cổng riêng để chạy song song trên 1 máy
    options.add_argument(f"user-data-dir={profile_dir}")
    options.add_argument(f"profile-directory={profile_directory}")
    options.add_argument(f"--remote-debugging-port={debug_port}")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    # Tránh bị nhận dạng bot
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    # Chrome chỉ tự tắt sau khi đăng thành công; job lỗi / dừng worker thì giữ Chrome mở để xem lại
    options.add_experimental_option("detach", True)
    return options


def start_chrome(profile_dir, profile_directory, debug_port):
    log(f"Đang mở Chrome (profile {profile_dir}\\{profile_directory}, cổng {debug_port})...")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options(profile_dir, profile_directory, debug_port),
    )
    log("Chrome đã mở")
    return driver


def close_chrome(driver):
    log("Đang tắt Chrome...")
    try:
        driver.quit()
        log("Chrome đã tắt")
    except Exception as e:
        log(f"Lỗi khi tắt Chrome (bỏ qua): {e}")


def chrome_alive(driver):
    """Chrome có thể đã tắt (sau khi đăng xong, người dùng tắt, crash): lần sau có job thì mở lại."""
    try:
        driver.current_url
        return True
    except Exception:
        return False


def is_shown(element):
    """is_displayed() nhưng phần tử đã mất (trang vừa chuyển) thì coi như không hiện thay vì ném lỗi."""
    try:
        return element.is_displayed()
    except StaleElementReferenceException:
        return False


def find_visible(root, by, value):
    """Phần tử đang hiện đầu tiên khớp (root: driver hoặc một phần tử, vd hộp thoại)."""
    return next((e for e in root.find_elements(by, value) if is_shown(e)), None)


def _norm(text):
    return " ".join((text or "").split()).lower()


def find_button(root, texts, by_label=True, startswith=False):
    """
    Nút đang hiện (<button> / role=button / role=link) có chữ hoặc aria-label khớp một trong texts
    (không phân biệt hoa thường, bỏ khoảng trắng thừa). startswith: chữ của nút chỉ cần bắt đầu bằng text.
    """
    wanted = [_norm(t) for t in texts]
    xpath = ".//button | .//div[@role='button'] | .//span[@role='button'] | .//a[@role='link'] | .//div[@role='link']"
    for button in root.find_elements("xpath", xpath):
        try:
            candidates = [_norm(button.text)]
            if by_label:
                candidates.append(_norm(button.get_attribute("aria-label")))
            matched = any(
                c and any(c.startswith(w) if startswith else c == w for w in wanted)
                for c in candidates
            )
            if matched and button.is_displayed():
                return button
        except StaleElementReferenceException:
            continue
    return None


def button_ready(button):
    """Nút đang bấm được: không disabled, không aria-disabled, không đang xoay (data-loading)."""
    try:
        return (button.is_enabled()
                and (button.get_attribute("aria-disabled") or "").lower() != "true"
                and (button.get_attribute("data-disabled") or "").lower() != "true"
                and (button.get_attribute("data-loading") or "").lower() != "true")
    except StaleElementReferenceException:
        return False


def js_click(driver, element):
    """Click bằng JS: không bị banner cookie / lớp phủ che nút."""
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    human_delay()
    driver.execute_script("arguments[0].click();", element)
    human_delay()


def page_text(driver):
    try:
        return (driver.execute_script("return document.body.innerText") or "").lower()
    except Exception:
        return ""


def element_text(element):
    """Nội dung hiện có của ô: value với <input>/<textarea>, chữ hiển thị với ô soạn thảo contenteditable."""
    if element.tag_name.lower() in ("input", "textarea"):
        return element.get_attribute("value") or ""
    return element.text or ""


def same_text(actual, expected):
    """So sánh bỏ qua khác biệt khoảng trắng / xuống dòng (ô soạn thảo tách mỗi dòng thành 1 khối)."""
    return " ".join(actual.split()) == " ".join(expected.split())


def focus_and_clear(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    human_delay()
    ActionChains(driver).move_to_element(element).click().perform()
    human_delay()
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.DELETE).perform()
    human_delay()


def insert_text(driver, element, text, shift_enter=True):
    """
    Điền text vào ô bằng CDP Input.insertText từng dòng (không dùng clipboard, không gõ từng phím nên ít bật gợi ý
    hashtag / nhắc tên). Giữa các dòng bấm Shift+Enter (xuống dòng, không gửi bài) hoặc Enter (shift_enter=False).
    """
    focus_and_clear(driver, element)
    for i, line in enumerate(text.split("\n")):
        if i > 0:
            chain = ActionChains(driver)
            if shift_enter:
                chain.key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT)
            else:
                chain.send_keys(Keys.ENTER)
            chain.perform()
            time.sleep(0.2)
        if line:
            driver.execute_cdp_cmd("Input.insertText", {"text": line})
        time.sleep(0.2)
    human_delay()


def screenshot(driver, error_dir, name):
    if driver is None:
        return
    try:
        os.makedirs(error_dir, exist_ok=True)
        path = os.path.join(error_dir, f"{name}-{datetime.now():%Y%m%d-%H%M%S}.png")
        driver.save_screenshot(path)
        log(f"Đã chụp màn hình lỗi: {path}")
    except Exception as e:
        log(f"Không chụp được màn hình (bỏ qua): {e}")
