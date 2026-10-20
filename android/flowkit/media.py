"""Ảnh của job: tải về, đổi sang JPG, chèn viền cho vừa tỉ lệ nền tảng nhận (Instagram)."""

import os

import requests
from PIL import Image

from .config import HTTP_TIMEOUT, JPEG_QUALITY, log
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
