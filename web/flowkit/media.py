"""Ảnh của job: tải về, đổi sang JPG, chèn logo shop, chèn viền cho vừa tỉ lệ nền tảng nhận (Instagram)."""

import os

import requests
from PIL import Image, ImageChops, ImageDraw

from .config import HTTP_TIMEOUT, JPEG_QUALITY, env_first, log
from .errors import JobError


def download_images(job_dir, images):
    """Tải ảnh NGAY (link ký số ~15 phút) về thư mục của job, đặt tên 01, 02... đúng thứ tự, đổi sang JPG."""
    if not images:
        raise JobError("IMAGE_DOWNLOAD_FAILED", "Job không có ảnh")
    paths = []
    for i, source in enumerate(images, start=1):
        path = os.path.join(job_dir, f"{i:02d}.jpg")
        try:
            if source.startswith(("http://", "https://")):
                response = requests.get(source, timeout=HTTP_TIMEOUT)
                response.raise_for_status()
                content = response.content
            else:
                with open(source, "rb") as f:  # Chế độ giả lập: ảnh trên máy
                    content = f.read()
            raw_path = path + ".src"
            with open(raw_path, "wb") as f:
                f.write(content)
            # Đổi mọi định dạng sang JPG để nền tảng luôn nhận
            with Image.open(raw_path) as image:
                image.convert("RGB").save(path, "JPEG", quality=JPEG_QUALITY)
            os.remove(raw_path)
        except Exception as e:
            raise JobError("IMAGE_DOWNLOAD_FAILED", f"Không tải được ảnh {i}: {e}")
        paths.append(path)
    log(f"ĐÃ TẢI {len(paths)} ẢNH về {job_dir}")
    return paths


# ======================================
# LOGO SHOP
# ======================================

def _percent(prefix, key, default):
    """Giá trị % của logo: {PREFIX}_LOGO_{KEY} (riêng worker) rồi LOGO_{KEY} (chung), không có thì default."""
    names = [f"{prefix}_LOGO_{key}", f"LOGO_{key}"]
    raw = (env_first(names, "") or "").strip()
    if raw == "":
        return float(default)
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise SystemExit(f"Cấu hình {names[0]} / {names[1]}={raw!r} không phải số.")


def load_logo_layout(prefix):
    """
    Đọc + kiểm tra vị trí / kích thước logo lúc khởi động (worker tạo ảnh; file logo là ảnh đại diện shop, lấy từ job).
    Biến: {PREFIX}_LOGO_* (vd IMAGE_LOGO_WIDTH) hoặc LOGO_* dùng chung: WIDTH, HEIGHT, RADIUS, X, Y (% cạnh ngắn
    của ảnh). Sai -> dừng worker. Khi chèn: apply_logo(ảnh, {**layout, "path": file logo}).
    """
    config = {
        "width": _percent(prefix, "WIDTH", 7),
        "height": _percent(prefix, "HEIGHT", 7),
        "radius": _percent(prefix, "RADIUS", 50),
        "x": _percent(prefix, "X", 2),
        "y": _percent(prefix, "Y", 2),
    }
    errors = []
    if not 0 < config["width"] <= 100:
        errors.append("LOGO_WIDTH phải trong (0, 100]")
    if not 0 < config["height"] <= 100:
        errors.append("LOGO_HEIGHT phải trong (0, 100]")
    if not 0 <= config["radius"] <= 50:
        errors.append("LOGO_RADIUS phải trong [0, 50]")
    if not 0 <= config["x"] < 100 or config["width"] + config["x"] > 100:
        errors.append("LOGO_X phải >= 0 và WIDTH + X <= 100")
    if not 0 <= config["y"] < 100 or config["height"] + config["y"] > 100:
        errors.append("LOGO_Y phải >= 0 và HEIGHT + Y <= 100")
    if errors:
        raise SystemExit("CẤU HÌNH LOGO SAI: " + "; ".join(errors))

    log(f"Logo: rộng {config['width']}%, cao {config['height']}%, bo góc {config['radius']}%, "
        f"cách phải {config['x']}%, cách dưới {config['y']}% (theo cạnh ngắn của ảnh, góc dưới bên phải)")
    return config


def apply_logo(image_path, logo_config):
    """Chèn logo vào góc dưới bên phải, mọi giá trị % tính theo cạnh ngắn của ảnh. Lưu đè file (JPG)."""
    try:
        with Image.open(image_path) as source:
            image = source.convert("RGBA")
        short_side = min(image.size)
        to_px = lambda percent: max(0, round(short_side * percent / 100))

        width, height = max(1, to_px(logo_config["width"])), max(1, to_px(logo_config["height"]))
        with Image.open(logo_config["path"]) as source:
            logo = source.convert("RGBA").resize((width, height), Image.LANCZOS)

        # Bo góc: nhân kênh trong suốt của logo với mặt nạ hình chữ nhật bo góc
        radius = round(min(width, height) * logo_config["radius"] / 100)
        if radius > 0:
            mask = Image.new("L", (width, height), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
            logo.putalpha(ImageChops.multiply(logo.getchannel("A"), mask))

        left = image.width - to_px(logo_config["x"]) - width
        top = image.height - to_px(logo_config["y"]) - height
        # Phần vượt ra ngoài ảnh (phía trên / trái) bị cắt: alpha_composite không nhận toạ độ âm -> cắt logo trước
        crop_left, crop_top = max(0, -left), max(0, -top)
        if crop_left or crop_top:
            logo = logo.crop((crop_left, crop_top, width, height))
        image.alpha_composite(logo, (max(0, left), max(0, top)))

        image.convert("RGB").save(image_path, "JPEG", quality=JPEG_QUALITY)
    except Exception as e:
        raise JobError("LOGO_FAILED", f"Không chèn được logo vào {os.path.basename(image_path)}: {e}")


def pad_to_aspect(image_path, min_ratio, max_ratio, color=(255, 255, 255)):
    """
    Chèn viền (màu color) để tỉ lệ rộng / cao nằm trong [min_ratio, max_ratio], không cắt ảnh. Lưu đè file (JPG).
    Vd Instagram nhận từ 4:5 (0.8) đến 1.91:1: ảnh dọc 9:16 được thêm viền hai bên thành 4:5.
    """
    try:
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        width, height = image.size
        ratio = width / height
        if ratio < min_ratio:
            canvas = Image.new("RGB", (round(height * min_ratio), height), color)
        elif ratio > max_ratio:
            canvas = Image.new("RGB", (width, round(width / max_ratio)), color)
        else:
            return False
        canvas.paste(image, ((canvas.width - width) // 2, (canvas.height - height) // 2))
        canvas.save(image_path, "JPEG", quality=JPEG_QUALITY)
        return True
    except Exception as e:
        raise JobError("IMAGE_PREPARE_FAILED", f"Không chèn viền được ảnh {os.path.basename(image_path)}: {e}")
