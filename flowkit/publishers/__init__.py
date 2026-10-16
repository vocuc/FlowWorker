from .facebook import FacebookPublisher
from .instagram import InstagramPublisher
from .tiktok import TikTokPublisher

PUBLISHERS = {p.platform: p for p in (TikTokPublisher, FacebookPublisher, InstagramPublisher)}
