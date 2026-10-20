"""
Xử lý 1 job đăng bài trên điện thoại (vòng lặp nhận job ở flowkit.phone_runner):
  1. Mở app, kiểm tra đăng nhập + đúng tài khoản.
  2. Tải ảnh, sửa ảnh theo nền tảng, đăng bài.
  3. POST /publish-jobs/{id} -> completed (+ post_url) hoặc failed (error_code, error_message).
Đăng xong thì dọn ảnh trên máy; job lỗi thì giữ nguyên màn hình để xem lại. Job lỗi / quá hạn không được giao lại.
"""

import os
import shutil
import time

from .config import BASE_DIR, CLOSE_AFTER_SUCCESS_DELAY, LEASE_SAFETY, LEASE_SECONDS, LOGIN_WAIT_RESERVE, log
from .errors import JobError
from .media import download_images


class Runner:
    def __init__(self, publisher):
        self.publisher = publisher

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

            # Logo shop đã được chèn lúc tạo ảnh (worker tạo ảnh), không chèn lại ở đây
            p.prepare_images(paths)

            log(f"Bước 3: ĐĂNG LÊN {p.label.upper()}")
            return p.publish(driver, request, paths, guard)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def finish_after_success(self, driver):
        """Đăng xong: dừng CLOSE_AFTER_SUCCESS_DELAY giây rồi dọn máy."""
        time.sleep(CLOSE_AFTER_SUCCESS_DELAY)
        self.publisher.close_session(driver)

    def process_job(self, driver, client, job):
        """Xử lý 1 job. Trả về True nếu đăng thành công."""
        job_id = job["id"]
        claimed_at = time.time()
        request = job.get("request") or {}
        log("=" * 60)
        log(f"NHẬN JOB #{job_id} ({job.get('type')}): bài #{request.get('post_id')}, {len(request.get('images') or [])} ảnh")
        try:
            post_url = self.run_job(driver, job, claimed_at)
            # Đăng xong: dọn phiên ngay (không chờ gửi kết quả, gửi có thể chờ mạng lâu), rồi mới báo kết quả
            self.finish_after_success(driver)
            log(f"Bước 4: BÁO KẾT QUẢ{f' ({post_url})' if post_url else ''}")
            client.submit_completed(job_id, post_url)
            return True
        except JobError as e:
            log(f"JOB #{job_id} LỖI [{e.code}]: {e}")
            self.publisher.screenshot(driver, f"job-{job_id}")
            client.submit_failed(job_id, e.code, e)
        except Exception as e:
            message = self.publisher.describe_error(e)
            log(f"JOB #{job_id} LỖI: {message}")
            self.publisher.screenshot(driver, f"job-{job_id}")
            client.submit_failed(job_id, "WORKER_ERROR", message)
        finally:
            log(f"JOB #{job_id} (bài #{request.get('post_id')}) KẾT THÚC sau {int(time.time() - claimed_at)} giây")
            log("=" * 60)
        # Job lỗi: giữ nguyên màn hình điện thoại để xem lại
        return False

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
            driver = self.publisher.start_session()
            post_url = self.run_job(driver, job, started)
            log(f"HOÀN THÀNH sau {int(time.time() - started)} giây{f': {post_url}' if post_url else ''}")
            self.finish_after_success(driver)
            driver = None
        except KeyboardInterrupt:
            log("ĐÃ DỪNG (Ctrl+C).")
        except JobError as e:
            log(f"LỖI [{e.code}]: {e}")
            self.publisher.screenshot(driver, "mock")
        except Exception as e:
            log(f"LỖI: {e}")
            self.publisher.screenshot(driver, "mock")
        finally:
            if driver is not None:
                log("Giữ nguyên màn hình điện thoại để xem lại.")


def make_runner(publisher, runner_class=Runner):
    """Runner của publisher, tạo thư mục làm việc."""
    runner = runner_class(publisher)
    os.makedirs(publisher.work_dir, exist_ok=True)
    return runner
