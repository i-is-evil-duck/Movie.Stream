import os
from dotenv import load_dotenv

# Get the path to the main root directory
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Explicitly load the .env from the root
env_path = os.path.join(BASE_DIR, '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)

# Force paths to be absolute, anchoring them to BASE_DIR (/app/)
MEDIA_DIR = os.path.abspath(os.path.join(BASE_DIR, os.getenv("MEDIA_DIR", "media")))
TMP_DIR = os.path.abspath(os.path.join(BASE_DIR, os.getenv("TMP_DIR", "tmp")))
LOG_DIR = os.path.abspath(os.path.join(BASE_DIR, os.getenv("LOG_DIR", "logs")))

YTS_API_URL = os.getenv("YTS_API_URL", "https://movies-api.accel.li/api/v2")
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
MOVIES_CACHE_TTL = int(os.getenv("MOVIES_CACHE_TTL", "21600"))

TOP_250_URL = "https://raw.githubusercontent.com/theapache64/top250/master/top250_min.json"

TRACKERS = [
    "udp://glotorrents.pw:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://torrent.gresille.org:80/announce",
    "udp://tracker.openbittorrent.com:80",
]

# Ensure directories exist
os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)