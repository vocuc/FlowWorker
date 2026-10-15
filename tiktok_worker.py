"""
Worker đăng bài TikTok cho A51 (nghiệp vụ: note/dang-tiktok-qua-worker.txt ở repo a90-fe).

Vòng lặp:
  1. GET  {API_BASE_URL}/publish-jobs      -> nhận 1 job (bài của tài khoản A51 được gắn với worker này).
     Chưa có job thì KHÔNG mở Chrome. Lưu nháp xong dừng 1 giây rồi tắt Chrome; job lỗi thì giữ Chrome để xem lại.
  2. Có job: mở Chrome (profile riêng). CHƯA ĐĂNG NHẬP TikTok thì dừng lại chờ đăng nhập tay trên cửa sổ Chrome,
     tối đa 10 phút (job chỉ có 15 phút), quá hạn -> failed TIKTOK_LOGIN_REQUIRED.
     Tải ảnh, chèn logo shop (nếu cấu hình), mở TikTok Studio, tải ảnh lên, điền mô tả, bấm LƯU NHÁP (không bấm Đăng).
  3. POST {API_BASE_URL}/publish-jobs/{id} -> completed, hoặc failed (error_code, error_message).
Job lỗi / quá hạn không được giao lại: worker không tự làm lại.

Chế độ giả lập (AI90_USE_MOCK=1): không gọi API, chạy 1 lần với MOCK_JOB (ảnh lấy từ máy), VẪN lưu nháp thật lên TikTok.

Cấu hình qua biến môi trường:
  AI90_API_BASE_URL         mặc định https://admin.a51.vn/api/v1
  AI90_TIKTOK_WORKER_TOKEN  token tạo bằng: php artisan workers:create {name}  (bắt buộc khi gọi API thật)
  AI90_USE_MOCK             1 = chạy job giả lập
  TIKTOK_CHROME_PROFILE     thư mục profile Chrome riêng của worker, mặc định C:\\selenium-tiktok-profile
  TIKTOK_CHROME_PROFILE_DIRECTORY   profile con, mặc định Default
  TIKTOK_LOGO_PATH          file logo shop, mặc định logo/logo.jpg cạnh file này. Đặt rỗng -> không chèn logo.
  TIKTOK_LOGO_WIDTH / TIKTOK_LOGO_HEIGHT   % cạnh ngắn của ảnh (mặc định 10)
  TIKTOK_LOGO_RADIUS        % cạnh ngắn của logo, 0 = vuông, 50 = tròn (mặc định 20)
  TIKTOK_LOGO_X / TIKTOK_LOGO_Y            khoảng cách tới mép phải / mép dưới, % cạnh ngắn của ảnh (mặc định 3)

Chạy: python tiktok_worker.py   (Ctrl+C để dừng)
"""

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image, ImageChops, ImageDraw

import os
import pyperclip
import random
import requests
import shutil
import sys
import time
from datetime import datetime

# Console Windows (cp1252) không in được tiếng Việt -> ép stdout/stderr sang UTF-8
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# ======================================
# CẤU HÌNH
# ======================================
API_BASE_URL = os.environ.get("AI90_API_BASE_URL", "https://admin.a51.vn/api/v1").rstrip("/")
WORKER_TOKEN = os.environ.get("AI90_TIKTOK_WORKER_TOKEN", "cA18Du4HphfWpcCUNbWrnkHbrTyucvcMybCyAAXtMiSAdHUoFbNhG79OUhYOBMkD")
USE_MOCK = os.environ.get("AI90_USE_MOCK", "0") == "1"

POLL_INTERVAL = 5            # Giây nghỉ khi chưa có job
IDLE_LOG_INTERVAL = 60       # Khi chưa có job: cứ mỗi bấy nhiêu giây in 1 dòng "vẫn đang chờ"
NOT_ASSIGNED_DELAY = 60      # Worker chưa được gắn tài khoản A51: nghỉ lâu rồi hỏi lại
API_ERROR_DELAY = 15         # Giây nghỉ khi không gọi được API
HTTP_TIMEOUT = 120           # Timeout mỗi request tới API / tải ảnh
SUBMIT_RETRIES = 3           # Gửi kết quả lỗi mạng: thử lại bấy nhiêu lần

LEASE_SECONDS = 15 * 60      # Server cho 15 phút để trả kết quả
LEASE_SAFETY = 90            # Còn ít hơn bấy nhiêu giây thì KHÔNG bấm lưu nháp (tránh nháp trùng khi bài đã bị cho thất bại)
LOGIN_CHECK_INTERVAL = 10    # Đang chờ đăng nhập: kiểm tra lại mỗi bấy nhiêu giây
LOGIN_WAIT_RESERVE = 5 * 60  # Đang giữ job mà chưa đăng nhập: chờ tối đa (15 phút - bấy nhiêu), chừa thời gian đăng bài
UPLOAD_TIMEOUT = 300         # Thời gian tối đa chờ TikTok tải ảnh lên xong
CLOSE_AFTER_DRAFT_DELAY = 1  # Lưu nháp xong: dừng bấy nhiêu giây rồi tắt Chrome
HUMAN_DELAY_MIN = 1         # Mỗi thao tác trên trình duyệt nghỉ ngẫu nhiên từ HUMAN_DELAY_MIN
HUMAN_DELAY_MAX = 3          # đến HUMAN_DELAY_MAX giây
DRAFT_TIMEOUT = 60           # Thời gian tối đa chờ TikTok báo đã lưu nháp

TIKTOK_HOME_URL = "https://www.tiktok.com/"
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp&tab=photo"
# API nội bộ của TikTok web, trả thông tin tài khoản đang đăng nhập (gọi bằng fetch trong trang, kèm cookie)
TIKTOK_ACCOUNT_INFO_URL = "https://www.tiktok.com/passport/web/account/info/"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(BASE_DIR, "job", "tiktok")            # Thư mục tạm của từng job
ERROR_DIR = os.path.join(BASE_DIR, "job", "tiktok", "errors")  # Ảnh chụp màn hình khi lỗi
DEFAULT_LOGO_PATH = os.path.join(BASE_DIR, "logo", "logo.jpg")  # Logo shop chèn vào ảnh (đổi bằng TIKTOK_LOGO_PATH)
CHROME_PROFILE = os.environ.get("TIKTOK_CHROME_PROFILE", r"C:\selenium-tiktok-profile")
CHROME_PROFILE_DIRECTORY = os.environ.get("TIKTOK_CHROME_PROFILE_DIRECTORY", "Default")
JPEG_QUALITY = 95

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
POST_BUTTON_TEXTS = ["Đăng", "Post"]  # Chỉ để nhận biết, KHÔNG bao giờ bấm
DRAFT_SAVED_TEXTS = ["draft saved", "saved to drafts", "đã lưu bản nháp", "đã lưu nháp", "đã lưu vào bản nháp"]
CAPTCHA_CSS = "#captcha-verify-container, #captcha_container, .captcha-verify-container, iframe[src*='captcha']"

# Dữ liệu giả lập thay cho "data" của GET /publish-jobs (dùng khi AI90_USE_MOCK=1). images: link hoặc đường dẫn file trên máy.
MOCK_JOB = {
    "id": 0,
    "type": "tiktok_publish",
    "request": {
        "post_id": 0,
        "title": "Ảnh mới từ A51",
        "content": "Bài thử của worker đăng TikTok.",
        "hashtags": ["a51"],
        "description": "Bài thử của worker đăng TikTok.\n#a51",
        "images": [os.path.join(BASE_DIR, "job", "output", f) for f in sorted(os.listdir(os.path.join(BASE_DIR, "job", "output")))[:2]]
        if os.path.isdir(os.path.join(BASE_DIR, "job", "output")) else [],
        "account": {"id": 0, "name": "Thử", "username": os.environ.get("TIKTOK_MOCK_USERNAME", "")},
    },
}


def log(message=""):
    """In ra console kèm giờ, flush ngay để thấy log theo thời gian thực."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


class JobError(RuntimeError):
    """Lỗi khi xử lý job, kèm mã lỗi gửi về server (chữ hoa, số, '_')."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def human_delay():
    """Nghỉ ngẫu nhiên giữa các thao tác như người thật (HUMAN_DELAY_MIN..HUMAN_DELAY_MAX giây)."""
    time.sleep(random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX))


# ======================================
# CẤU HÌNH LOGO
# ======================================

def env_percent(name, default):
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return float(default)
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise SystemExit(f"Cấu hình {name}={raw!r} không phải số.")


def load_logo_config():
    """Đọc + kiểm tra cấu hình logo lúc khởi động. Trả về None nếu không chèn logo. Sai -> dừng worker."""
    if "TIKTOK_LOGO_PATH" in os.environ:
        path = os.environ["TIKTOK_LOGO_PATH"].strip()  # Đặt rỗng = tắt chèn logo
    else:
        path = DEFAULT_LOGO_PATH if os.path.isfile(DEFAULT_LOGO_PATH) else ""
    if not path:
        log("Không chèn logo (không có logo/logo.jpg và không đặt TIKTOK_LOGO_PATH)")
        return None

    config = {
        "path": path,
        "width": env_percent("TIKTOK_LOGO_WIDTH", 7),
        "height": env_percent("TIKTOK_LOGO_HEIGHT", 7),
        "radius": env_percent("TIKTOK_LOGO_RADIUS", 50),
        "x": env_percent("TIKTOK_LOGO_X", 2),
        "y": env_percent("TIKTOK_LOGO_Y", 2),
    }
    errors = []
    if not os.path.isfile(path):
        errors.append(f"không có file logo {path}")
    if not 0 < config["width"] <= 100:
        errors.append("TIKTOK_LOGO_WIDTH phải trong (0, 100]")
    if not 0 < config["height"] <= 100:
        errors.append("TIKTOK_LOGO_HEIGHT phải trong (0, 100]")
    if not 0 <= config["radius"] <= 50:
        errors.append("TIKTOK_LOGO_RADIUS phải trong [0, 50]")
    if not 0 <= config["x"] < 100 or config["width"] + config["x"] > 100:
        errors.append("TIKTOK_LOGO_X phải >= 0 và WIDTH + X <= 100")
    if not 0 <= config["y"] < 100 or config["height"] + config["y"] > 100:
        errors.append("TIKTOK_LOGO_Y phải >= 0 và HEIGHT + Y <= 100")
    if not errors:
        try:
            with Image.open(path) as logo:
                logo.verify()
        except Exception as e:
            errors.append(f"không đọc được file logo: {e}")
    if errors:
        raise SystemExit("CẤU HÌNH LOGO SAI: " + "; ".join(errors))

    log(f"Logo: {path} | rộng {config['width']}%, cao {config['height']}%, bo góc {config['radius']}%, "
        f"cách phải {config['x']}%, cách dưới {config['y']}% (theo cạnh ngắn của ảnh, góc dưới bên phải)")
    return config


LOGO = None  # Đọc trong main()


def apply_logo(image_path, logo_config):
    """Chèn logo vào góc dưới bên phải, mọi giá trị % tính theo cạnh ngắn của ảnh. Lưu đè file (JPG)."""
    try:
        with Image.open(image_path) as source:
            image = source.convert("RGBA")
        short_side = min(image.size)
        to_px = lambda percent: max(0, round(short_side * percent / 100))

        width, height = max(1, to_px(logo_config["width"])), max(1, to_px(logo_config["height"]))
        with Image.open(logo_config["path"]) as source:
            logo = source.convert("RGBA").resize((width, height), Image.LANCZOS)

        # Bo góc: nhân kênh trong suốt của logo với mặt nạ hình chữ nhật bo góc
        radius = round(min(width, height) * logo_config["radius"] / 100)
        if radius > 0:
            mask = Image.new("L", (width, height), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
            logo.putalpha(ImageChops.multiply(logo.getchannel("A"), mask))

        left = image.width - to_px(logo_config["x"]) - width
        top = image.height - to_px(logo_config["y"]) - height
        # Phần vượt ra ngoài ảnh (phía trên / trái) bị cắt: alpha_composite không nhận toạ độ âm -> cắt logo trước
        crop_left, crop_top = max(0, -left), max(0, -top)
        if crop_left or crop_top:
            logo = logo.crop((crop_left, crop_top, width, height))
        image.alpha_composite(logo, (max(0, left), max(0, top)))

        image.convert("RGB").save(image_path, "JPEG", quality=JPEG_QUALITY)
    except Exception as e:
        raise JobError("LOGO_FAILED", f"Không chèn được logo vào {os.path.basename(image_path)}: {e}")


# ======================================
# CHROME + ĐĂNG NHẬP TIKTOK
# ======================================

def chrome_options():
    options = Options()
    options.add_argument("--start-maximized")
    # Profile RIÊNG cho worker TikTok, không dùng chung với worker tạo ảnh (C:\selenium-profile, Profile1)
    options.add_argument(f"user-data-dir={CHROME_PROFILE}")
    options.add_argument(f"profile-directory={CHROME_PROFILE_DIRECTORY}")
    # Cổng khác worker tạo ảnh (9222) để chạy song song trên cùng máy
    options.add_argument("--remote-debugging-port=9223")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    # Tránh bị nhận dạng bot
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    # Chrome chỉ tự tắt sau khi lưu nháp thành công; job lỗi / dừng worker thì giữ Chrome mở để xem lại
    options.add_experimental_option("detach", True)
    return options


def start_chrome():
    log("Đang mở Chrome...")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options())
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
    """Chrome có thể đã tắt (sau khi lưu nháp, người dùng tắt, crash): lần sau có job thì mở lại."""
    try:
        driver.current_url
        return True
    except Exception:
        return False


def on_tiktok(driver):
    return "tiktok.com" in (driver.current_url or "")


def is_logged_in(driver):
    """TikTok đặt cookie sessionid khi đã đăng nhập. Trang đăng nhập (/login) = chưa đăng nhập."""
    if "/login" in (driver.current_url or ""):
        return False
    return any(c.get("name") == "sessionid" and c.get("value") for c in driver.get_cookies())


def wait_until_logged_in(driver, deadline=None):
    """
    Mở TikTok Studio, chưa đăng nhập thì DỪNG LẠI chờ người dùng đăng nhập tay trên cửa sổ Chrome.
    deadline: thời điểm (time.time()) phải đăng nhập xong, quá hạn -> TIKTOK_LOGIN_REQUIRED. None = chờ mãi.
    """
    if not on_tiktok(driver):
        driver.get(TIKTOK_UPLOAD_URL)
        time.sleep(5)
        human_delay()
    if is_logged_in(driver):
        return

    limit = f" (tối đa {int((deadline - time.time()) // 60)} phút)" if deadline else ""
    log(f"CHƯA ĐĂNG NHẬP TIKTOK! Hãy đăng nhập trên cửa sổ Chrome vừa mở{limit}...")
    if "/login" not in driver.current_url:
        driver.get(TIKTOK_UPLOAD_URL)
    waited = 0
    while not is_logged_in(driver):
        if deadline and time.time() >= deadline:
            raise JobError("TIKTOK_LOGIN_REQUIRED", "Hết thời gian chờ đăng nhập TikTok")
        time.sleep(LOGIN_CHECK_INTERVAL)
        waited += LOGIN_CHECK_INTERVAL
        if waited % 60 == 0:
            log(f"Vẫn đang chờ đăng nhập TikTok ({waited // 60} phút)...")
    log("ĐÃ ĐĂNG NHẬP TIKTOK, TIẾP TỤC...")
    time.sleep(3)


def current_username(driver):
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


def check_account(driver, account):
    """Đang đăng nhập đúng tài khoản TikTok của job (request.account.username)."""
    expected = ((account or {}).get("username") or "").strip().lstrip("@").lower()
    if not is_logged_in(driver):
        raise JobError("TIKTOK_LOGIN_REQUIRED", "Tài khoản TikTok bị đăng xuất")
    if not expected:
        log("Job không có username TikTok, bỏ qua kiểm tra tài khoản")
        return
    actual = current_username(driver)
    if actual is None:
        log("CẢNH BÁO: không đọc được username TikTok đang đăng nhập, bỏ qua kiểm tra tài khoản")
        return
    if actual != expected:
        raise JobError("TIKTOK_ACCOUNT_MISMATCH", f"Chrome đang đăng nhập @{actual}, job cần @{expected}")
    log(f"Đang đăng nhập đúng tài khoản @{actual}")


def is_shown(element):
    """is_displayed() nhưng phần tử đã mất (trang vừa chuyển) thì coi như không hiện thay vì ném lỗi."""
    try:
        return element.is_displayed()
    except StaleElementReferenceException:
        return False


def check_captcha(driver):
    if any(is_shown(e) for e in driver.find_elements(By.CSS_SELECTOR, CAPTCHA_CSS)):
        raise JobError("TIKTOK_CAPTCHA", "TikTok bắt giải captcha")


def check_still_logged_in(driver):
    if "/login" in (driver.current_url or ""):
        raise JobError("TIKTOK_LOGIN_REQUIRED", "TikTok chuyển về trang đăng nhập giữa chừng")


# ======================================
# THAO TÁC TRÊN TIKTOK STUDIO
# ======================================

def find_visible(driver, by, value):
    return next((e for e in driver.find_elements(by, value) if is_shown(e)), None)


def find_button(driver, texts):
    """Nút đang hiện có chữ khớp một trong texts (so sánh không phân biệt hoa thường, bỏ khoảng trắng thừa)."""
    wanted = [t.lower() for t in texts]
    for button in driver.find_elements(By.XPATH, "//button | //div[@role='button']"):
        try:
            text = " ".join((button.text or "").split()).lower()
            if text and text in wanted and button.is_displayed():
                return button
        except StaleElementReferenceException:
            continue
    return None


def button_ready(button):
    """Nút TikTok (TUX) đang bấm được: không disabled, không đang xoay (data-loading)."""
    return (button.is_enabled()
            and (button.get_attribute("aria-disabled") or "").lower() != "true"
            and (button.get_attribute("data-disabled") or "").lower() != "true"
            and (button.get_attribute("data-loading") or "").lower() != "true")


def loaded_photo_count(driver):
    """Số ảnh đã hiện trong lưới ảnh của trang soạn bài (ảnh đã load xong)."""
    return driver.execute_script(
        "return [...document.querySelectorAll(arguments[0])]"
        ".filter(el => { const img = el.querySelector('img'); return img && img.complete && img.naturalWidth > 0; })"
        ".length;",
        PHOTO_ITEM_CSS,
    )


def js_click(driver, element):
    """Click bằng JS: không bị banner cookie / lớp phủ che nút."""
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    human_delay()
    driver.execute_script("arguments[0].click();", element)
    human_delay()


def upload_images(driver, paths):
    """Mở trang tải lên, đưa toàn bộ ảnh (đúng thứ tự) vào ô chọn file, chờ TikTok tải xong."""
    driver.get(TIKTOK_UPLOAD_URL)
    time.sleep(5)
    human_delay()
    check_still_logged_in(driver)
    check_captcha(driver)

    # Chờ nút "Select photos" hiện (trang tải ảnh đã sẵn sàng), lấy ô chọn file ẩn nằm cùng khung với nút
    deadline = time.time() + 60
    file_input = None
    while time.time() < deadline and file_input is None:
        button = find_button(driver, SELECT_PHOTOS_TEXTS)
        if button is not None:
            file_input = next(iter(button.find_elements(
                By.XPATH, "./ancestor::div[.//input[@type='file']][1]//input[@type='file']")), None)
            if file_input is None:
                file_input = next(iter(driver.find_elements(By.CSS_SELECTOR, FILE_INPUT_CSS)), None)
        if file_input is None:
            check_captcha(driver)
            time.sleep(2)
    if file_input is None:
        raise JobError("TIKTOK_UPLOAD_FAILED", "Không thấy nút 'Select photos' / ô chọn ảnh trên TikTok Studio")
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
        check_still_logged_in(driver)
        check_captcha(driver)
        count = loaded_photo_count(driver)
        if count != last_count:
            log(f"Đã hiện {count}/{len(paths)} ảnh trên trang soạn bài")
            last_count = count
        if count < len(paths):
            continue
        draft = find_button(driver, SAVE_DRAFT_TEXTS)
        editor = find_visible(driver, By.CSS_SELECTOR, CAPTION_EDITOR_CSS)
        if draft is not None and editor is not None and button_ready(draft):
            time.sleep(3)  # Chờ ảnh cuối xử lý xong
            log(f"ẢNH ĐÃ TẢI LÊN sau {int(time.time() - started)} giây ({driver.current_url})")
            return
    raise JobError("TIKTOK_UPLOAD_FAILED",
                   f"Sau {UPLOAD_TIMEOUT} giây TikTok mới hiện {max(last_count, 0)}/{len(paths)} ảnh hoặc chưa sẵn sàng lưu nháp")


def field_text(element):
    """Nội dung hiện có của ô: value với <input>, chữ hiển thị với ô soạn thảo (DraftJS)."""
    if element.tag_name.lower() in ("input", "textarea"):
        return element.get_attribute("value") or ""
    return element.text or ""


def same_text(actual, expected):
    """So sánh bỏ qua khác biệt khoảng trắng / xuống dòng (DraftJS tách mỗi dòng thành 1 khối)."""
    return " ".join(actual.split()) == " ".join(expected.split())


def focus_and_clear(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    human_delay()
    ActionChains(driver).move_to_element(element).click().perform()
    human_delay()
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.DELETE).perform()
    human_delay()


def paste_into(driver, element, text, what):
    """
    Điền text vào ô: dán (Ctrl+V) trước, không vào thì chèn từng dòng bằng CDP Input.insertText (Enter giữa các dòng).
    Dán / chèn không gõ từng phím nên ít bật gợi ý hashtag; cuối cùng Esc để đóng gợi ý nếu có. Kiểm tra lại nội dung.
    """
    focus_and_clear(driver, element)
    pyperclip.copy(text)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
    human_delay()

    if not same_text(field_text(element), text):
        log(f"Dán {what} không vào, chuyển sang chèn trực tiếp")
        focus_and_clear(driver, element)
        for i, line in enumerate(text.split("\n")):
            if i > 0:
                ActionChains(driver).send_keys(Keys.ENTER).perform()
                human_delay()
            if line:
                driver.execute_cdp_cmd("Input.insertText", {"text": line})
            human_delay()

    # Đóng gợi ý hashtag / nhắc tên nếu có, để không chọn nhầm
    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    human_delay()
    if not same_text(field_text(element), text):
        raise JobError("TIKTOK_UPLOAD_FAILED", f"Không điền được {what}: ô đang có {field_text(element)[:100]!r}")


def fill_content(driver, request):
    title = (request.get("title") or "").strip()
    if title:
        title_input = find_visible(driver, By.XPATH, TITLE_INPUT_XPATH)
        if title_input is not None:
            paste_into(driver, title_input, title, "tiêu đề")
            log("ĐÃ ĐIỀN TIÊU ĐỀ")
        else:
            log("Trang không có ô tiêu đề, bỏ qua tiêu đề")

    description = request.get("description") or ""
    editor = find_visible(driver, By.CSS_SELECTOR, CAPTION_EDITOR_CSS)
    if editor is None:
        raise JobError("TIKTOK_UPLOAD_FAILED", "Không tìm thấy ô mô tả trên TikTok Studio")
    if description:
        paste_into(driver, editor, description, "mô tả")
        log(f"ĐÃ ĐIỀN MÔ TẢ ({len(description)} ký tự)")
    else:
        log("Mô tả rỗng, giữ nguyên ô mô tả")


def page_text(driver):
    try:
        return (driver.execute_script("return document.body.innerText") or "").lower()
    except Exception:
        return ""


def save_draft(driver):
    """Bấm LƯU NHÁP (không bao giờ bấm Đăng) rồi chờ TikTok báo đã lưu / rời trang soạn bài."""
    check_captcha(driver)
    button = find_button(driver, SAVE_DRAFT_TEXTS)
    if button is None or not button_ready(button):
        raise JobError("TIKTOK_DRAFT_FAILED", "Không tìm thấy nút Lưu nháp hoặc nút đang khoá")

    url_before = driver.current_url
    js_click(driver, button)
    log(f"ĐÃ CLICK: {' '.join(button.text.split())}")

    started = time.time()
    while time.time() - started < DRAFT_TIMEOUT:
        time.sleep(1)
        check_captcha(driver)
        # Lưu xong TikTok rời trang soạn bài (sang trang quản lý nội dung / trang tải lên mới) hoặc báo "draft saved"
        url = driver.current_url
        left_editor = url != url_before or find_visible(driver, By.CSS_SELECTOR, CAPTION_EDITOR_CSS) is None
        saved_toast = any(t in page_text(driver) for t in DRAFT_SAVED_TEXTS)
        if left_editor or saved_toast:
            log(f"ĐÃ LƯU NHÁP sau {int(time.time() - started)} giây ({url})")
            return
    raise JobError("TIKTOK_DRAFT_FAILED", f"Sau {DRAFT_TIMEOUT} giây TikTok chưa báo đã lưu nháp")


# ======================================
# ẢNH CỦA JOB
# ======================================

def download_images(job_dir, images):
    """Tải ảnh NGAY (link ký số ~15 phút) về thư mục của job, đặt tên 01, 02... đúng thứ tự."""
    if not images:
        raise JobError("IMAGE_DOWNLOAD_FAILED", "Job không có ảnh")
    paths = []
    for i, source in enumerate(images, start=1):
        path = os.path.join(job_dir, f"{i:02d}.jpg")
        try:
            if source.startswith(("http://", "https://")):
                response = requests.get(source, timeout=HTTP_TIMEOUT)
                response.raise_for_status()
                content = response.content
            else:
                with open(source, "rb") as f:  # Chế độ giả lập: ảnh trên máy
                    content = f.read()
            raw_path = path + ".src"
            with open(raw_path, "wb") as f:
                f.write(content)
            # Đổi mọi định dạng sang JPG để TikTok luôn nhận
            with Image.open(raw_path) as image:
                image.convert("RGB").save(path, "JPEG", quality=JPEG_QUALITY)
            os.remove(raw_path)
        except Exception as e:
            raise JobError("IMAGE_DOWNLOAD_FAILED", f"Không tải được ảnh {i}: {e}")
        paths.append(path)
    log(f"ĐÃ TẢI {len(paths)} ẢNH về {job_dir}")
    return paths


def screenshot(driver, job_id):
    if driver is None:
        return
    try:
        os.makedirs(ERROR_DIR, exist_ok=True)
        path = os.path.join(ERROR_DIR, f"job-{job_id}-{datetime.now():%Y%m%d-%H%M%S}.png")
        driver.save_screenshot(path)
        log(f"Đã chụp màn hình lỗi: {path}")
    except Exception as e:
        log(f"Không chụp được màn hình (bỏ qua): {e}")


# ======================================
# GIAO TIẾP VỚI SERVER A51
# ======================================

def api_session():
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {WORKER_TOKEN}", "Accept": "application/json"})
    return session


def claim_job(session):
    """
    Nhận 1 job. Trả về dict job, None khi chưa có việc (204), "NOT_ASSIGNED" khi worker chưa được gắn tài khoản A51.
    Phải trả kết quả trong 15 phút, quá hạn server cho thất bại (không giao lại).
    """
    response = session.get(f"{API_BASE_URL}/publish-jobs", timeout=HTTP_TIMEOUT)
    if response.status_code == 204:
        return None
    if response.status_code == 401:
        raise SystemExit("Token worker không hợp lệ hoặc worker đã bị tắt (401). Kiểm tra AI90_TIKTOK_WORKER_TOKEN.")
    if response.status_code == 409 and (response.json() or {}).get("code") == "TIKTOK_ACCOUNT_NOT_ASSIGNED":
        return "NOT_ASSIGNED"
    response.raise_for_status()
    return response.json()["data"]


def submit(session, job_id, body):
    """Gửi kết quả, lỗi mạng thì thử lại (cùng kết quả). 409 JOB_NOT_ASSIGNED: job đã quá hạn, chỉ ghi log."""
    for attempt in range(1, SUBMIT_RETRIES + 1):
        try:
            response = session.post(f"{API_BASE_URL}/publish-jobs/{job_id}", json=body, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            log(f"JOB #{job_id}: GỬI KẾT QUẢ LỖI MẠNG (lần {attempt}/{SUBMIT_RETRIES}): {e}")
            time.sleep(5 * attempt)
            continue
        if response.status_code == 200:
            log(f"JOB #{job_id}: SERVER ĐÃ NHẬN KẾT QUẢ ({response.json()['data']['status']})")
        elif response.status_code == 409:
            log(f"JOB #{job_id}: SERVER TỪ CHỐI (409, job đã quá hạn hoặc không còn của worker này): {response.text[:500]}")
        else:
            log(f"JOB #{job_id}: GỬI KẾT QUẢ LỖI HTTP {response.status_code}: {response.text[:500]}")
        return
    log(f"JOB #{job_id}: KHÔNG GỬI ĐƯỢC KẾT QUẢ sau {SUBMIT_RETRIES} lần")


def submit_completed(session, job_id):
    submit(session, job_id, {"status": "completed"})


def submit_failed(session, job_id, code, message):
    log(f"JOB #{job_id}: đang báo lỗi [{code}] lên server...")
    submit(session, job_id, {"status": "failed", "error_code": code, "error_message": str(message)[:490]})


# ======================================
# XỬ LÝ JOB
# ======================================

def run_job(driver, job, claimed_at):
    """Chạy toàn bộ thao tác cho 1 job: đến khi lưu nháp xong. Lỗi -> JobError."""
    request = job.get("request") or {}
    job_dir = os.path.join(WORK_DIR, str(job["id"]))
    shutil.rmtree(job_dir, ignore_errors=True)
    os.makedirs(job_dir, exist_ok=True)
    try:
        log("Bước 1: KIỂM TRA ĐĂNG NHẬP")
        # Chưa đăng nhập: chờ đăng nhập tay, nhưng không quá hạn job (chừa LOGIN_WAIT_RESERVE để đăng bài)
        wait_until_logged_in(driver, claimed_at + LEASE_SECONDS - LOGIN_WAIT_RESERVE)
        check_account(driver, request.get("account"))

        log("Bước 2: TẢI ẢNH")
        paths = download_images(job_dir, request.get("images") or [])

        if LOGO:
            log("Bước 3: CHÈN LOGO SHOP")
            for path in paths:
                apply_logo(path, LOGO)
            log(f"ĐÃ CHÈN LOGO vào {len(paths)} ảnh")
        else:
            log("Bước 3: không cấu hình logo, đăng ảnh gốc")

        log("Bước 4: TẢI ẢNH LÊN TIKTOK STUDIO")
        upload_images(driver, paths)

        log("Bước 5: ĐIỀN NỘI DUNG")
        fill_content(driver, request)

        # Gần hết 15 phút: server sắp cho thất bại, lưu nháp lúc này dễ tạo nháp trùng khi người dùng đăng lại
        remaining = LEASE_SECONDS - (time.time() - claimed_at)
        if remaining < LEASE_SAFETY:
            raise JobError("LEASE_EXPIRED", f"Chỉ còn {int(remaining)} giây trước khi job hết hạn, không lưu nháp")

        log("Bước 6: LƯU NHÁP")
        save_draft(driver)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def finish_after_draft(driver):
    """Lưu nháp xong: dừng CLOSE_AFTER_DRAFT_DELAY giây rồi tắt Chrome (job sau mở lại)."""
    time.sleep(CLOSE_AFTER_DRAFT_DELAY)
    close_chrome(driver)


def process_job(driver, session, job):
    """Xử lý 1 job. Trả về True nếu đã tắt Chrome (lưu nháp thành công) để lần sau mở lại."""
    job_id = job["id"]
    claimed_at = time.time()
    request = job.get("request") or {}
    log("=" * 60)
    log(f"NHẬN JOB #{job_id}: bài #{request.get('post_id')}, {len(request.get('images') or [])} ảnh")
    try:
        run_job(driver, job, claimed_at)
        # Lưu nháp xong: tắt Chrome ngay (không chờ gửi kết quả, gửi có thể chờ mạng lâu), rồi mới báo kết quả
        finish_after_draft(driver)
        log("Bước 7: BÁO KẾT QUẢ")
        submit_completed(session, job_id)
        return True
    except JobError as e:
        log(f"JOB #{job_id} LỖI [{e.code}]: {e}")
        screenshot(driver, job_id)
        submit_failed(session, job_id, e.code, e)
    except WebDriverException as e:
        log(f"JOB #{job_id} LỖI CHROME: {e}")
        screenshot(driver, job_id)
        submit_failed(session, job_id, "WORKER_ERROR", f"Lỗi Chrome: {e}")
    except Exception as e:
        log(f"JOB #{job_id} LỖI: {e}")
        screenshot(driver, job_id)
        submit_failed(session, job_id, "WORKER_ERROR", e)
    finally:
        log(f"JOB #{job_id} (bài #{request.get('post_id')}) KẾT THÚC sau {int(time.time() - claimed_at)} giây")
        log("=" * 60)
    # Job lỗi: giữ Chrome mở để xem lại
    return False


def run_mock_job():
    """Chưa gọi API A51: chạy 1 lần với job giả lập. VẪN lưu nháp thật lên tài khoản TikTok đang đăng nhập."""
    log("CHẾ ĐỘ GIẢ LẬP: không gọi API, dùng MOCK_JOB (bỏ AI90_USE_MOCK=1 để gọi API thật)")
    driver = None
    started = time.time()
    try:
        driver = start_chrome()
        run_job(driver, MOCK_JOB, started)
        log(f"HOÀN THÀNH sau {int(time.time() - started)} giây")
        finish_after_draft(driver)
        driver = None
    except KeyboardInterrupt:
        log("ĐÃ DỪNG (Ctrl+C).")
    except JobError as e:
        log(f"LỖI [{e.code}]: {e}")
        screenshot(driver, "mock")
    except Exception as e:
        log(f"LỖI: {e}")
        screenshot(driver, "mock")
    finally:
        if driver is not None:
            log("Lỗi: Chrome vẫn mở để xem lại, tắt thủ công khi xem xong.")


def main():
    global LOGO
    LOGO = load_logo_config()
    os.makedirs(WORK_DIR, exist_ok=True)

    if USE_MOCK:
        run_mock_job()
        return

    if not WORKER_TOKEN:
        raise SystemExit("Chưa đặt biến môi trường AI90_TIKTOK_WORKER_TOKEN (tạo bằng: php artisan workers:create {name}).")

    session = api_session()
    log(f"WORKER ĐĂNG TIKTOK ĐANG CHẠY, LẤY JOB TỪ {API_BASE_URL}/publish-jobs (Ctrl+C để dừng)")
    log("Chưa mở Chrome: chỉ mở khi có job, lưu nháp xong thì tắt (job lỗi thì giữ Chrome để xem lại)")

    driver = None
    idle_since = None
    next_idle_log = 0
    try:
        while True:
            try:
                job = claim_job(session)
            except requests.RequestException as e:
                log(f"KHÔNG GỌI ĐƯỢC API: {e}. Thử lại sau {API_ERROR_DELAY} giây...")
                time.sleep(API_ERROR_DELAY)
                continue

            if job == "NOT_ASSIGNED":
                log(f"Worker chưa được gắn tài khoản A51 hoặc đang bị tắt (admin -> Tài khoản TikTok). "
                    f"Hỏi lại sau {NOT_ASSIGNED_DELAY} giây...")
                time.sleep(NOT_ASSIGNED_DELAY)
                continue

            if job is None:
                if idle_since is None:
                    idle_since = time.time()
                    next_idle_log = idle_since + IDLE_LOG_INTERVAL
                    log(f"Chưa có job, kiểm tra lại mỗi {POLL_INTERVAL} giây...")
                elif time.time() >= next_idle_log:
                    log(f"Vẫn chưa có job (đã chờ {int(time.time() - idle_since) // 60} phút)")
                    next_idle_log += IDLE_LOG_INTERVAL
                time.sleep(POLL_INTERVAL)
                continue

            idle_since = None
            # Có job: dùng lại Chrome đang mở, chưa mở / đã bị tắt thủ công thì mở mới
            # (đăng nhập được kiểm tra ở bước 1 của job)
            if driver is None or not chrome_alive(driver):
                try:
                    driver = start_chrome()
                except Exception as e:
                    log(f"KHÔNG MỞ ĐƯỢC CHROME: {e}")
                    submit_failed(session, job["id"], "WORKER_ERROR", f"Không mở được Chrome: {e}")
                    driver = None
                    time.sleep(API_ERROR_DELAY)
                    continue
            if process_job(driver, session, job):
                driver = None
    except KeyboardInterrupt:
        # Job đang làm dở (nếu có): server cho thất bại khi quá 15 phút, bài về "Chờ gửi"
        log("ĐÃ DỪNG WORKER (Ctrl+C). Chrome vẫn mở, tắt thủ công khi cần.")


if __name__ == "__main__":
    main()
