---
name: dang-tiktok
description: Chạy / kiểm tra worker đăng bài TikTok của FlowKit (lưu nháp trên TikTok Studio qua Selenium, nhận job từ A51). Dùng khi người dùng muốn chạy thử 1 bài TikTok, chạy worker TikTok, xem vì sao job TikTok lỗi (TIKTOK_*), đăng nhập lại TikTok cho worker, hoặc sửa phần thao tác TikTok Studio.
---

# Đăng TikTok (worker FlowKit)

Worker TikTok **chỉ lưu nháp** trên TikTok Studio, không bao giờ bấm Đăng; nhân viên A51 vào TikTok đăng các bài nháp.
Nghiệp vụ: `D:\a90-fe\note\dang-bai-da-nen-tang.txt` (chung) và `D:\a90-fe\note\dang-tiktok-qua-worker.txt` (riêng TikTok).

## Code

- Phần riêng TikTok: `flowkit/publishers/tiktok.py` (đăng nhập, kiểm tra username, tải ảnh, điền tiêu đề / mô tả, lưu nháp).
  Chữ / selector của TikTok Studio nằm ở đầu file: sửa ở đó khi TikTok đổi giao diện.
- Dùng chung mọi nền tảng: `flowkit/runner.py` (vòng lặp, 15 phút / job), `flowkit/a51.py` (API), `flowkit/media.py` (ảnh, logo), `flowkit/browser.py`.
- `tiktok_worker.py` chỉ là lối tắt cũ của `python worker.py tiktok`.

## Chạy

Luôn chạy từ thư mục `D:\FlowKit`.

| Việc | Lệnh |
|---|---|
| Chạy thử 1 bài, không gọi API (ảnh trong `job/output`, VẪN lưu nháp thật) | `python worker.py tiktok --mock` |
| Làm 1 job thật từ A51 rồi dừng | `python worker.py tiktok --once` |
| Chạy worker liên tục | `python worker.py tiktok` (hoặc `run_all_workers.bat`) |

Worker mở Chrome có giao diện và có thể chờ đăng nhập tay tới 10 phút: chạy nền (`run_in_background`) và đọc log, đừng chặn phiên làm việc.
Trước khi chạy `--once` / vòng lặp thật, nói rõ với người dùng là worker sẽ nhận job thật của A51.

## Cấu hình (`.env`, mẫu `.env.example`)

- `TIKTOK_WORKER_TOKEN`: token worker (`php artisan workers:create {tên}` trên server), admin gắn worker với tài khoản A51 + nền tảng TikTok ở trang **Tài khoản đăng bài**.
- `TIKTOK_CHROME_PROFILE` (mặc định `C:\selenium-tiktok-profile`), `TIKTOK_CHROME_PORT` (9223).
- Logo: `TIKTOK_LOGO_PATH`, `_WIDTH`, `_HEIGHT`, `_RADIUS`, `_X`, `_Y` (hoặc `LOGO_*` dùng chung).
- `TIKTOK_MOCK_USERNAME`: username dùng khi `--mock` (để trống: bỏ kiểm tra tài khoản).

## Khi job lỗi

Ảnh chụp màn hình lỗi: `job/tiktok/errors/job-{id}-*.png` (xem bằng Read). Chrome được giữ mở để xem lại.

| Mã lỗi | Ý nghĩa / cách xử lý |
|---|---|
| `TIKTOK_LOGIN_REQUIRED` | Chưa / bị đăng xuất: chạy `--mock` hoặc worker, đăng nhập tay trên cửa sổ Chrome vừa mở |
| `TIKTOK_ACCOUNT_MISMATCH` | Chrome đăng nhập tài khoản khác username admin khai báo |
| `TIKTOK_CAPTCHA` | TikTok bắt giải captcha: người dùng giải tay rồi đăng lại bài |
| `TIKTOK_UPLOAD_FAILED` / `TIKTOK_DRAFT_FAILED` | Không thấy nút / ô hoặc TikTok không phản hồi: xem ảnh chụp, có thể TikTok đổi giao diện (sửa selector ở đầu `tiktok.py`) |
| `IMAGE_DOWNLOAD_FAILED`, `LOGO_FAILED` | Không tải được ảnh từ A51 / lỗi file logo |
| `LEASE_EXPIRED` | Gần hết 15 phút nên không lưu nháp (tránh nháp trùng) |
| `ACCOUNT_NOT_ASSIGNED` / `TIKTOK_ACCOUNT_NOT_ASSIGNED` (log) | Worker chưa được gắn tài khoản hoặc tài khoản đang tắt ở trang admin |

Job lỗi không được giao lại: người dùng bấm đăng lại trên A51.
