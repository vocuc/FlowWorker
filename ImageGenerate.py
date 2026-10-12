"""
Worker tạo ảnh cho AI90.

Vòng lặp:
  1. GET  {API_BASE_URL}/jobs        -> nhận 1 job, trường "request" là JSON gửi cho tool Flow
                                        (productImage, outfitImage, prompt, aspectRatio, modelDisplayName).
  2. Mở tool trên Google Flow, dán JSON request vào ô nhập, bấm tạo ảnh (btn_image_generate),
     chờ tạo xong rồi tải ảnh 2K về job/output.
  3. POST {API_BASE_URL}/jobs/{id}   -> gửi ảnh kết quả (status=completed) hoặc báo lỗi (status=failed).

Chế độ giả lập (AI90_USE_MOCK=1): không gọi API, chạy 1 lần với MOCK_JOB, giữ ảnh trong job/output.

Cấu hình qua biến môi trường:
  AI90_USE_MOCK       mặc định 0 (gọi API thật). Đặt 1 để dùng job giả lập.
  AI90_API_BASE_URL   mặc định https://admin.a51.vn/api/v1
  AI90_WORKER_TOKEN   token tạo bằng: php artisan workers:create {name}   (bắt buộc khi gọi API thật)

Chạy: python ImageGenerate.py   (Ctrl+C để dừng)
"""

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

import json
import mimetypes
import pyperclip
import requests
import sys
import time
import random
import os
from datetime import datetime

# Console Windows (cp1252) không in được tiếng Việt -> ép stdout/stderr sang UTF-8
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# ======================================
# CẤU HÌNH
# ======================================
API_BASE_URL = os.environ.get("AI90_API_BASE_URL", "https://admin.a51.vn/api/v1").rstrip("/")
WORKER_TOKEN = os.environ.get("AI90_WORKER_TOKEN", "cA18Du4HphfWpcCUNbWrnkHbrTyucvcMybCyAAXtMiSAdHUoFbNhG79OUhYOBMkD")
POLL_INTERVAL = 5        # Giây nghỉ khi chưa có job
IDLE_LOG_INTERVAL = 60   # Khi chưa có job: cứ mỗi bấy nhiêu giây in 1 dòng "vẫn đang chờ"
API_ERROR_DELAY = 15     # Giây nghỉ khi không gọi được API
HTTP_TIMEOUT = 120       # Timeout mỗi request tới API / tải ảnh đầu vào

USE_MOCK = os.environ.get("AI90_USE_MOCK", "0") == "1"

# Dữ liệu giả lập thay cho "data" của GET /jobs (dùng khi AI90_USE_MOCK=1).
# Server trả {"data": {"id": <mã job>, "request": {...}}}; trong request mỗi ảnh đầu vào là một khoá
# camelCase theo trường của creative (productImage, outfitImage, ...), kèm prompt, aspectRatio, modelDisplayName.
MOCK_JOB = {
    "id": 0,
    "request": {
        "productImage": "https://topmcn.s3.ap-southeast-1.amazonaws.com/creatives/17/376bd6ea-d47a-435d-8635-3925cc58f718.png",
        "outfitImage": "https://topmcn.s3.ap-southeast-1.amazonaws.com/creatives/18/0a38d326-0fe9-440d-93dd-6ebc4f997efb.jpg",
        "prompt": "Tạo một người mẫu mặc outfitImage và đi đôi giày của tôi ở productImage",
        "aspectRatio": "16:9",
        "modelDisplayName": "🍌 Nano Banana Pro",
    },
}

FLOW_URL = os.environ.get(
    "AI90_FLOW_URL",
    "https://flow.google.com/u/0/project/c53f1d47-14d7-47f1-8654-8f0dd599ae34/tool/5267a6b1-43f5-44bd-9795-52b22d0ab4fa",
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "job", "output")  # Nơi lưu ảnh 2K tải xuống
LOGIN_TIMEOUT = 600     # Thời gian tối đa (giây) chờ đăng nhập Google lần đầu
GENERATE_TIMEOUT = 600  # Thời gian tối đa (giây) chờ tạo ảnh xong
DOWNLOAD_TIMEOUT = 300  # Thời gian tối đa (giây) chờ tải ảnh 2K về máy

os.makedirs(OUTPUT_DIR, exist_ok=True)

chrome_options = Options()
chrome_options.add_argument("--start-maximized")

# Tạo Profile RIÊNG cho Selenium, KHÔNG dùng Default
# (Profile này cần đăng nhập sẵn tài khoản Google /u/0)
chrome_options.add_argument(r"user-data-dir=C:\selenium-profile")
chrome_options.add_argument("profile-directory=Profile1")

# Fix DevToolsActivePort
chrome_options.add_argument("--remote-debugging-port=9222")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")

# Tránh bị nhận dạng bot
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option("useAutomationExtension", False)
chrome_options.add_argument("--disable-blink-features=AutomationControlled")

# Ảnh tải xuống lưu thẳng vào job/output, không hỏi vị trí lưu
chrome_options.add_experimental_option("prefs", {
    "download.default_directory": OUTPUT_DIR,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
})


def log(message=""):
    """In ra console kèm giờ, flush ngay để thấy log theo thời gian thực."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


class JobError(RuntimeError):
    """Lỗi khi xử lý job, kèm mã lỗi gửi về server (chữ hoa, số, '_')."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def human_delay(a=1, b=3):
    time.sleep(random.uniform(a, b))


def js_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    human_delay()
    driver.execute_script("arguments[0].click();", element)


REQUEST_TEXTAREA_XPATH = "//textarea[@id='prompt_id']"
START_BUTTON_XPATH = "//button[@id='btn_image_generate']"


def fill_request(driver, request_text):
    """Click vào ô nhập JSON của tool (trong iframe) rồi dán nội dung request vào."""
    switch_to_frame_with(driver, REQUEST_TEXTAREA_XPATH)

    textarea = driver.find_element(By.XPATH, REQUEST_TEXTAREA_XPATH)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", textarea)
    human_delay()
    # Phải click chuột thật vào ô trước rồi mới nhập, app mới nhận nội dung
    ActionChains(driver).move_to_element(textarea).click().perform()
    log("ĐÃ CLICK: ô nhập request")
    human_delay(1, 2)
    # Xoá nội dung cũ (nếu có) rồi copy request vào clipboard và Ctrl+V vào ô
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).perform()
    pyperclip.copy(request_text)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
    human_delay(1, 2)
    if (textarea.get_attribute("value") or "").strip() != request_text.strip():
        # Dự phòng nếu paste không vào
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).perform()
        driver.execute_cdp_cmd("Input.insertText", {"text": request_text})
        human_delay(1, 2)
    log(f"ĐÃ ĐIỀN REQUEST ({len(textarea.get_attribute('value') or '')} ký tự)")


def button_ready(button):
    """Nút sáng = đang hiện, không bị disabled / aria-disabled."""
    return (button.is_displayed() and button.is_enabled()
            and (button.get_attribute("aria-disabled") or "").lower() != "true")


def click_start(driver, request):
    """Chờ nút tạo ảnh (btn_image_generate) sáng lên rồi bấm. Trả về số ảnh đang có để biết khi nào có ảnh mới."""
    try:
        button = WebDriverWait(driver, 60).until(lambda d: next(
            (b for b in d.find_elements(By.XPATH, START_BUTTON_XPATH) if button_ready(b)), False
        ))
    except TimeoutException:
        raise RuntimeError("Nút tạo ảnh (btn_image_generate) không sáng lên (tool chưa nhận request)")

    human_delay()
    img_count = count_result_images(driver, request)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    driver.execute_script("arguments[0].click();", button)
    log("ĐÃ CLICK: tạo ảnh (btn_image_generate)")
    return img_count


def count_result_images(driver, request):
    """Đếm ảnh đã load trong tool, bỏ qua ảnh đầu vào (productImage, outfitImage) hiện ở phần xem trước."""
    input_urls = [v for v in request.values() if isinstance(v, str) and v.startswith("http")]
    return driver.execute_script(
        "return [...document.images].filter(i => i.complete && i.naturalWidth > 0"
        " && !arguments[0].includes(i.currentSrc || i.src)).length;",
        input_urls,
    )


def wait_for_generation(driver, request, img_count_before):
    """Chờ ảnh tạo xong: có ảnh mới hiện trong tool, hoặc nút tạo ảnh (btn_image_generate) ẩn/khoá rồi hiện lại."""
    log(f"ĐANG CHỜ TẠO ẢNH (tối đa {GENERATE_TIMEOUT // 60} phút)...")
    started = time.time()
    deadline = started + GENERATE_TIMEOUT
    next_progress_log = started + 30
    was_busy = False
    while time.time() < deadline:
        time.sleep(3)
        if time.time() >= next_progress_log:
            log(f"Vẫn đang tạo ảnh... ({int(time.time() - started)} giây)")
            next_progress_log += 30
        try:
            if count_result_images(driver, request) > img_count_before:
                break
            buttons = [b for b in driver.find_elements(By.XPATH, START_BUTTON_XPATH) if b.is_displayed()]
            busy = not buttons or not button_ready(buttons[0])
            if busy:
                was_busy = True
            elif was_busy:
                break
        except StaleElementReferenceException:
            continue
    else:
        raise JobError("GENERATION_TIMEOUT", "Hết thời gian chờ Flow tạo ảnh")

    # Chờ thêm để Flow lưu ảnh vào thư viện
    time.sleep(5)
    driver.switch_to.default_content()
    log(f"ẢNH ĐÃ TẠO XONG sau {int(time.time() - started)} giây")


def download_first_image_2k(driver, wait):
    """Vào tab 'Hình ảnh', chuột phải ảnh đầu tiên -> Tải xuống -> 2K, chờ file tải về job/output."""
    driver.switch_to.default_content()

    tab_xpath = "//span[contains(@class, 'mat-mdc-list-item-title') and normalize-space()='Hình ảnh']"
    tab = wait.until(lambda d: next((t for t in d.find_elements(By.XPATH, tab_xpath) if t.is_displayed()), False))
    js_click(driver, tab)
    log("ĐÃ CLICK: tab 'Hình ảnh'")

    first_img = wait.until(lambda d: next(
        (i for i in d.find_elements(By.CSS_SELECTOR, "img.image") if i.is_displayed()), False
    ))
    time.sleep(3)  # Chờ lưới ảnh ổn định
    first_img = next(i for i in driver.find_elements(By.CSS_SELECTOR, "img.image") if i.is_displayed())

    files_before = set(os.listdir(OUTPUT_DIR))

    ActionChains(driver).move_to_element(first_img).context_click(first_img).perform()
    log("ĐÃ CHUỘT PHẢI: ảnh đầu tiên")
    human_delay(1, 2)

    download_item = wait.until(EC.element_to_be_clickable((By.XPATH,
        "//button[@role='menuitem' and .//span[contains(@class, 'label') and normalize-space()='Tải xuống']]"
    )))
    if "submenu-trigger" not in (download_item.get_attribute("class") or ""):
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        raise JobError("NO_IMAGE_RETURNED", "Ảnh đầu tiên không có tuỳ chọn độ phân giải (không phải ảnh do AI tạo)")
    # Click chuột thật để mở menu con độ phân giải
    ActionChains(driver).move_to_element(download_item).click().perform()
    log("ĐÃ CLICK: 'Tải xuống'")

    item_2k = wait.until(EC.element_to_be_clickable((By.XPATH,
        "//button[@role='menuitem' and .//span[contains(@class, 'label') and normalize-space()='2K']]"
    )))
    human_delay(1, 2)
    ActionChains(driver).move_to_element(item_2k).click().perform()
    log("ĐÃ CLICK: '2K'")

    log(f"ĐANG CHỜ TẢI ẢNH 2K VỀ {OUTPUT_DIR} ...")
    deadline = time.time() + DOWNLOAD_TIMEOUT
    while time.time() < deadline:
        time.sleep(2)
        new_files = [
            f for f in set(os.listdir(OUTPUT_DIR)) - files_before
            if not f.endswith((".crdownload", ".tmp"))
        ]
        if new_files:
            path = os.path.join(OUTPUT_DIR, new_files[0])
            log(f"ĐÃ TẢI XONG: {path}")
            return path
    raise JobError("DOWNLOAD_TIMEOUT", "Hết thời gian chờ tải ảnh 2K")


def switch_to_frame_with(driver, xpath, timeout=30):
    """Applet chạy trong iframe -> tìm iframe chứa phần tử xpath rồi chuyển vào đó."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        driver.switch_to.default_content()
        if driver.find_elements(By.XPATH, xpath):
            return
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.frame(frame)
                if driver.find_elements(By.XPATH, xpath):
                    return
            except Exception:
                pass
            driver.switch_to.default_content()
        time.sleep(1)
    raise RuntimeError(f"Không tìm thấy iframe chứa phần tử: {xpath}")


def ensure_logged_in(driver):
    """
    Nếu profile chưa đăng nhập Google, Flow sẽ chuyển về trang /about hoặc trang đăng nhập.
    Khi đó chờ người dùng đăng nhập tay trên cửa sổ Chrome rồi mở lại link project.
    """
    time.sleep(5)
    if "/project/" in driver.current_url:
        log("Đã đăng nhập Google, đang ở trang project Flow")
        return

    log("CHƯA ĐĂNG NHẬP GOOGLE! Hãy đăng nhập trên cửa sổ Chrome vừa mở "
          f"(chờ tối đa {LOGIN_TIMEOUT // 60} phút)...")
    deadline = time.time() + LOGIN_TIMEOUT
    while time.time() < deadline:
        time.sleep(10)
        url = driver.current_url
        if "accounts.google.com" in url:
            continue  # Đang ở trang đăng nhập
        driver.get(FLOW_URL)
        time.sleep(5)
        if "/project/" in driver.current_url:
            log("ĐÃ ĐĂNG NHẬP, TIẾP TỤC...")
            return

    raise JobError("FLOW_LOGIN_REQUIRED", "Hết thời gian chờ đăng nhập Google")


def normalize_value(value):
    """Chuẩn hoá 1 giá trị trong request: link bỏ phần từ dấu '?', chuỗi bỏ ký tự xuống dòng."""
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    if not isinstance(value, str):
        return value
    value = value.strip()
    if value.startswith(("http://", "https://")):
        # Link S3 (có chữ ký tạm thời) -> chỉ giữ phần trước dấu '?'
        return value.split("?", 1)[0]
    # Bỏ ký tự xuống dòng thật và chuỗi "\n" dạng chữ, gộp khoảng trắng thừa
    value = value.replace("\\r\\n", " ").replace("\\n", " ").replace("\\r", " ")
    return " ".join(value.split())


def normalize_request(request):
    """Chuẩn hoá request rồi trả về (request đã chuẩn hoá, JSON 1 dòng để dán vào tool)."""
    request = normalize_value(request)
    return request, json.dumps(request, ensure_ascii=False, separators=(",", ":"))


def run_flow(driver, wait, request):
    """Chạy toàn bộ thao tác trên Google Flow, trả về đường dẫn ảnh 2K đã tải về."""
    request, request_text = normalize_request(request)
    log(f"Request đã chuẩn hoá: {request_text[:500]}")

    # Bước 1: Mở link tool trên Flow
    log("Bước 1: MỞ LINK TOOL FLOW")
    driver.switch_to.default_content()
    log(f"Đang mở {FLOW_URL}")
    driver.get(FLOW_URL)
    ensure_logged_in(driver)

    # Bước 2 + 4: Click vào ô nhập và điền nội dung request của job
    log("Bước 2: CLICK Ô NHẬP VÀ ĐIỀN REQUEST")
    fill_request(driver, request_text)

    # Bước 5: Chờ nút tạo ảnh (btn_image_generate) sáng lên và bấm để tạo ảnh
    log("Bước 3: BẤM tạo ảnh (btn_image_generate)")
    img_count = click_start(driver, request)

    # Chờ Flow tạo ảnh xong
    log("Bước 4: CHỜ TẠO ẢNH")
    wait_for_generation(driver, request, img_count)

    # Về tab 'Hình ảnh' -> chuột phải ảnh đầu tiên -> Tải xuống -> 2K
    log("Bước 5: TẢI ẢNH ĐẦU TIÊN Ở ĐỘ PHÂN GIẢI 2K")
    return download_first_image_2k(driver, wait)


# ======================================
# GIAO TIẾP VỚI SERVER AI90
# ======================================

def api_session():
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {WORKER_TOKEN}",
        "Accept": "application/json",
    })
    return session


def claim_job(session):
    """
    Nhận 1 job. Trả về dict job {"id": ..., "request": {...}}, hoặc None khi chưa có việc (204).
    Phải trả kết quả trong lease_minutes của server (mặc định 15 phút), quá hạn job giao cho worker khác.
    """
    response = session.get(f"{API_BASE_URL}/jobs", timeout=HTTP_TIMEOUT)
    if response.status_code == 204:
        return None
    if response.status_code == 401:
        raise SystemExit("Token worker không hợp lệ hoặc worker đã bị tắt (401). Kiểm tra AI90_WORKER_TOKEN.")
    response.raise_for_status()
    return response.json()["data"]


def get_request(job):
    """Lấy trường 'request' của job để dán vào tool Flow."""
    request = job.get("request")
    if not isinstance(request, dict) or not str(request.get("prompt") or "").strip():
        raise JobError("EMPTY_PROMPT", "Job không có request hoặc request không có prompt")
    return request


def submit_completed(session, job_id, image_path):
    mime = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
    log(f"JOB #{job_id}: đang gửi ảnh kết quả lên server ({os.path.getsize(image_path) // 1024} KB, {mime})...")
    with open(image_path, "rb") as f:
        response = session.post(
            f"{API_BASE_URL}/jobs/{job_id}",
            data={"status": "completed"},
            files={"image": (os.path.basename(image_path), f, mime)},
            timeout=HTTP_TIMEOUT,
        )
    return response


def submit_failed(session, job_id, code, message):
    log(f"JOB #{job_id}: đang báo lỗi [{code}] lên server...")
    return session.post(
        f"{API_BASE_URL}/jobs/{job_id}",
        data={"status": "failed", "error_code": code, "error_message": str(message)[:490]},
        timeout=HTTP_TIMEOUT,
    )


def log_submit_result(job_id, response):
    if response.status_code == 200:
        log(f"JOB #{job_id}: SERVER ĐÃ NHẬN KẾT QUẢ ({response.json()['data']['status']})")
    elif response.status_code == 409:
        # JOB_NOT_ASSIGNED: job đã quá hạn và giao cho worker khác, hoặc đã có kết quả
        log(f"JOB #{job_id}: SERVER TỪ CHỐI (409): {response.text[:500]}")
    else:
        log(f"JOB #{job_id}: GỬI KẾT QUẢ LỖI HTTP {response.status_code}: {response.text[:500]}")


def remove_files(*paths):
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                log(f"Đã xoá file tạm: {path}")
            except OSError as e:
                log(f"Không xoá được file tạm {path}: {e}")


def process_job(driver, wait, session, job):
    job_id = job["id"]
    started = time.time()
    log("=" * 60)
    log(f"NHẬN JOB #{job_id}")

    output_path = None
    try:
        request = get_request(job)
        log(f"Request: {json.dumps(request, ensure_ascii=False)[:500]}")

        output_path = run_flow(driver, wait, request)
        log_submit_result(job_id, submit_completed(session, job_id, output_path))
    except JobError as e:
        log(f"JOB #{job_id} LỖI [{e.code}]: {e}")
        log_submit_result(job_id, submit_failed(session, job_id, e.code, e))
    except Exception as e:
        log(f"JOB #{job_id} LỖI: {e}")
        log_submit_result(job_id, submit_failed(session, job_id, "WORKER_ERROR", e))
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        remove_files(output_path)
        log(f"JOB #{job_id} KẾT THÚC sau {int(time.time() - started)} giây")
        log("=" * 60)


def start_chrome():
    log("Đang mở Chrome...")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )
    # Profile có sẵn cài đặt tải xuống riêng nên prefs bị bỏ qua -> ép thư mục tải qua CDP
    driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": OUTPUT_DIR,
        "eventsEnabled": True,
    })
    log("Chrome đã mở")
    return driver, WebDriverWait(driver, 30)


def close_chrome(driver):
    if driver is None:
        return
    log("Đang đóng Chrome...")
    try:
        driver.quit()
        log("Chrome đã đóng")
    except Exception as e:
        log(f"Lỗi khi đóng Chrome (bỏ qua): {e}")


def chrome_alive(driver):
    """Chrome có thể bị người dùng tắt hoặc crash giữa chừng."""
    try:
        driver.current_url
        return True
    except Exception:
        return False


def run_mock_job():
    """Chưa gọi API A51: chạy 1 lần với job giả lập, giữ ảnh 2K trong job/output."""
    log("CHẾ ĐỘ GIẢ LẬP: không gọi API, dùng MOCK_JOB (bỏ AI90_USE_MOCK=1 để gọi API thật)")
    driver = None
    started = time.time()
    try:
        driver, wait = start_chrome()
        request = get_request(MOCK_JOB)
        log(f"Request: {json.dumps(request, ensure_ascii=False)}")
        output_path = run_flow(driver, wait, request)
        log(f"HOÀN THÀNH sau {int(time.time() - started)} giây, ảnh lưu tại: {output_path}")
    except KeyboardInterrupt:
        log("ĐÃ DỪNG (Ctrl+C).")
    except Exception as e:
        log(f"LỖI: {e}")
    finally:
        close_chrome(driver)


def main():
    if USE_MOCK:
        run_mock_job()
        return

    if not WORKER_TOKEN:
        raise SystemExit("Chưa đặt biến môi trường AI90_WORKER_TOKEN (tạo bằng: php artisan workers:create {name}).")

    session = api_session()
    log(f"WORKER ĐANG CHẠY, LẤY JOB TỪ {API_BASE_URL}/jobs (Ctrl+C để dừng)")
    log(f"Chưa mở Chrome: chỉ mở khi có job, đóng lại khi hết job")

    driver = wait = None
    idle_since = None       # Thời điểm bắt đầu không có job
    next_idle_log = 0

    try:
        while True:
            try:
                job = claim_job(session)
            except requests.RequestException as e:
                log(f"KHÔNG GỌI ĐƯỢC API: {e}. Thử lại sau {API_ERROR_DELAY} giây...")
                time.sleep(API_ERROR_DELAY)
                continue

            if job is None:
                if idle_since is None:
                    idle_since = time.time()
                    next_idle_log = idle_since + IDLE_LOG_INTERVAL
                    log(f"Chưa có job, kiểm tra lại mỗi {POLL_INTERVAL} giây...")
                    # Hết job -> đóng Chrome, lần sau có job sẽ mở lại
                    close_chrome(driver)
                    driver = wait = None
                elif time.time() >= next_idle_log:
                    log(f"Vẫn chưa có job (đã chờ {int(time.time() - idle_since) // 60} phút)")
                    next_idle_log += IDLE_LOG_INTERVAL
                time.sleep(POLL_INTERVAL)
                continue

            idle_since = None
            if driver is None or not chrome_alive(driver):
                try:
                    driver, wait = start_chrome()
                except Exception as e:
                    log(f"KHÔNG MỞ ĐƯỢC CHROME: {e}")
                    log_submit_result(job["id"], submit_failed(session, job["id"], "WORKER_ERROR", f"Không mở được Chrome: {e}"))
                    driver = wait = None
                    time.sleep(API_ERROR_DELAY)
                    continue

            try:
                process_job(driver, wait, session, job)
            except requests.RequestException as e:
                # Không gửi được kết quả: server sẽ giao lại job khi hết hạn
                log(f"JOB #{job['id']}: KHÔNG GỬI ĐƯỢC KẾT QUẢ: {e}")
    except KeyboardInterrupt:
        # Job đang làm dở (nếu có) sẽ được server giao lại khi hết hạn
        log("ĐÃ DỪNG WORKER (Ctrl+C).")
    finally:
        close_chrome(driver)


if __name__ == "__main__":
    main()
