"""
Worker tạo ảnh cho AI90.

Vòng lặp:
  1. GET  {API_BASE_URL}/jobs        -> nhận 1 job (1 ảnh): prompt (đã ghi sẵn tỉ lệ khung hình), link ảnh đầu vào.
  2. Tải ảnh đầu vào về job/img, chạy Google Flow bằng Selenium, tải ảnh 2K về job/output.
  3. POST {API_BASE_URL}/jobs/{id}   -> gửi ảnh kết quả (status=completed) hoặc báo lỗi (status=failed).

Cấu hình qua biến môi trường:
  AI90_API_BASE_URL   mặc định http://a90.local/api/v1
  AI90_WORKER_TOKEN   token tạo bằng: php artisan workers:create {name}   (bắt buộc)

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

import mimetypes
import pyperclip
import requests
import sys
import time
import random
import os
import uuid
from datetime import datetime

# Console Windows (cp1252) không in được tiếng Việt -> ép stdout/stderr sang UTF-8
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# ======================================
# CẤU HÌNH
# ======================================
API_BASE_URL = os.environ.get("AI90_API_BASE_URL", "https://admin.a51.vn/api/v1").rstrip("/")
WORKER_TOKEN = os.environ.get("AI90_WORKER_TOKEN", "VbCY6Joa2Yd8ru8119QIfLJFxLhdzEjiwUMx9gbD3lmuVXIwgHGdgOQWXkiocpRM")
POLL_INTERVAL = 5        # Giây nghỉ khi chưa có job
IDLE_LOG_INTERVAL = 60   # Khi chưa có job: cứ mỗi bấy nhiêu giây in 1 dòng "vẫn đang chờ"
API_ERROR_DELAY = 15     # Giây nghỉ khi không gọi được API
HTTP_TIMEOUT = 120       # Timeout mỗi request tới API / tải ảnh đầu vào

FLOW_URL = "https://flow.google.com/u/2/project/c53f1d47-14d7-47f1-8654-8f0dd599ae34"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE_DIR, "job", "img")        # Ảnh đầu vào tải từ server
OUTPUT_DIR = os.path.join(BASE_DIR, "job", "output")  # Nơi lưu ảnh 2K tải xuống
UPLOAD_TIMEOUT = 180    # Thời gian tối đa (giây) chờ ảnh tải lên xong
LOGIN_TIMEOUT = 600     # Thời gian tối đa (giây) chờ đăng nhập Google lần đầu
GENERATE_TIMEOUT = 600  # Thời gian tối đa (giây) chờ tạo ảnh xong
DOWNLOAD_TIMEOUT = 300  # Thời gian tối đa (giây) chờ tải ảnh 2K về máy

os.makedirs(IMG_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

chrome_options = Options()
chrome_options.add_argument("--start-maximized")

# Tạo Profile RIÊNG cho Selenium, KHÔNG dùng Default
# (Profile này cần đăng nhập sẵn tài khoản Google /u/2)
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


def click_by_xpath(driver, wait, xpath, label, retries=5):
    # Trang Angular hay render lại -> tìm lại phần tử ngay trước khi click, thử lại nếu bị stale
    wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
    human_delay()
    for attempt in range(retries):
        try:
            element = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            driver.execute_script("arguments[0].click();", element)
            log(f"ĐÃ CLICK: {label}")
            return
        except StaleElementReferenceException:
            log(f"Phần tử bị render lại, thử lại ({attempt + 1}/{retries})...")
            time.sleep(1)
    raise RuntimeError(f"Không click được {label}")


# Chặn hộp thoại chọn file của hệ điều hành (Selenium không điều khiển được),
# giữ lại thẻ <input type=file> để gửi đường dẫn ảnh trực tiếp vào đó.
INTERCEPT_FILE_DIALOG_JS = """
if (!window.__fileDialogPatched) {
    window.__fileDialogPatched = true;
    const origClick = HTMLInputElement.prototype.click;
    HTMLInputElement.prototype.click = function () {
        if (this.type === 'file') {
            if (!this.isConnected) {
                this.style.display = 'none';
                document.body.appendChild(this);
            }
            window.__lastFileInput = this;
            return;
        }
        return origClick.apply(this, arguments);
    };
}
window.__lastFileInput = null;
"""


def upload_images(driver, wait, img_list):
    """Bấm 'Tải nội dung nghe nhìn lên' rồi đưa ảnh vào input file."""
    driver.execute_script(INTERCEPT_FILE_DIALOG_JS)

    click_by_xpath(
        driver, wait,
        "//span[contains(@class, 'upload-text') and normalize-space()='Tải nội dung nghe nhìn lên']",
        "'Tải nội dung nghe nhìn lên'"
    )
    human_delay(1, 2)

    file_input = driver.execute_script("return window.__lastFileInput;")
    if file_input is None:
        inputs = driver.find_elements(By.XPATH, "//input[@type='file']")
        if not inputs:
            raise RuntimeError("Không tìm thấy ô input file để tải ảnh lên")
        file_input = inputs[-1]

    if file_input.get_attribute("multiple") is not None:
        file_input.send_keys("\n".join(img_list))
    else:
        for path in img_list:
            file_input.send_keys(path)
            human_delay(1, 2)

    log(f"ĐÃ GỬI {len(img_list)} ẢNH LÊN, ĐANG CHỜ TẢI XONG...")


def select_uploaded_images(driver, img_list):
    """Chờ ảnh tải xong rồi bấm chọn từng ảnh dựa vào tên file."""
    upload_wait = WebDriverWait(driver, UPLOAD_TIMEOUT)

    for path in img_list:
        name = os.path.basename(path)
        xpath = f"//span[contains(@class, 'asset-title') and normalize-space()='{name}']"

        log(f"Đang chờ ảnh {name} tải lên Flow (tối đa {UPLOAD_TIMEOUT} giây)...")
        upload_wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        log(f"Ảnh {name} đã tải lên xong")
        # Chờ thêm để ảnh load xong hẳn trước khi chọn
        human_delay(2, 4)
        click_by_xpath(driver, upload_wait, xpath, f"ảnh {name}")

        # Ảnh xem trước bên phải có thể không hiện -> chỉ chờ một chút rồi đi tiếp
        human_delay(3, 5)


def add_media_to_applet(driver, wait, retries=3):
    """Bấm 'Thêm nội dung nghe nhìn' và chờ popup đóng lại."""
    xpath = "//button[contains(@class, 'detail-add-to-prompt-btn')]"
    for attempt in range(retries):
        click_by_xpath(driver, wait, xpath, "'Thêm nội dung nghe nhìn'")
        try:
            WebDriverWait(driver, 15).until(
                lambda d: not [b for b in d.find_elements(By.XPATH, xpath) if b.is_displayed()]
            )
            log("POPUP ĐÃ ĐÓNG, ẢNH ĐÃ ĐƯỢC THÊM")
            return
        except TimeoutException:
            log(f"Popup chưa đóng, bấm lại ({attempt + 1}/{retries})...")
    raise RuntimeError("Bấm 'Thêm nội dung nghe nhìn' nhưng popup không đóng")


GENERATE_BUTTON_XPATH = "//button[contains(normalize-space(), 'TẠO ẢNH BỐI CẢNH') or contains(normalize-space(), 'Tạo ảnh bối cảnh')]"


def fill_prompt_and_generate(driver, prompt):
    """Điền prompt vào ô '02. Bối cảnh mong muốn' (trong iframe applet) rồi bấm tạo ảnh."""
    # Trang chính Flow cũng có textarea ẩn -> phải chỉ đúng ô của applet theo placeholder
    textarea_xpath = "//textarea[starts-with(@placeholder, 'Ví dụ: Đặt trên mặt bàn')]"
    switch_to_frame_with(driver, textarea_xpath)

    textarea = driver.find_element(By.XPATH, textarea_xpath)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", textarea)
    human_delay()
    # Phải click chuột thật vào ô trước rồi mới nhập, app mới nhận nội dung
    ActionChains(driver).move_to_element(textarea).click().perform()
    log("ĐÃ CLICK: ô '02. Bối cảnh mong muốn'")
    human_delay(1, 2)
    # Copy prompt vào clipboard rồi Ctrl+V vào ô
    pyperclip.copy(prompt)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
    human_delay(1, 2)
    if not (textarea.get_attribute("value") or "").strip():
        # Dự phòng nếu paste không vào
        driver.execute_cdp_cmd("Input.insertText", {"text": prompt})
        human_delay(1, 2)
    log(f"ĐÃ ĐIỀN PROMPT ({len(textarea.get_attribute('value') or '')} ký tự)")

    # Nút tạo ảnh chỉ bật khi đã có ảnh sản phẩm + prompt
    try:
        button = WebDriverWait(driver, 30).until(lambda d: next(
            (b for b in d.find_elements(By.XPATH, GENERATE_BUTTON_XPATH) if b.is_enabled()), False
        ))
    except TimeoutException:
        raise RuntimeError("Nút 'Tạo ảnh bối cảnh' vẫn bị khoá (chưa nhận ảnh sản phẩm hoặc prompt)")

    img_count = count_loaded_images(driver)
    human_delay()
    driver.execute_script("arguments[0].click();", button)
    log("ĐÃ CLICK: 'Tạo ảnh bối cảnh'")
    return img_count


def count_loaded_images(driver):
    return driver.execute_script(
        "return [...document.images].filter(i => i.complete && i.naturalWidth > 0).length;"
    )


def wait_for_generation(driver, img_count_before):
    """Chờ ảnh tạo xong: có ảnh mới hiện trong applet, hoặc nút tạo ảnh bị khoá rồi mở lại."""
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
            if count_loaded_images(driver) > img_count_before:
                break
            buttons = driver.find_elements(By.XPATH, GENERATE_BUTTON_XPATH)
            busy = not buttons or not buttons[0].is_enabled()
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


def run_flow(driver, wait, img_list, prompt):
    """Chạy toàn bộ thao tác trên Google Flow, trả về đường dẫn ảnh 2K đã tải về."""
    # Bước 1: Vào link project Flow
    log("Bước 1: MỞ LINK PROJECT FLOW")
    driver.switch_to.default_content()
    log(f"Đang mở {FLOW_URL}")
    driver.get(FLOW_URL)
    ensure_logged_in(driver)

    # Bước 2: Bấm 'Tạo ảnh từ câu lệnh'
    log("Bước 2: BẤM 'Tạo ảnh từ câu lệnh'")
    click_by_xpath(
        driver, wait,
        "//span[contains(@class, 'applet-title') and normalize-space()='Tạo ảnh từ câu lệnh']",
        "'Tạo ảnh từ câu lệnh'"
    )

    # Bước 3: Bấm 'Tải ảnh sản phẩm'
    log("Bước 3: BẤM 'Tải ảnh sản phẩm'")
    upload_xpath = "//p[normalize-space()='Tải ảnh sản phẩm']"
    switch_to_frame_with(driver, upload_xpath)
    click_by_xpath(driver, wait, upload_xpath, "'Tải ảnh sản phẩm'")
    # Popup chọn nội dung nghe nhìn nằm ở trang chính của Flow
    driver.switch_to.default_content()

    # Bước 4: Trong popup, tải ảnh đầu vào lên
    log("Bước 4: TẢI ẢNH LÊN")
    upload_images(driver, wait, img_list)

    # Bước 5: Chờ ảnh load xong và chọn ảnh theo tên
    log("Bước 5: CHỌN ẢNH VỪA TẢI LÊN")
    select_uploaded_images(driver, img_list)

    # Bước 6: Bấm 'Thêm nội dung nghe nhìn'
    log("Bước 6: BẤM 'Thêm nội dung nghe nhìn'")
    add_media_to_applet(driver, wait)

    # Bước 7: Điền prompt vào ô '02. Bối cảnh mong muốn' và bấm tạo ảnh
    log("Bước 7: ĐIỀN PROMPT VÀ BẤM TẠO ẢNH")
    img_count = fill_prompt_and_generate(driver, prompt)

    # Bước 8: Chờ ảnh tạo xong
    log("Bước 8: CHỜ TẠO ẢNH")
    wait_for_generation(driver, img_count)

    # Bước 9: Tab 'Hình ảnh' -> chuột phải ảnh đầu tiên -> Tải xuống -> 2K
    log("Bước 9: TẢI ẢNH ĐẦU TIÊN Ở ĐỘ PHÂN GIẢI 2K")
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
    """Nhận 1 job. Trả về dict job, hoặc None khi chưa có việc."""
    response = session.get(f"{API_BASE_URL}/jobs", timeout=HTTP_TIMEOUT)
    if response.status_code == 204:
        return None
    if response.status_code == 401:
        raise SystemExit("Token worker không hợp lệ hoặc worker đã bị tắt (401). Kiểm tra AI90_WORKER_TOKEN.")
    response.raise_for_status()
    return response.json()["data"]


def download_input_image(job):
    """Tải ảnh đầu vào từ link S3 tạm về job/img. Tên file duy nhất để chọn đúng ảnh trong thư viện Flow."""
    extension = mimetypes.guess_extension(job.get("input_mime") or "") or ".png"
    if extension == ".jpe":
        extension = ".jpg"
    path = os.path.join(IMG_DIR, f"ai90-job-{job['id']}-{uuid.uuid4().hex[:8]}{extension}")

    log("Đang tải ảnh đầu vào từ server...")
    response = requests.get(job["input_image_url"], timeout=HTTP_TIMEOUT)
    if response.status_code != 200:
        raise JobError("INPUT_DOWNLOAD_FAILED", f"Không tải được ảnh đầu vào (HTTP {response.status_code})")
    with open(path, "wb") as f:
        f.write(response.content)
    log(f"ĐÃ TẢI ẢNH ĐẦU VÀO: {path} ({len(response.content) // 1024} KB)")
    return path


def build_prompt(job):
    prompt = (job.get("prompt") or "").strip()
    if not prompt:
        raise JobError("EMPTY_PROMPT", "Job không có prompt")
    return prompt


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
        log(f"JOB #{job_id}: SERVER TỪ CHỐI (409) - job đã quá hạn và giao cho worker khác, hoặc đã có kết quả")
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
    log(f"NHẬN JOB #{job_id} (lần nhận {job.get('attempt')}, hạn trả kết quả {job.get('lease_expires_at')})")

    input_path = output_path = None
    try:
        prompt = build_prompt(job)
        log(f"Prompt ({len(prompt)} ký tự): {prompt[:200]}{'...' if len(prompt) > 200 else ''}")
        input_path = download_input_image(job)

        output_path = run_flow(driver, wait, [input_path], prompt)
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
        remove_files(input_path, output_path)
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


def main():
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
