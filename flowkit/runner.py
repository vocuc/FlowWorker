"""
Vòng lặp worker đăng bài, dùng chung mọi nền tảng (phần riêng ở flowkit.publishers):
  1. GET /publish-jobs -> 1 job của nền tảng worker giữ tài khoản. Chưa có job thì KHÔNG mở Chrome.
  2. Có job: mở Chrome (profile riêng), chưa đăng nhập thì chờ đăng nhập tay (tối đa 10 phút), kiểm tra đúng tài khoản,
     tải ảnh, chèn logo, sửa ảnh theo nền tảng, đăng bài.
  3. POST /publish-jobs/{id} -> completed (+ post_url) hoặc failed (error_code, error_message).
Đăng xong thì tắt Chrome; job lỗi thì giữ Chrome để xem lại. Job lỗi / quá hạn không được giao lại.
"""

import os
import shutil
import time

import requests
from selenium.common.exceptions import WebDriverException

from . import browser
from .a51 import NOT_ASSIGNED, A51Client
from .config import (
    API_BASE_URL, API_ERROR_DELAY, BASE_DIR, CLOSE_AFTER_SUCCESS_DELAY, IDLE_LOG_INTERVAL, LEASE_SAFETY, LEASE_SECONDS,
    LOGIN_WAIT_RESERVE, NOT_ASSIGNED_DELAY, POLL_INTERVAL, log,
)
from .errors import JobError
from .media import apply_logo, download_images, load_logo_config


class Runner:
    def __init__(self, publisher):
        self.publisher = publisher
        self.logo = None

    # ---- Một job ----

    def run_job(self, driver, job, claimed_at):
        """Chạy toàn bộ thao tác cho 1 job tới khi đăng xong. Trả về link bài (có thể None). Lỗi -> JobError."""
        p = self.publisher
        request = job.get("request") or {}
        job_dir = os.path.join(p.work_dir, str(job["id"]))
        shutil.rmtree(job_dir, ignore_errors=True)
        os.makedirs(job_dir, exist_ok=True)

        def guard():
            # Gần hết 15 phút: server sắp cho thất bại, đăng lúc này dễ tạo bài trùng khi người dùng đăng lại
            remaining = LEASE_SECONDS - (time.time() - claimed_at)
            if remaining < LEASE_SAFETY:
                raise JobError("LEASE_EXPIRED", f"Chỉ còn {int(remaining)} giây trước khi job hết hạn, không bấm đăng")

        try:
            log("Bước 1: KIỂM TRA ĐĂNG NHẬP")
            # Chưa đăng nhập: chờ đăng nhập tay, nhưng không quá hạn job (chừa LOGIN_WAIT_RESERVE để đăng bài)
            p.wait_until_logged_in(driver, claimed_at + LEASE_SECONDS - LOGIN_WAIT_RESERVE)
            p.check_account(driver, request.get("account"))

            log("Bước 2: TẢI ẢNH")
            paths = download_images(job_dir, request.get("images") or [])

            if self.logo:
                log("Bước 3: CHÈN LOGO SHOP")
                for path in paths:
                    apply_logo(path, self.logo)
                log(f"ĐÃ CHÈN LOGO vào {len(paths)} ảnh")
            else:
                log("Bước 3: không cấu hình logo, đăng ảnh gốc")
            p.prepare_images(paths)

            log(f"Bước 4: ĐĂNG LÊN {p.label.upper()}")
            return p.publish(driver, request, paths, guard)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def finish_after_success(self, driver):
        """Đăng xong: dừng CLOSE_AFTER_SUCCESS_DELAY giây rồi tắt Chrome (job sau mở lại)."""
        time.sleep(CLOSE_AFTER_SUCCESS_DELAY)
        browser.close_chrome(driver)

    def process_job(self, driver, client, job):
        """Xử lý 1 job. Trả về True nếu đã tắt Chrome (đăng thành công) để lần sau mở lại."""
        job_id = job["id"]
        claimed_at = time.time()
        request = job.get("request") or {}
        log("=" * 60)
        log(f"NHẬN JOB #{job_id} ({job.get('type')}): bài #{request.get('post_id')}, {len(request.get('images') or [])} ảnh")
        try:
            post_url = self.run_job(driver, job, claimed_at)
            # Đăng xong: tắt Chrome ngay (không chờ gửi kết quả, gửi có thể chờ mạng lâu), rồi mới báo kết quả
            self.finish_after_success(driver)
            log(f"Bước 5: BÁO KẾT QUẢ{f' ({post_url})' if post_url else ''}")
            client.submit_completed(job_id, post_url)
            return True
        except JobError as e:
            log(f"JOB #{job_id} LỖI [{e.code}]: {e}")
            browser.screenshot(driver, self.publisher.error_dir, f"job-{job_id}")
            client.submit_failed(job_id, e.code, e)
        except WebDriverException as e:
            log(f"JOB #{job_id} LỖI CHROME: {e}")
            browser.screenshot(driver, self.publisher.error_dir, f"job-{job_id}")
            client.submit_failed(job_id, "WORKER_ERROR", f"Lỗi Chrome: {e}")
        except Exception as e:
            log(f"JOB #{job_id} LỖI: {e}")
            browser.screenshot(driver, self.publisher.error_dir, f"job-{job_id}")
            client.submit_failed(job_id, "WORKER_ERROR", e)
        finally:
            log(f"JOB #{job_id} (bài #{request.get('post_id')}) KẾT THÚC sau {int(time.time() - claimed_at)} giây")
            log("=" * 60)
        # Job lỗi: giữ Chrome mở để xem lại
        return False

    def start_chrome(self):
        p = self.publisher
        return browser.start_chrome(p.profile_dir, p.profile_directory, p.port)

    # ---- Chế độ chạy ----

    def run_mock(self):
        """Không gọi API A51: chạy 1 lần với job giả lập (ảnh lấy từ job/output). VẪN thao tác thật trên nền tảng."""
        p = self.publisher
        output_dir = os.path.join(BASE_DIR, "job", "output")
        images = [os.path.join(output_dir, f) for f in sorted(os.listdir(output_dir))[:2]] if os.path.isdir(output_dir) else []
        job = {
            "id": 0,
            "type": f"{p.platform}_publish",
            "request": {
                "post_id": 0,
                "platform": p.platform,
                "title": "Ảnh mới từ A51",
                "content": f"Bài thử của worker đăng {p.label}.",
                "hashtags": ["a51"],
                "description": f"Bài thử của worker đăng {p.label}.\n#a51",
                "images": images,
                "account": p.mock_account(),
            },
        }
        log(f"CHẾ ĐỘ GIẢ LẬP: không gọi API, dùng job thử với {len(images)} ảnh trong job/output "
            f"(VẪN thao tác thật trên {p.label}; {p.prefix}_AUTO_POST=0 để không bấm đăng)")
        driver = None
        started = time.time()
        try:
            driver = self.start_chrome()
            post_url = self.run_job(driver, job, started)
            log(f"HOÀN THÀNH sau {int(time.time() - started)} giây{f': {post_url}' if post_url else ''}")
            self.finish_after_success(driver)
            driver = None
        except KeyboardInterrupt:
            log("ĐÃ DỪNG (Ctrl+C).")
        except JobError as e:
            log(f"LỖI [{e.code}]: {e}")
            browser.screenshot(driver, p.error_dir, "mock")
        except Exception as e:
            log(f"LỖI: {e}")
            browser.screenshot(driver, p.error_dir, "mock")
        finally:
            if driver is not None:
                log("Chrome vẫn mở để xem lại, tắt thủ công khi xem xong.")

    def run(self, once=False):
        """Vòng lặp nhận job. once: làm xong 1 job (hoặc chưa có job) thì dừng."""
        p = self.publisher
        if not p.token:
            raise SystemExit(f"Chưa đặt {p.prefix}_WORKER_TOKEN trong .env (tạo bằng: php artisan workers:create {{tên}}, "
                             f"rồi admin gắn worker với tài khoản {p.label} ở trang Tài khoản đăng bài).")

        client = A51Client(p.token)
        log(f"WORKER ĐĂNG {p.label.upper()} ĐANG CHẠY, LẤY JOB TỪ {API_BASE_URL}/publish-jobs (Ctrl+C để dừng)")
        log("Chưa mở Chrome: chỉ mở khi có job, đăng xong thì tắt (job lỗi thì giữ Chrome để xem lại)")

        driver = None
        idle_since = None
        next_idle_log = 0
        try:
            while True:
                try:
                    job = client.claim()
                except requests.RequestException as e:
                    log(f"KHÔNG GỌI ĐƯỢC API: {e}. Thử lại sau {API_ERROR_DELAY} giây...")
                    time.sleep(API_ERROR_DELAY)
                    continue

                if job == NOT_ASSIGNED:
                    log(f"Worker chưa được gắn tài khoản {p.label} hoặc tài khoản đang tắt (admin -> Tài khoản đăng bài). "
                        f"{'Dừng.' if once else f'Hỏi lại sau {NOT_ASSIGNED_DELAY} giây...'}")
                    if once:
                        return
                    time.sleep(NOT_ASSIGNED_DELAY)
                    continue

                if job is None:
                    if once:
                        log("Chưa có job, dừng (--once).")
                        return
                    if idle_since is None:
                        idle_since = time.time()
                        next_idle_log = idle_since + IDLE_LOG_INTERVAL
                        log(f"Chưa có job, kiểm tra lại mỗi {POLL_INTERVAL} giây...")
                    elif time.time() >= next_idle_log:
                        log(f"Vẫn chưa có job (đã chờ {int(time.time() - idle_since) // 60} phút)")
                        next_idle_log += IDLE_LOG_INTERVAL
                    time.sleep(POLL_INTERVAL)
                    continue

                if (job.get("request") or {}).get("platform", p.platform) != p.platform:
                    # Server chỉ giao job đúng nền tảng của tài khoản; khác là cấu hình sai (token của worker khác)
                    client.submit_failed(job["id"], "WORKER_PLATFORM_MISMATCH",
                                         f"Worker {p.platform} nhận job {job.get('type')}: kiểm tra token / tài khoản gắn worker")
                    continue

                idle_since = None
                # Có job: dùng lại Chrome đang mở, chưa mở / đã bị tắt thủ công thì mở mới
                if driver is None or not browser.chrome_alive(driver):
                    try:
                        driver = self.start_chrome()
                    except Exception as e:
                        log(f"KHÔNG MỞ ĐƯỢC CHROME: {e}")
                        client.submit_failed(job["id"], "WORKER_ERROR", f"Không mở được Chrome: {e}")
                        driver = None
                        time.sleep(API_ERROR_DELAY)
                        continue
                if self.process_job(driver, client, job):
                    driver = None
                if once:
                    return
        except KeyboardInterrupt:
            # Job đang làm dở (nếu có): server cho thất bại khi quá 15 phút
            log("ĐÃ DỪNG WORKER (Ctrl+C). Chrome vẫn mở, tắt thủ công khi cần.")


def main(publisher, mock=False, once=False):
    runner = Runner(publisher)
    runner.logo = load_logo_config(publisher.prefix)
    os.makedirs(publisher.work_dir, exist_ok=True)
    if mock:
        runner.run_mock()
    else:
        runner.run(once=once)
