from .instagram import InstagramPublisher
from .tiktok import TikTokPublisher

# Nền tảng đã làm (Facebook: làm tiếp)
PUBLISHERS = {p.platform: p for p in (TikTokPublisher, InstagramPublisher)}
