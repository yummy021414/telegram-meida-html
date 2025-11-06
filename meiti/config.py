import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Server
DOMAIN = os.getenv('DOMAIN', 'https://hotbaby.top')
WEB_PORT = int(os.getenv('WEB_PORT', 5000))

# Album Settings
ALBUM_EXPIRE_DAYS = int(os.getenv('ALBUM_EXPIRE_DAYS', 3))  # 3天后自动焚毁
COLLECTION_DELAY_SECONDS = int(os.getenv('COLLECTION_DELAY_SECONDS', 3))
MAX_MEDIA_GROUPS = int(os.getenv('MAX_MEDIA_GROUPS', 50))  # 最多50组媒体

# Database
DATABASE_PATH = os.getenv('DATABASE_PATH', './data/albums.db')

# Admin Settings
ADMIN_USER_IDS = [int(x) for x in os.getenv('ADMIN_USER_IDS', '').split(',') if x.strip()]  # 超管用户ID列表，用逗号分隔

# Ensure data directory exists
os.makedirs(os.path.dirname(DATABASE_PATH) if os.path.dirname(DATABASE_PATH) else './data', exist_ok=True)

