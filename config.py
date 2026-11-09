import os
from pathlib import Path
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "media"
DB_PATH = BASE_DIR / "state.db"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002764115757"))
POST_WINDOW_START = time.fromisoformat(os.getenv("POST_WINDOW_START", "09:00"))
POST_WINDOW_END = time.fromisoformat(os.getenv("POST_WINDOW_END", "21:00"))
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Etc/GMT-3"))
