"""
Gọi API A51 cho worker (dùng chung mọi worker đăng bài): nhận job, báo kết quả.
Đặc tả: note/dang-bai-da-nen-tang.txt ở repo a90-fe.

  GET  {API}/publish-jobs        -> 200 job | 204 chưa có job | 409 *ACCOUNT_NOT_ASSIGNED | 401 token sai
  POST {API}/publish-jobs/{id}   -> { status: completed, post_url? } | { status: failed, error_code, error_message }
"""

import time

import requests

from .config import API_BASE_URL, HTTP_TIMEOUT, SUBMIT_RETRIES, log

NOT_ASSIGNED = "NOT_ASSIGNED"


class A51Client:
    def __init__(self, token, path="publish-jobs", base_url=API_BASE_URL):
        self.url = f"{base_url}/{path}"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def claim(self):
        """
        Nhận 1 job. Trả về dict job, None khi chưa có việc (204), NOT_ASSIGNED khi worker chưa được gắn tài khoản
        (ACCOUNT_NOT_ASSIGNED / TIKTOK_ACCOUNT_NOT_ASSIGNED / FACEBOOK_... / INSTAGRAM_...).
        Phải trả kết quả trong 15 phút, quá hạn server cho thất bại (không giao lại). Lỗi mạng -> requests.RequestException.
        """
        response = self.session.get(self.url, timeout=HTTP_TIMEOUT)
        if response.status_code == 204:
            return None
        if response.status_code == 401:
            raise SystemExit("Token worker không hợp lệ hoặc worker đã bị tắt (401). Kiểm tra token trong .env.")
        if response.status_code == 409:
            code = (self._json(response) or {}).get("code") or ""
            if code.endswith("ACCOUNT_NOT_ASSIGNED"):
                return NOT_ASSIGNED
        response.raise_for_status()
        return response.json()["data"]

    def submit_completed(self, job_id, post_url=None):
        body = {"status": "completed"}
        if post_url:
            body["post_url"] = post_url
        self._submit(job_id, body)

    def submit_failed(self, job_id, code, message):
        log(f"JOB #{job_id}: đang báo lỗi [{code}] lên server...")
        self._submit(job_id, {"status": "failed", "error_code": code[:50], "error_message": str(message)[:490]})

    def _submit(self, job_id, body):
        """Gửi kết quả, lỗi mạng thì thử lại (cùng kết quả). 409 JOB_NOT_ASSIGNED: job đã quá hạn, chỉ ghi log."""
        for attempt in range(1, SUBMIT_RETRIES + 1):
            try:
                response = self.session.post(f"{self.url}/{job_id}", json=body, timeout=HTTP_TIMEOUT)
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

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except ValueError:
            return None
