import os
import logging
import threading
import urllib.parse
from functools import wraps
from flask import Flask, request, send_file, abort, render_template_string
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
import requests
import shutil
import subprocess
import time
import math

load_dotenv()

app = Flask(__name__)

MEDIA_DIR = os.getenv("MEDIA_DIR", "media")
TMP_DIR = os.getenv("TMP_DIR", "tmp")
LOG_DIR = os.getenv("LOG_DIR", "logs")
YTS_API_URL = os.getenv("YTS_API_URL", "https://yts.mx/api/v2")
DOWNLOAD_RETRY_ATTEMPTS = int(os.getenv("DOWNLOAD_RETRY_ATTEMPTS", "3"))
DOWNLOAD_RETRY_BACKOFF = int(os.getenv("DOWNLOAD_RETRY_BACKOFF", "2"))
MAX_CONNECTION_PER_SERVER = int(os.getenv("MAX_CONNECTION_PER_SERVER", "5"))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(LOG_DIR, "app.log"),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

STATUS = {}
download_locks = {}
locks_lock = threading.Lock()

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri="memory://",
    default_limits=["200 per day", "50 per hour"],
)


def get_lock(imdb_id):
    with locks_lock:
        if imdb_id not in download_locks:
            download_locks[imdb_id] = threading.Lock()
        return download_locks[imdb_id]


def log_info(msg):
    logger.info(msg)


def log_error(msg):
    logger.error(msg)


TRACKERS = [
    "udp://glotorrents.pw:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://torrent.gresille.org:80/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://tracker.leechers-paradise.org:6969",
    "udp://p4p.arenabg.ch:1337",
    "udp://tracker.internetwarriors.net:1331",
]


def get_yts_torrent(imdb_id):
    try:
        url = f"{YTS_API_URL}/movie_details.json?imdb_id={imdb_id}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        movie_data = data.get("data", {}).get("movie", {})
        if not movie_data:
            return None
        torrents = movie_data.get("torrents", [])
        if not torrents:
            return None

        def torrent_sort_key(t):
            quality_order = {"1080p": 2, "720p": 1}
            return (-quality_order.get(t.get("quality", ""), 0), t.get("type") != "web")

        torrents.sort(key=torrent_sort_key)
        torrent = torrents[0]
        hash_str = torrent.get("hash", "")
        title = movie_data.get("title_long", movie_data.get("title", "movie"))
        dn = urllib.parse.quote(title)
        trackers = "&tr=".join(TRACKERS)
        magnet = f"magnet:?xt=urn:btih:{hash_str}&dn={dn}&tr={trackers}"
        return magnet
    except requests.RequestException as e:
        log_error(f"Request error fetching torrent: {e}")
        return None
    except (ValueError, KeyError) as e:
        log_error(f"Parse error fetching torrent: {e}")
        return None


def download_torrent(url, dest_dir, attempt=1):
    os.makedirs(dest_dir, exist_ok=True)
    cmd = [
        "aria2c",
        "--dir",
        dest_dir,
        "--seed-time=0",
        f"--max-connection-per-server={MAX_CONNECTION_PER_SERVER}",
        "--summary-interval=0",
        "--console-log-level=warn",
        url,
    ]
    log_info(f"⬇️  Starting aria2c (attempt {attempt}): {url}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        log_error(f"aria2c failed with code {result.returncode}")
    return result.returncode == 0


def download_torrent_with_retry(
    url,
    dest_dir,
    max_attempts=DOWNLOAD_RETRY_ATTEMPTS,
    backoff_base=DOWNLOAD_RETRY_BACKOFF,
):
    for attempt in range(1, max_attempts + 1):
        if download_torrent(url, dest_dir, attempt):
            return True
        if attempt < max_attempts:
            wait_time = backoff_base ** (attempt - 1)
            log_info(f"⬇️  Retrying in {wait_time}s...")
            time.sleep(wait_time)
    return False


def move_media(imdb_id, source_dir):
    dest_dir = os.path.join(MEDIA_DIR, imdb_id)
    os.makedirs(dest_dir, exist_ok=True)
    movie_file = None
    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.lower().endswith((".mp4", ".mkv")):
                movie_file = os.path.join(root, file)
                break
        if movie_file:
            break
    if movie_file:
        ext = os.path.splitext(movie_file)[-1]
        new_path = os.path.join(dest_dir, f"{imdb_id}{ext}")
        shutil.move(movie_file, new_path)
        shutil.rmtree(source_dir, ignore_errors=True)
        log_info(f"✅ Moved movie to: {new_path}")
        return new_path
    shutil.rmtree(source_dir, ignore_errors=True)
    return None


def download_worker(imdb_id, torrent_url):
    STATUS[imdb_id] = "downloading"
    temp_dir = os.path.join(TMP_DIR, imdb_id)
    try:
        if download_torrent_with_retry(torrent_url, temp_dir):
            final_path = move_media(imdb_id, temp_dir)
            if final_path and os.path.exists(final_path):
                STATUS[imdb_id] = "done"
                log_info(f"✅ Download complete: {imdb_id}")
            else:
                STATUS[imdb_id] = "error: media not found"
                log_error(f"❌ Media not found after download: {imdb_id}")
        else:
            STATUS[imdb_id] = "error: torrent failed"
            log_error(f"❌ Download failed after retries: {imdb_id}")
    except Exception as e:
        STATUS[imdb_id] = f"error: {e}"
        log_error(f"❌ Exception in download_worker: {e}")


@app.route("/")
@limiter.limit(f"{RATE_LIMIT_REQUESTS} per {RATE_LIMIT_WINDOW} second")
def serve_movie():
    imdb_id = request.args.get("id")
    if (
        not imdb_id
        or not imdb_id.startswith("tt")
        or not imdb_id[2:].isdigit()
        or not (7 <= len(imdb_id[2:]) <= 9)
    ):
        abort(
            400,
            description="Invalid IMDb ID format. Expected 'tt' followed by 7–9 digits.",
        )

    media_path_mp4 = os.path.join(MEDIA_DIR, imdb_id, f"{imdb_id}.mp4")
    media_path_mkv = os.path.join(MEDIA_DIR, imdb_id, f"{imdb_id}.mkv")
    media_path = media_path_mp4 if os.path.exists(media_path_mp4) else media_path_mkv

    if os.path.exists(media_path):
        log_info(f"📺 Serving {media_path}")
        return send_file(media_path, mimetype="video/mp4")

    lock = get_lock(imdb_id)
    with lock:
        if imdb_id in STATUS and STATUS[imdb_id] in ("downloading", "queued"):
            pass
        else:
            torrent_url = get_yts_torrent(imdb_id)
            if not torrent_url:
                abort(404, description="Movie not found on YTS.")
            thread = threading.Thread(
                target=download_worker, args=(imdb_id, torrent_url), daemon=True
            )
            thread.start()
            log_info(f"🚀 Started background download for {imdb_id}")
            STATUS[imdb_id] = "queued"

    return render_template_string(
        """
        <h1>Downloading {{ imdb_id }}...</h1>
        <p>Status: {{ status }}</p>
        <p>please wait.</p>
        <script>
            async function checkStatus() {
                const res = await fetch('/status?id={{ imdb_id }}');
                const data = await res.json();
                if (data.status === 'done') {
                    location.reload();
                } else {
                    document.querySelector('p:nth-of-type(1)').textContent = 'Status: ' + data.status;
                    setTimeout(checkStatus, 5000);
                }
            }
            checkStatus();
        </script>
    """,
        imdb_id=imdb_id,
        status=STATUS.get(imdb_id, "starting..."),
    )


@app.route("/status")
@limiter.limit(f"{RATE_LIMIT_REQUESTS} per {RATE_LIMIT_WINDOW} second")
def check_status():
    imdb_id = request.args.get("id")
    if not imdb_id:
        abort(400, description="Missing IMDb ID.")
    status = STATUS.get(imdb_id, "not found")
    return {"id": imdb_id, "status": status}


@app.route("/health")
def health_check():
    def get_dir_size(path):
        total = 0
        if os.path.exists(path):
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    total += os.path.getsize(fp)
        return total

    media_size = get_dir_size(MEDIA_DIR)
    total, _, _ = shutil.disk_usage(".")
    disk_used_percent = round((media_size / total) * 100, 1) if total > 0 else 0
    media_count = (
        len(
            [
                d
                for d in os.listdir(MEDIA_DIR)
                if os.path.isdir(os.path.join(MEDIA_DIR, d))
            ]
        )
        if os.path.exists(MEDIA_DIR)
        else 0
    )
    active_downloads = sum(1 for s in STATUS.values() if s in ("downloading", "queued"))
    return {
        "status": "healthy",
        "disk_used_percent": disk_used_percent,
        "media_count": media_count,
        "active_downloads": active_downloads,
    }


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8973"))
    log_info(f"🚀 Server starting on http://{host}:{port}")
    app.run(host=host, port=port, threaded=True)
