---
name: dang-instagram
description: Chạy / kiểm tra worker đăng bài Instagram của FlowKit (đăng ảnh / carousel + chú thích trên instagram.com qua Selenium, nhận job từ A51). Dùng khi người dùng muốn chạy thử 1 bài Instagram, chạy worker Instagram, xem vì sao job Instagram lỗi (INSTAGRAM_*), đăng nhập lại Instagram cho worker, hoặc sửa phần thao tác trên Instagram.
---

# Đăng Instagram (worker FlowKit)

Worker Instagram **đăng thẳng** bài (1-10 ảnh dạng carousel + chú thích) lên tài khoản Instagram đã đăng nhập trong profile Chrome của worker.
Nghiệp vụ: `D:\a90-fe\note\dang-bai-da-nen-tang.txt`.

**Rủi ro:** đăng tự động bằng trình duyệt vi phạm điều khoản của Instagram, có thể bị yêu cầu xác minh / khoá tài khoản.
Tài khoản Business / Creator có API chính thức (Instagram Graph API) nếu sau này cần ổn định hơn.

## Giới hạn

- Tối đa 10 ảnh, chú thích (nội dung + hashtag) tối đa 2200 ký tự: A51 chặn ngay lúc bấm đăng.
- Instagram chỉ nhận tỉ lệ 4:5 đến 1.91:1: worker **chèn viền trắng** (không cắt ảnh) rồi chọn tỉ lệ "Gốc" ở bước cắt ảnh.

## Code

- Phần riêng Instagram: `flowkit/publishers/instagram.py`: bấm Tạo -> Bài viết, đưa ảnh vào ô chọn file, chọn tỉ lệ Gốc,
  Tiếp -> Tiếp, điền chú thích, bấm Chia sẻ, chờ "Đã chia sẻ bài viết". Kiểm tra username qua API nội bộ của Instagram web.
  Chữ / selector nằm ở đầu file: sửa ở đó khi Instagram đổi giao diện.
- Dùng chung: `flowkit/runner.py`, `flowkit/a51.py`, `flowkit/media.py` (`pad_to_aspect`), `flowkit/browser.py`.

## Chạy

Luôn chạy từ thư mục `D:\FlowKit`.

| Việc | Lệnh |
|---|---|
| Chạy thử, KHÔNG bấm Chia sẻ (khuyên dùng lần đầu) | `INSTAGRAM_AUTO_POST=0` rồi `python worker.py instagram --mock` |
| Chạy thử và đăng thật (ảnh trong `job/output`) | `python worker.py instagram --mock` |
| Làm 1 job thật từ A51 rồi dừng | `python worker.py instagram --once` |
| Chạy worker liên tục | `python worker.py instagram` (hoặc `run_all_workers.bat`) |

Trong PowerShell đặt biến bằng `$env:INSTAGRAM_AUTO_POST='0'`; hoặc ghi vào `.env`.
Worker mở Chrome có giao diện, có thể chờ đăng nhập tay tới 10 phút: chạy nền và đọc log.
Bài đăng thật không rút lại được: hỏi người dùng trước khi chạy chế độ đăng thật.

## Cấu hình (`.env`)

- `INSTAGRAM_WORKER_TOKEN`; admin gắn worker với tài khoản A51 + nền tảng Instagram (+ username để kiểm tra) ở trang **Tài khoản đăng bài**.
- `INSTAGRAM_CHROME_PROFILE` (mặc định `C:\selenium-instagram-profile`), `INSTAGRAM_CHROME_PORT` (9225).
- `INSTAGRAM_AUTO_POST=0`: điền xong bài nhưng không bấm Chia sẻ, job báo `INSTAGRAM_AUTO_POST_DISABLED`.
- `INSTAGRAM_MOCK_USERNAME`: username dùng khi `--mock` (để trống: bỏ kiểm tra tài khoản).
- Logo: `INSTAGRAM_LOGO_*` hoặc `LOGO_*`.

## Khi job lỗi

Ảnh chụp màn hình lỗi: `job/instagram/errors/job-{id}-*.png`. Chrome được giữ mở để xem lại.

| Mã lỗi | Ý nghĩa / cách xử lý |
|---|---|
| `INSTAGRAM_LOGIN_REQUIRED` | Chưa / bị đăng xuất: đăng nhập tay trên cửa sổ Chrome của worker |
| `INSTAGRAM_ACCOUNT_MISMATCH` | Chrome đăng nhập tài khoản khác username admin khai báo |
| `INSTAGRAM_CHECKPOINT` | Instagram yêu cầu xác minh (`/challenge`): người dùng xử lý tay |
| `INSTAGRAM_COMPOSER_NOT_FOUND` | Không mở được hộp thoại tạo bài (sửa `CREATE_LABELS` / `POST_MENU_TEXTS`) |
| `INSTAGRAM_UPLOAD_FAILED` | Không thấy ô chọn ảnh / Instagram không nhận ảnh |
| `INSTAGRAM_COMPOSE_FAILED` | Không tới bước chú thích hoặc chú thích không khớp |
| `INSTAGRAM_SHARE_FAILED` | Không thấy nút Chia sẻ / Instagram báo không chia sẻ được / chờ quá lâu |
| `INSTAGRAM_TOO_MANY_IMAGES`, `IMAGE_PREPARE_FAILED` | Quá 10 ảnh / lỗi chèn viền ảnh |
| `INSTAGRAM_AUTO_POST_DISABLED` | Đang chạy thử (`INSTAGRAM_AUTO_POST=0`) |
| `LEASE_EXPIRED` | Gần hết 15 phút nên không bấm Chia sẻ (tránh bài trùng) |

Job lỗi không được giao lại: người dùng bấm đăng lại nền tảng lỗi trên A51.
