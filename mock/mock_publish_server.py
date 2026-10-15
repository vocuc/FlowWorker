"""
API giả lập thay cho server A51, để chạy thử tiktok_worker.py trên máy (không cần backend).

Giả lập đúng 2 API của worker đăng bài (note/dang-tiktok-qua-worker.txt ở repo a90-fe):
  GET  /api/v1/publish-jobs        -> lần lượt trả các job trong hàng đợi, hết thì 204
  POST /api/v1/publish-jobs/{id}   -> in kết quả worker gửi về, trả 200
Ảnh của job lấy từ job/output (hoặc --images), phục vụ qua http://127.0.0.1:{port}/images/...

Chạy:  python mock/mock_publish_server.py [--jobs 1] [--images 3] [--username a51.vn] [--not-assigned]
Worker trỏ vào:  AI90_API_BASE_URL=http://127.0.0.1:8765/api/v1  AI90_TIKTOK_WORKER_TOKEN=mock-token
"""

import argparse
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_DIR = os.path.join(BASE_DIR, "job", "output")
TOKEN = "mock-token"


def log(message):
    print(f"[{datetime.now():%H:%M:%S}] [MOCK API] {message}", flush=True)


def build_jobs(args):
    images = sorted(f for f in os.listdir(IMAGE_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
    if not images:
        raise SystemExit(f"Không có ảnh trong {IMAGE_DIR} để làm job giả lập.")
    images = images[: args.images]
    hashtags = ["a51", "thuworker"]
    jobs = []
    for i in range(1, args.jobs + 1):
        content = f"Một em giày xinh dành cho các tình iu đây ak"
        jobs.append({
            "id": 9000 + i,
            "type": "tiktok_publish",
            "request": {
                "post_id": 900 + i,
                "title": f"Giày sneaker lv mới 2026 #{i}",
                "content": content,
                "hashtags": hashtags,
                "description": content + "\n" + " ".join("#" + t for t in hashtags),
                # Link được tạo lúc worker nhận job (giống server thật: link ký số tạo khi claim)
                "images": images,
                "account": {"id": 1, "name": "Tài khoản thử", "username": args.username},
            },
        })
    return jobs


class Handler(BaseHTTPRequestHandler):
    queue = []
    claimed = {}
    not_assigned = False

    def log_message(self, *_):  # Tắt log mặc định của http.server
        pass

    def send_json(self, status, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        if self.headers.get("Authorization") == f"Bearer {TOKEN}":
            return True
        log(f"401: sai token ({self.headers.get('Authorization')!r}), cần Bearer {TOKEN}")
        self.send_json(401, {"message": "Token worker không hợp lệ.", "code": "WORKER_UNAUTHORIZED"})
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/images/"):
            return self.send_image(unquote(path[len("/images/"):]))
        if path.rstrip("/") != "/api/v1/publish-jobs":
            return self.send_json(404, {"message": "Not found"})
        if not self.authorized():
            return
        if Handler.not_assigned:
            log("409 TIKTOK_ACCOUNT_NOT_ASSIGNED (chạy với --not-assigned)")
            return self.send_json(409, {"message": "Worker chưa được gắn tài khoản A51 hoặc đang bị tắt.",
                                        "code": "TIKTOK_ACCOUNT_NOT_ASSIGNED"})
        if not Handler.queue:
            return self.send_json(204)

        job = Handler.queue.pop(0)
        host = self.headers.get("Host", "127.0.0.1")
        data = json.loads(json.dumps(job))
        data["request"]["images"] = [f"http://{host}/images/{name}" for name in job["request"]["images"]]
        Handler.claimed[job["id"]] = job
        log(f"GIAO JOB #{job['id']} (bài #{job['request']['post_id']}, {len(data['request']['images'])} ảnh). "
            f"Còn {len(Handler.queue)} job trong hàng đợi")
        self.send_json(200, {"data": data})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        prefix = "/api/v1/publish-jobs/"
        if not path.startswith(prefix) or not path[len(prefix):].isdigit():
            return self.send_json(404, {"message": "Not found"})
        if not self.authorized():
            return
        job_id = int(path[len(prefix):])
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json(422, {"message": "Body không phải JSON"})

        if job_id not in Handler.claimed:
            log(f"409 JOB_NOT_ASSIGNED: job #{job_id} không được giao hoặc đã có kết quả")
            return self.send_json(409, {"message": "Job không còn được giao cho worker này.", "code": "JOB_NOT_ASSIGNED"})

        status = body.get("status")
        if status not in ("completed", "failed") or (status == "failed" and not body.get("error_code")):
            log(f"422: body không hợp lệ {body}")
            return self.send_json(422, {"message": "Dữ liệu không hợp lệ", "errors": {"status": ["completed | failed"]}})

        Handler.claimed.pop(job_id)
        if status == "completed":
            log(f"JOB #{job_id}: WORKER BÁO ĐÃ LƯU NHÁP -> bài 'Đã lưu nháp TikTok'")
        else:
            log(f"JOB #{job_id}: WORKER BÁO LỖI [{body.get('error_code')}] {body.get('error_message') or ''}"
                f" -> bài về 'Chờ gửi'")
        self.send_json(200, {"data": {"id": job_id, "status": status}})

    def send_image(self, name):
        path = os.path.join(IMAGE_DIR, os.path.basename(name))
        if not os.path.isfile(path):
            return self.send_json(404, {"message": "Không có ảnh"})
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/png" if path.lower().endswith(".png") else "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description="API giả lập cho tiktok_worker.py")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--jobs", type=int, default=1, help="số job trong hàng đợi (mặc định 1)")
    parser.add_argument("--images", type=int, default=2, help="số ảnh mỗi job, lấy từ job/output (mặc định 2)")
    parser.add_argument("--username", default="",
                        help="username TikTok của job; để trống thì worker bỏ qua kiểm tra tài khoản")
    parser.add_argument("--not-assigned", action="store_true", help="luôn trả 409 TIKTOK_ACCOUNT_NOT_ASSIGNED")
    args = parser.parse_args()

    Handler.queue = build_jobs(args)
    Handler.not_assigned = args.not_assigned
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log(f"Đang chạy tại http://127.0.0.1:{args.port}/api/v1 , token: {TOKEN}")
    log(f"Hàng đợi: {len(Handler.queue)} job, mỗi job {args.images} ảnh từ {IMAGE_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Đã dừng.")


if __name__ == "__main__":
    main()
