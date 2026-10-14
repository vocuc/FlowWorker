# Kế hoạch: biến FlowKit thành bộ skill tự động hoá (làm ảnh sản phẩm + soạn bài TikTok)

_Ghi lại từ buổi thảo luận ngày 2026-10-14. Chưa viết code, đây là bản để suy nghĩ thêm._

> **Cập nhật 2026-10-14:** FlowKit **không tự đăng TikTok**. FlowKit chỉ tạo ảnh, viết tiêu đề, nội dung, hashtag rồi tạo thành **một bài viết trên A51**. Người dùng xem bài trên A51, đồng ý thì bấm **"Đăng bài"** trên A51, và **A51 tự gửi bài sang TikTok**.
>
> **Cập nhật 2 (2026-10-14):** Phần viết nội dung tách thành **worker riêng (content worker)**, không gắn chết với Claude. Trước mắt dùng Claude, sau này có thể đổi sang **ChatGPT, Gemini, Grok** chỉ bằng cấu hình.

## 1. Hiện trạng

`ImageGenerate.py` là worker chạy vòng lặp:
1. `GET {A51}/jobs` để nhận job (request gồm productImage, outfitImage, prompt, aspectRatio, modelDisplayName).
2. Mở Google Flow bằng Selenium, dán JSON vào, bấm tạo, chờ xong rồi tải ảnh 2K về `job/output`.
3. `POST {A51}/jobs/{id}` để gửi ảnh lên (completed), hoặc báo lỗi (failed).

=> Khâu **làm ảnh** đã chạy được. Còn thiếu khâu **viết tiêu đề / nội dung / hashtag** và **tạo bài viết trên A51**.

## 2. Luồng tổng thể

```
A51 giao job image_generate ──► Image worker tạo ảnh (Google Flow) ──► trả ảnh về A51
                                                                          │
A51 giao job content_generate ──► Content worker gọi AI (Claude / ChatGPT / Gemini / Grok)
                                  viết tiêu đề + nội dung + hashtag ──► trả về A51
                                                                          │
                                                                          ▼
                                               A51 tạo bài viết (trạng thái "chờ duyệt")
                                                   │
                                     Người dùng xem / sửa trên A51
                                                   │
                                     Bấm "Đăng bài" trên A51
                                                   │
                                                   ▼
                                     A51 gọi TikTok API đăng bài
```

**Ranh giới trách nhiệm:**
- **FlowKit – Image worker**: chỉ nhận job `image_generate`, tạo ảnh bằng Google Flow (Selenium, cần trình duyệt).
- **FlowKit – Content worker**: chỉ nhận job `content_generate`, gọi AI viết tiêu đề / nội dung / hashtag. Không cần trình duyệt, chạy được ở máy khác hoặc nhiều bản song song.
- Cả hai **không** giữ token TikTok, **không** gọi TikTok API.
- **A51**: giao job, lưu bài viết, cho người dùng duyệt / sửa, và **đăng sang TikTok** khi người dùng bấm.

## 3. Các quyết định đã chốt

| Câu hỏi | Quyết định |
|---|---|
| Skill của Claude Code hay pipeline tự chạy? | **Cả hai**: lõi là các script Python dùng chung, worker gọi tự động, skill bọc lại để Claude gọi |
| Số tài khoản TikTok | **1 tài khoản** (kết nối ở phía A51) |
| Đăng ảnh hay video? | **Cả hai**, nhưng **làm ảnh trước**, video phát triển sau |
| Có duyệt trước khi đăng? | **Có**: FlowKit tạo **bài viết chờ duyệt trên A51**, người dùng bấm **"Đăng bài"** trên A51 |
| Ai đăng lên TikTok? | **A51**, không phải FlowKit |
| Ai điều phối? | **Server A51** nói cho worker biết phải làm gì |
| Tiêu đề / nội dung / hashtag ai viết? | **Content worker riêng**, gọi AI qua lớp provider chung. Làm **Claude** trước, sau thêm **ChatGPT / Gemini / Grok** |
| Ai chọn dùng AI / model nào để viết nội dung? | **Người dùng tự cấu hình trên A51**. A51 gửi `provider` + `model` trong từng job, content worker làm theo |
| Sửa phía server A51 | **Chưa làm**, để sau |

## 4. Kiến trúc đề xuất (phía FlowKit)

### Job có thêm trường `type` (A51 quyết định thứ tự các bước)

```json
{ "id": 123, "type": "image_generate",   "request": { "productImage": "...", "prompt": "...", ... } }
{ "id": 124, "type": "content_generate", "request": { "images": ["..."], "productName": "...", "notes": "...", "provider": "claude", "model": "claude-opus-5-5" } }
{ "id": 125, "type": "video_generate",   "request": { ... } }   // làm sau
```

- Job không có `type` thì mặc định là `image_generate`, để không phá worker hiện tại.
- Kết quả vẫn trả qua `POST /jobs/{id}`.
- Job `content_generate` trả về:
  ```json
  { "title": "...", "description": "...", "hashtags": ["#...", "#..."] }
  ```
  A51 dùng kết quả này (cùng ảnh của job `image_generate`) để tạo **bài viết chờ duyệt**.
- `provider` + `model` lấy từ **cấu hình của người dùng trên A51** (xem "Chọn model nội dung" bên dưới). Job thiếu hai trường này (job cũ / lỗi cấu hình) thì content worker dùng mặc định `CONTENT_PROVIDER` / `CONTENT_MODEL` trong `.env`.
- Job chỉ định provider mà worker **không có API key** → báo `failed` với lỗi `provider_not_configured` (ghi rõ thiếu biến key nào). Model không tồn tại → `model_not_found`. Worker **không** tự đổi sang AI khác, để người dùng biết cấu hình đang sai.
- Kết quả trả thêm `provider` và `model` đã dùng, để A51 lưu lại và so sánh chất lượng giữa các AI.
- Worker không cần biết quy trình, chỉ làm từng việc được giao.
- Mỗi worker khi `GET /jobs` gửi kèm loại job nó nhận được (ví dụ `?types=content_generate`), để image worker không nhận nhầm job nội dung và ngược lại. (Cần A51 hỗ trợ.)

### Content worker: lớp provider chung

Mọi AI đều đi qua **một interface chung**, phần còn lại của worker không biết đang dùng AI nào:

```python
class ContentProvider:
    name: str  # "claude" | "openai" | "gemini" | "grok"

    def generate(self, req: ContentRequest) -> ContentResult:
        """Nhận ảnh + thông tin sản phẩm, trả về title / description / hashtags."""
```

- `ContentRequest`: danh sách ảnh (bytes hoặc URL), tên sản phẩm, ghi chú, giọng văn, ngôn ngữ.
- `ContentResult`: `title`, `description`, `hashtags`, `provider`, `model`.
- **Prompt dùng chung** cho mọi provider (đặt ở `prompts/`), chỉ phần gọi API là khác nhau.
- **Kiểm tra kết quả dùng chung** (sau khi provider trả về): `title` ≤ 90 ký tự, `description` + hashtag ≤ 4000, hashtag có dấu `#`, bỏ trùng. Sai thì thử lại 1 lần, vẫn sai thì báo `failed`.
- **Thêm AI mới** = viết thêm 1 file trong `providers/` và đăng ký tên vào `registry`. Không đụng worker, không đụng prompt.
- (Tuỳ chọn, sau) **Dự phòng**: người dùng chọn thêm AI dự phòng trên A51, A51 gửi kèm trong job (`fallback: [{provider, model}, ...]`). AI chính lỗi tạm thời / hết quota thì worker thử AI dự phòng, và ghi rõ trong kết quả là đã dùng AI nào.

### Chọn model nội dung (người dùng cấu hình trên A51)

- Trên A51 có trang **Cài đặt → AI viết nội dung**: người dùng chọn **provider** (Claude / ChatGPT / Gemini / Grok) và **model** của provider đó.
- Danh sách provider / model để chọn được **cấu hình sẵn trên A51** (danh sách cố định, ví dụ Claude: `claude-opus-5-5`, ...; ChatGPT: ...; Gemini: ...; Grok: ...). Worker **không** gửi danh sách lên A51.
- Người dùng có thể chọn cả AI mà worker chưa có key. Khi nhận job, worker **kiểm tra key** của provider đó trong `.env`:
  - Có key → chạy bình thường.
  - **Không có key** → báo `failed` về A51 với lỗi `provider_not_configured` và thông báo dễ hiểu, ví dụ: _"Chưa cấu hình API key cho Gemini (GEMINI_API_KEY)"_. A51 hiển thị lỗi này cho người dùng để họ thêm key hoặc chọn AI khác.
- A51 lưu lựa chọn và gắn vào mỗi job `content_generate` khi tạo job.
- Đổi cấu hình chỉ áp dụng cho job tạo **sau** khi đổi; job đang chạy giữ nguyên.
- **API key vẫn nằm ở `.env` của content worker**, A51 không giữ và không gửi key qua job.

### Cấu trúc thư mục

```
FlowKit/
├─ flowkit/
│  ├─ config.py            # đọc .env (bỏ token ghi cứng)
│  ├─ a51.py               # claim_job / submit_completed / submit_failed (dùng chung 2 worker)
│  ├─ image/
│  │  └─ flow_image.py     # tách từ ImageGenerate.py
│  ├─ content/
│  │  ├─ base.py           # ContentProvider, ContentRequest, ContentResult
│  │  ├─ registry.py       # tên provider → class; chọn theo provider/model trong job; kiểm tra có key chưa
│  │  ├─ validate.py       # kiểm tra độ dài, hashtag
│  │  ├─ prompts/          # prompt dùng chung cho mọi AI
│  │  └─ providers/
│  │     ├─ claude.py      # làm trước
│  │     ├─ openai.py      # ChatGPT (sau)
│  │     ├─ gemini.py      # (sau)
│  │     └─ grok.py        # (sau)
│  └─ video.py             # (sau)
├─ image_worker.py         # vòng lặp: chỉ nhận image_generate
├─ content_worker.py       # vòng lặp: chỉ nhận content_generate
└─ .claude/skills/
   ├─ tao-anh-san-pham/
   ├─ viet-noi-dung/   # tiêu đề + nội dung + hashtag
   ├─ tao-bai-a51/     # gộp ảnh + nội dung thành bài viết chờ duyệt trên A51
   └─ a51-jobs/        # xem/đẩy job, chạy thử 1 job
```

> Bỏ `tiktok.py` và skill `dang-tiktok-nhap`: việc đăng TikTok chuyển hẳn sang A51.

### Thứ tự làm dự kiến
1. Tách `ImageGenerate.py` thành `flowkit/` + `image_worker.py`, giữ nguyên hành vi.
2. Chuyển token sang `.env`, thêm `.gitignore`.
3. Viết `content/base.py`, `registry.py`, `validate.py`, prompt dùng chung.
4. Viết provider đầu tiên: `providers/claude.py`.
5. Viết `content_worker.py`, nhận job `content_generate`, chạy đúng `provider` + `model` ghi trong job, trả kết quả về A51. Thiếu key thì báo lỗi `provider_not_configured`.
6. Viết các skill trong `.claude/skills/`.
7. (Sau) Thêm provider `openai.py`, `gemini.py`, `grok.py`; cơ chế dự phòng.
8. (Sau) Video.

### Việc phía A51 (để sau)
1. Hỗ trợ trường `type` trong job, và cho worker lọc theo loại khi nhận job (`GET /jobs?types=...`). Khi job `image_generate` xong thì tự tạo job `content_generate` kèm ảnh.
2. Bảng / màn hình **bài viết**: ảnh, tiêu đề, nội dung, hashtag, trạng thái (`chờ duyệt` → `đang đăng` → `đã đăng` / `lỗi`).
3. Cho người dùng **sửa** tiêu đề / nội dung / hashtag trước khi đăng.
4. Nút **"Đăng bài"** → A51 gọi TikTok Content Posting API (xem mục 5).
5. Kết nối tài khoản TikTok (OAuth) và lưu token phía A51.
6. Trang **Cài đặt → AI viết nội dung**: danh sách provider + model **cấu hình sẵn** trên A51; người dùng chọn; gắn `provider` + `model` vào mỗi job `content_generate`; hiển thị lỗi `provider_not_configured` cho người dùng khi worker báo thiếu key.

## 5. Thông tin kỹ thuật đã tra

### Các AI cho content worker

Yêu cầu chung với mọi provider: **nhận được ảnh** (để nhìn ảnh rồi viết) và **trả JSON đúng schema** (`title`, `description`, `hashtags`).

| Provider | Thư viện | Biến môi trường | Ghi chú |
|---|---|---|---|
| **Claude** (làm trước) | `pip install anthropic` (máy hiện chưa có) | `ANTHROPIC_API_KEY` | Model `claude-opus-5-5`. Ép JSON bằng `output_config.format` (json_schema). Gửi ảnh base64. |
| ChatGPT (OpenAI) | `pip install openai` | `OPENAI_API_KEY` | Có structured output theo JSON schema, nhận ảnh. Cần tra lại model + cú pháp khi làm. |
| Gemini (Google) | `pip install google-genai` | `GEMINI_API_KEY` | Có `response_schema` để ép JSON, nhận ảnh. Cần tra lại khi làm. |
| Grok (xAI) | dùng được thư viện `openai` với `base_url` của xAI | `XAI_API_KEY` | API tương thích OpenAI, nên `grok.py` có thể kế thừa phần lớn `openai.py`. Cần tra lại khi làm. |

Cấu hình trong `.env` của content worker (chỉ chứa key và giá trị dự phòng; **việc chọn model là của người dùng trên A51**):
```
ANTHROPIC_API_KEY=...            # thiếu key của provider nào thì job dùng provider đó báo lỗi provider_not_configured
OPENAI_API_KEY=
GEMINI_API_KEY=
XAI_API_KEY=
CONTENT_PROVIDER=claude          # chỉ dùng khi job không ghi provider
CONTENT_MODEL=claude-opus-5-5    # chỉ dùng khi job không ghi model
```

- Giới hạn cho khớp TikTok đặt ở cả prompt / schema và `validate.py`: `title` ≤ 90 ký tự, `description` + hashtag ≤ 4000 ký tự.

### TikTok Content Posting API (**A51 dùng**, FlowKit không gọi)
- Endpoint: `POST https://open.tiktokapis.com/v2/post/publish/content/init/`
- Body chính: `media_type: "PHOTO"`, `post_mode: "DIRECT_POST"` (đăng thẳng, vì người dùng đã duyệt trên A51) hoặc `"MEDIA_UPLOAD"` (vào hộp thư nháp TikTok), `post_info.title` (≤ 90 ký tự), `post_info.description` (≤ 4000), `source_info: { source: "PULL_FROM_URL", photo_images: [...], photo_cover_index: 0 }`.
- Quyền: `video.publish` (đăng thẳng) hoặc `video.upload` (nháp).
- Trả về `publish_id`, dùng để kiểm tra trạng thái và cập nhật trạng thái bài viết trên A51.

**Ràng buộc quan trọng:**
- Ảnh **chỉ nhận qua URL công khai**, và **domain phải được xác minh** với TikTok (nếu không sẽ lỗi `url_ownership_unverified`). Ảnh đã nằm trên A51 nên cần xác minh domain A51 (ví dụ `a51.vn`).
- **6 request / phút** cho mỗi access token.
- Tối đa 35 ảnh / bài.
- App chưa qua audit chỉ đăng được ở chế độ **riêng tư** (`SELF_ONLY`). Muốn đăng công khai bằng `DIRECT_POST` phải qua audit của TikTok.
- Nếu dùng `MEDIA_UPLOAD`: tối đa **5 bài nháp chờ xử lý / 24 giờ** (`spam_risk_too_many_pending_share`).
- Có cờ `is_aigc` để gắn nhãn nội dung AI. **Đã chốt: không bật** (không gửi cờ này khi đăng).

### TikTok OAuth (A51 làm)
- URL đăng nhập: `https://www.tiktok.com/v2/auth/authorize/` với `client_key`, `response_type=code`, `scope`, `redirect_uri`, `state`.
- Đổi code lấy token: `POST https://open.tiktokapis.com/v2/oauth/token/` (form-urlencoded).
- Access token sống **24 giờ**, refresh token sống **365 ngày**. Refresh không cần người dùng, nên chỉ đăng nhập một lần. Refresh token mới trả về có thể khác cái cũ, cần lưu lại.

## 6. Việc cần xử lý / lưu ý
- ⚠️ `WORKER_TOKEN` đang **ghi cứng trong `ImageGenerate.py` và đã commit vào git**. Nên tạo token mới và chuyển sang `.env`.
- (Phía A51) Cần đăng ký app trên TikTok for Developers và lấy `client_key` / `client_secret`.
- (Phía A51) Cần xác minh domain chứa ảnh với TikTok.

## 7. Câu hỏi còn mở
- A51 đăng bằng `DIRECT_POST` (đăng thẳng, cần audit để công khai) hay `MEDIA_UPLOAD` (vào nháp TikTok, người dùng bấm thêm lần nữa trong app)?
- Tạo ảnh và viết nội dung là **2 job riêng** (`image_generate` rồi `content_generate`) hay **1 job gộp** (worker làm xong cả hai rồi gửi một lần)?
- Video: ghép slideshow từ ảnh bằng ffmpeg (máy đã có ffmpeg), hay dùng Google Flow / Veo tạo video?
- Nội dung: giọng văn, độ dài, bộ hashtag cố định cho thương hiệu?
- Cấu hình model là **một cái chung cho cả A51**, hay **mỗi người dùng / mỗi shop một cấu hình riêng**?
- Có cần cho người dùng trên A51 bấm "viết lại bằng AI khác" để so sánh không?
- Content worker chạy chung máy với image worker hay tách máy / server riêng?
- Khi nào sửa server A51 để hỗ trợ `type` và màn hình bài viết?
