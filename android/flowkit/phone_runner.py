"""
Worker đăng bài trên 1 điện thoại Android: máy có app TikTok / Facebook / Instagram đăng nhập sẵn (mỗi máy 1 bộ tài khoản),
mỗi nền tảng 1 token ({NAME}_{PREFIX}_TOKEN). Điện thoại chỉ mở được 1 app một lúc nên 1 tiến trình / máy:
hỏi job lần lượt từng token, có job thì làm xong (flowkit.runner.Runner.process_job) rồi mới hỏi tiếp.
Độc lập với bản web (thư mục web/): không cần Selenium / Chrome.
"""

import os
import time

import requests

from .a51 import NOT_ASSIGNED, A51Client
from .config import API_BASE_URL, API_ERROR_DELAY, JOB_DIR, IDLE_LOG_INTERVAL, NOT_ASSIGNED_DELAY, POLL_INTERVAL, log
from .device import Phone
from .errors import JobError
from .publishers import PUBLISHERS
from .runner import make_runner

PLATFORMS = ["facebook", "instagram", "tiktok"]


class PhoneRunner:
    def __init__(self, phone):
        self.phone = phone
        self.runners = []
        for platform in PLATFORMS:
            cls = PUBLISHERS.get(platform)
            token_name = f"{phone.name}_{platform.upper()}_TOKEN"
            if cls is None:
                if os.environ.get(token_name):
                    log(f"CẢNH BÁO: {platform} chưa có bản Android, bỏ qua {token_name}")
                continue
            publisher = cls(phone)
            if publisher.token:
                self.runners.append(make_runner(publisher))
        if not self.runners:
            raise SystemExit(f"Chưa đặt token nào cho {phone.name} trong .env "
                             f"({', '.join(f'{phone.name}_{p.upper()}_TOKEN' for p in sorted(PUBLISHERS))}).")
        self.clients = {id(r): A51Client(r.publisher.token) for r in self.runners}
        self.paused_until = {}

    def claim(self, runner):
        """Hỏi job cho 1 nền tảng. Trả về job hoặc None."""
        p = runner.publisher
        if time.time() < self.paused_until.get(p.platform, 0):
            return None
        try:
            job = self.clients[id(runner)].claim()
        except requests.RequestException as e:
            log(f"{p.label}: KHÔNG GỌI ĐƯỢC API: {e}. Thử lại sau {API_ERROR_DELAY} giây...")
            self.paused_until[p.platform] = time.time() + API_ERROR_DELAY
            return None
        if job == NOT_ASSIGNED:
            log(f"{p.label}: worker chưa được gắn tài khoản hoặc tài khoản đang tắt (admin -> Tài khoản đăng bài). "
                f"Hỏi lại sau {NOT_ASSIGNED_DELAY} giây...")
            self.paused_until[p.platform] = time.time() + NOT_ASSIGNED_DELAY
            return None
        if job and (job.get("request") or {}).get("platform", p.platform) != p.platform:
            self.clients[id(runner)].submit_failed(
                job["id"], "WORKER_PLATFORM_MISMATCH",
                f"Worker {p.platform} ({self.phone.name}) nhận job {job.get('type')}: kiểm tra token / tài khoản gắn worker")
            return None
        return job

    def run(self, once=False):
        labels = ", ".join(r.publisher.label for r in self.runners)
        log(f"WORKER ĐIỆN THOẠI {self.phone.name} ({self.phone.addr}) ĐANG CHẠY: {labels}. "
            f"Lấy job từ {API_BASE_URL}/publish-jobs (Ctrl+C để dừng)")
        idle_since = None
        next_idle_log = 0
        try:
            while True:
                worked = False
                for runner in self.runners:
                    job = self.claim(runner)
                    if job is None:
                        continue
                    worked = True
                    idle_since = None
                    client = self.clients[id(runner)]
                    try:
                        device = self.phone.get()
                    except JobError as e:
                        log(f"JOB #{job['id']} LỖI [{e.code}]: {e}")
                        client.submit_failed(job["id"], e.code, e)
                        time.sleep(API_ERROR_DELAY)
                        continue
                    runner.process_job(device, client, job)
                    if once:
                        return
                if worked:
                    continue
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
        except KeyboardInterrupt:
            # Job đang làm dở (nếu có): server cho thất bại khi quá 15 phút
            log(f"ĐÃ DỪNG WORKER {self.phone.name} (Ctrl+C).")


def main(phone_name, mock_platform=None, once=False, dump=False):
    phone = Phone(phone_name)
    if dump:
        # Lưu màn hình hiện tại của máy (ảnh + cây giao diện) để sửa selector
        try:
            device = phone.get()
        except JobError as e:
            raise SystemExit(f"LỖI [{e.code}]: {e}")
        log(f"ĐÃ LƯU: {device.capture(os.path.join(JOB_DIR, phone.name.lower(), 'dump'), 'screen')}.png / .xml")
        return
    if mock_platform:
        make_runner(PUBLISHERS[mock_platform](phone)).run_mock()
        return
    PhoneRunner(phone).run(once=once)
