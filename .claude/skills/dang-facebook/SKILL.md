---
name: dang-facebook
description: Chạy / kiểm tra worker đăng bài Facebook của FlowKit (đăng ảnh + nội dung vào trang, nhóm hoặc trang cá nhân qua Selenium, nhận job từ A51). Dùng khi người dùng muốn chạy thử 1 bài Facebook, chạy worker Facebook, xem vì sao job Facebook lỗi (FACEBOOK_*), đăng nhập lại Facebook cho worker, hoặc sửa phần thao tác trên Facebook.
---

# Đăng Facebook (worker FlowKit)

Worker Facebook **đăng thẳng** bài (ảnh + nội dung) vào link admin khai báo cho tài khoản (`target_url`: trang, nhóm hoặc trang cá nhân).
Nghiệp vụ: `D:\a90-fe\note\dang-bai-da-nen-tang.txt`.

**Rủi ro:** đăng tự động bằng trình duyệt vi phạm điều khoản của Facebook, có thể bị checkpoint / chặn đăng nhóm / khoá tài khoản.
Không đăng dồn dập cùng nội dung vào nhiều nhóm. Fanpage có Graph API chính thức nếu sau này cần ổn định hơn.

## Code

- Phần riêng Facebook: `flowkit/publishers/facebook.py`: mở `target_url`, bấm ô "Bạn đang nghĩ gì?" / "Bạn viết gì đi...",
  điền nội dung (CDP insertText, không dùng clipboard), tải ảnh vào ô chọn file của hộp thoại, chờ tải xong, bấm "Đăng",
  chờ hộp thoại đóng. Chỉ dựa vào role / aria-label / chữ (tiếng Việt + Anh), KHÔNG dùng class kiểu `x1lliihq`.
  Chữ / selector nằm ở đầu file: sửa ở đó khi Facebook đổi giao diện.
- Dùng chung: `flowkit/runner.py`, `flowkit/a51.py`, `flowkit/media.py`, `flowkit/browser.py`.
- `facebook_worker.py` (cũ, gọi API batman.topmcn.vn) không còn dùng; worker mới là `python worker.py facebook`.

## Chạy

Luôn chạy từ thư mục `D:\FlowKit`.

| Việc | Lệnh |
|---|---|
| Chạy thử, KHÔNG bấm Đăng (khuyên dùng lần đầu) | `FACEBOOK_AUTO_POST=0` + `FACEBOOK_MOCK_TARGET_URL=<link>` rồi `python worker.py facebook --mock` |
| Chạy thử và đăng thật (ảnh trong `job/output`) | `python worker.py facebook --mock` |
| Làm 1 job thật từ A51 rồi dừng | `python worker.py facebook --once` |
| Chạy worker liên tục | `python worker.py facebook` (hoặc `run_all_workers.bat`) |

Trong PowerShell đặt biến bằng `$env:FACEBOOK_AUTO_POST='0'`; hoặc ghi vào `.env`.
Worker mở Chrome có giao diện, có thể chờ đăng nhập tay tới 10 phút: chạy nền và đọc log.
Bài đăng thật lên Facebook không rút lại được: hỏi người dùng trước khi chạy chế độ đăng thật.

## Cấu hình (`.env`)

- `FACEBOOK_WORKER_TOKEN`; admin gắn worker với tài khoản A51 + nền tảng Facebook + **link nơi đăng bài** ở trang **Tài khoản đăng bài**.
- `FACEBOOK_CHROME_PROFILE` (mặc định `C:\selenium-facebook-profile`), `FACEBOOK_CHROME_PORT` (9224).
  Muốn dùng lại profile của `facebook_worker.py` cũ (đã đăng nhập): `FACEBOOK_CHROME_PROFILE=C:\selenium-profile`,
  `FACEBOOK_CHROME_PROFILE_DIRECTORY=Profile1`, nhưng KHÔNG chạy cùng lúc với worker tạo ảnh (chung profile).
- `FACEBOOK_AUTO_POST=0`: điền xong bài nhưng không bấm Đăng, job báo `FACEBOOK_AUTO_POST_DISABLED`.
- Logo: `FACEBOOK_LOGO_*` hoặc `LOGO_*`.

## Khi job lỗi

Ảnh chụp màn hình lỗi: `job/facebook/errors/job-{id}-*.png`. Chrome được giữ mở để xem lại.

| Mã lỗi | Ý nghĩa / cách xử lý |
|---|---|
| `FACEBOOK_LOGIN_REQUIRED` | Chưa / bị đăng xuất: đăng nhập tay trên cửa sổ Chrome của worker |
| `FACEBOOK_CHECKPOINT` | Facebook yêu cầu xác minh: người dùng xử lý tay, tạm dừng worker |
| `FACEBOOK_TARGET_MISSING` | Tài khoản chưa khai báo link nơi đăng (admin) |
| `FACEBOOK_COMPOSER_NOT_FOUND` | Không mở được hộp thoại tạo bài: link sai, không có quyền đăng vào nhóm, hoặc Facebook đổi chữ (sửa `COMPOSER_PREFIXES`) |
| `FACEBOOK_COMPOSE_FAILED` | Nội dung trong ô không khớp bài cần đăng |
| `FACEBOOK_UPLOAD_FAILED` | Không thấy ô chọn ảnh / ảnh tải mãi không xong |
| `FACEBOOK_POST_FAILED` | Không thấy nút Đăng hoặc bấm xong hộp thoại không đóng (bài có thể đang chờ duyệt / bị chặn) |
| `FACEBOOK_AUTO_POST_DISABLED` | Đang chạy thử (`FACEBOOK_AUTO_POST=0`) |
| `LEASE_EXPIRED` | Gần hết 15 phút nên không bấm Đăng (tránh bài trùng) |

Job lỗi không được giao lại: người dùng bấm đăng lại nền tảng lỗi trên A51.
