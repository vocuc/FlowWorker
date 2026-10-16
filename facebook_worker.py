from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys
from datetime import datetime

import pyperclip
import time
import random
import os
import uuid
import requests

chrome_options = Options()
chrome_options.add_argument("--start-maximized")

# Tạo Profile RIÊNG cho Selenium, KHÔNG dùng Default
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

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=chrome_options
)

wait = WebDriverWait(driver, 30)

# ======================================
# CẤU HÌNH API
# ======================================
API_URL = "https://batman.topmcn.vn/api/v1/jobs/230948204209480294824802984"
TMP_DIR = r"D:\san-pham\tmp_download"
AUTO_POST = 1
MIN_TIME = 15
MAX_TIME = 20

os.makedirs(TMP_DIR, exist_ok=True)

def get_job():
    """
    Gọi API lấy job cần đăng.
    Trả về dict job (hoặc None nếu không có job nào), ví dụ:
    {
        "type": 1,
        "url": "https://www.facebook.com/phiphiphiii",
        "images": [...],
        "content": "..."
    }
    """
    try:
        resp = requests.get(API_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print("LỖI KHI GỌI API LẤY JOB:", e)
        return None

    # API có thể trả về 1 object hoặc 1 list job / {"data": [...]}
    if isinstance(data, list):
        if not data:
            return None
        return data[0]

    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            if not data["data"]:
                return None
            return data["data"][0]
        if "data" in data and isinstance(data["data"], dict):
            return data["data"]
        return data

    return None


def download_images(image_urls, tmp_dir):
    """
    Tải ảnh từ danh sách URL về thư mục tạm.
    Trả về list đường dẫn ảnh local đã tải thành công.
    """
    local_paths = []

    for url in image_urls:
        try:
            ext = os.path.splitext(url)[1]
            if not ext or len(ext) > 5:
                ext = ".jpg"

            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = os.path.join(tmp_dir, filename)

            resp = requests.get(url, timeout=30)
            resp.raise_for_status()

            with open(filepath, "wb") as f:
                f.write(resp.content)

            local_paths.append(filepath)
            print(f"Đã tải ảnh: {url} -> {filepath}")

        except Exception as e:
            print(f"LỖI KHI TẢI ẢNH {url}:", e)

    return local_paths


def cleanup_images(local_paths):
    """Xóa các ảnh tạm đã tải về sau khi đăng xong."""
    for path in local_paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"Đã xóa ảnh tạm: {path}")
        except Exception as e:
            print(f"LỖI KHI XÓA ẢNH {path}:", e)


def post_to_group(driver, wait, text, img_list):
    # Bước 1: CLICK VÀO NUT ĐĂNG BÀI
    print("Bước 1: CLICK VÀO NUT ĐĂNG BÀI")

    try:
        span = wait.until(
            EC.presence_of_element_located((By.XPATH,
                "//span["
                "contains(@class, 'x1lliihq') and "
                "contains(@class, 'x6ikm8r') and "
                "contains(@class, 'x10wlt62') and "
                "contains(@class, 'x1n2onr6') and "
                "normalize-space()='Bạn viết gì đi...'"
                "]"
            ))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", span)

        time.sleep(random.uniform(1, 3))

        driver.execute_script("arguments[0].click();", span)

        print("ĐÃ CLICK THÀNH CÔNG VÀO <span> 'Bạn viết gì đi...' → MỞ POPUP!")

    except Exception as e:
        print("LỖI KHI CLICK NUT ĐĂNG BÀI:", e)

    # BƯỚC 2: Nhập nội dung vào ô input
    print("Bước 2: NHẬP NỘI DUNG BÀI VIẾT")

    try:
        textbox = wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//div[@contenteditable='true' and contains(@aria-placeholder, 'Tạo bài viết công khai')]"
            ))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", textbox)

        time.sleep(random.uniform(1, 3))

        driver.execute_script("arguments[0].click();", textbox)

        pyperclip.copy(text)

        textbox.click()
        textbox.send_keys(Keys.CONTROL, "v")

        print("Đã nhập nội dung!")

    except Exception as e:
        print("LỖI KHI NHẬP NỘI DUNG BÀI VIẾT:", e)

    # BƯỚC 3: UPLOAD ẢNH
    print("Bước 3: upload ảnh")
    try:
        file_input = wait.until(
            EC.presence_of_element_located(
                (By.XPATH,
                 "//input[@type='file' and @multiple and contains(@accept, 'image/')]"
                 )
            )
        )

        file_input.send_keys("\n".join(img_list))

        print("ĐÃ UPLOAD ẢNH THÀNH CÔNG!")

        time.sleep(random.uniform(1, 3))

    except Exception as e:
        print("LỖI KHI UPLOAD ẢNH:", e)

    # BƯỚC 4: BẤM NÚT ĐĂNG BÀI VIẾT
    print("BƯỚC 4: BẤM NÚT ĐĂNG BÀI VIẾT")
    try:
        post_button = wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//div[@aria-label='Đăng' and @role='button']"
            ))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", post_button)

        time.sleep(random.uniform(1, 3))

        if AUTO_POST == 1:
            driver.execute_script("arguments[0].click();", post_button)

        print("ĐÃ CLICK NÚT ĐĂNG THÀNH CÔNG!")

    except Exception as e:
        print("LỖI KHI CLICK NÚT ĐĂNG:", e)


def post_to_profile(driver, wait, text, img_list):
    # Bước 1: CLICK VÀO NUT ĐĂNG BÀI
    print("Bước 1: CLICK VÀO NUT ĐĂNG BÀI")

    try:
        span = wait.until(
            EC.presence_of_element_located((By.XPATH,
                "//span["
                "contains(@class, 'x1lliihq') and "
                "contains(@class, 'x6ikm8r') and "
                "contains(@class, 'x10wlt62') and "
                "contains(@class, 'x1n2onr6') and "
                "normalize-space()='Bạn đang nghĩ gì?'"
                "]"
            ))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", span)

        time.sleep(random.uniform(1, 3))

        driver.execute_script("arguments[0].click();", span)

        print("ĐÃ CLICK THÀNH CÔNG VÀO <span> 'Bạn đang nghĩ gì?' → MỞ POPUP!")

    except Exception as e:
        print("LỖI KHI CLICK NUT ĐĂNG BÀI:", e)

    # BƯỚC 2: Nhập nội dung vào ô input
    print("Bước 2: NHẬP NỘI DUNG BÀI VIẾT")

    try:
        textbox = wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//div[@contenteditable='true' and contains(@aria-placeholder, 'Bạn đang nghĩ gì?')]"
            ))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", textbox)

        time.sleep(random.uniform(1, 3))

        driver.execute_script("arguments[0].click();", textbox)

        pyperclip.copy(text)

        textbox.click()

        textbox.send_keys(Keys.CONTROL, "v")

        print("Đã nhập nội dung!")

    except Exception as e:
        print("LỖI KHI NHẬP NỘI DUNG BÀI VIẾT:", e)

    # BƯỚC 3: UPLOAD ẢNH
    print("Bước 3: upload ảnh")
    try:
        file_input = wait.until(
            EC.presence_of_element_located((By.XPATH,
                "(//input[@type='file' and @multiple and contains(@accept, 'image/')])[2]"
            ))
        )

        file_input.send_keys("\n".join(img_list))

        print("ĐÃ UPLOAD ẢNH THÀNH CÔNG!")
    except Exception as e:
        print("LỖI KHI UPLOAD ẢNH:", e)

    # BƯỚC 4: BẤM NÚT ĐĂNG BÀI VIẾT
    print("BƯỚC 4: BẤM NÚT ĐĂNG BÀI VIẾT")
    try:
        post_button = wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//div[@aria-label='Đăng' and @role='button']"
            ))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", post_button)

        time.sleep(random.uniform(1, 3))

        if AUTO_POST == 1:
            driver.execute_script("arguments[0].click();", post_button)

        print("ĐÃ CLICK NÚT ĐĂNG THÀNH CÔNG!")

    except Exception as e:
        print("LỖI KHI CLICK NÚT ĐĂNG:", e)


while True:
    print("\n===== BẮT ĐẦU 1 LẦN ĐĂNG =====")
    print("Thời gian hiện tại:", datetime.now())

    job = get_job()

    if not job:
        print("Không có job nào từ API, chờ rồi thử lại...")
        time.sleep(60)
        continue

    post_url = job.get("url")
    content = job.get("content", "")
    image_urls = job.get("images", [])

    if not post_url:
        print("Job không có 'url', bỏ qua job này.")
        time.sleep(30)
        continue

    # Tải ảnh về thư mục tạm
    local_images = download_images(image_urls, TMP_DIR)

    if not local_images and image_urls:
        print("Không tải được ảnh nào, bỏ qua job này.")
        time.sleep(30)
        continue

    # Tải link đã lấy được từ job
    driver.get(post_url)

    if "facebook.com/groups/" in post_url:
        post_to_group(driver, wait, content, local_images)
    else:
        post_to_profile(driver, wait, content, local_images)

    # ======================================
    # CHỜ RANDOM THỜI GIAN ĐỂ LÀM TIẾP
    # ======================================
    wait_minutes = random.randint(MIN_TIME, MAX_TIME)
    wait_seconds = wait_minutes * 60

    print(f"⏳ Chờ {wait_seconds} giây rồi đăng tiếp...")

    time.sleep(wait_seconds)