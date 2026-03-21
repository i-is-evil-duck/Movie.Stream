import os
import logging
import threading
import time
import shutil
import subprocess
import requests
from flask import Flask, request, send_file, abort, render_template, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

MEDIA_DIR = os.getenv("MEDIA_DIR", "media")
TMP_DIR = os.getenv("TMP_DIR", "tmp")
LOG_DIR = os.getenv("LOG_DIR", "logs")
YTS_API_URL = os.getenv("YTS_API_URL", "https://movies-api.accel.li/api/v2")
DOWNLOAD_RETRY_ATTEMPTS = int(os.getenv("DOWNLOAD_RETRY_ATTEMPTS", "3"))
DOWNLOAD_RETRY_BACKOFF = int(os.getenv("DOWNLOAD_RETRY_BACKOFF", "2"))
MAX_CONNECTION_PER_SERVER = int(os.getenv("MAX_CONNECTION_PER_SERVER", "5"))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
MOVIES_CACHE_TTL = int(os.getenv("MOVIES_CACHE_TTL", "21600"))
TOP_250_URL = (
    "https://raw.githubusercontent.com/theapache64/top250/master/top250_min.json"
)

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
MOVIES_CACHE = {"data": None, "timestamp": 0}

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


def get_yts_torrent(imdb_id):
    try:
        url = f"{YTS_API_URL}/list_movies.json?query_term={imdb_id}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()

        try:
            data = r.json()
        except ValueError as e:
            log_error(f"JSON parse error: {e}")
            return None

        if data.get("status") != "ok":
            log_error(f"YTS API error: {data.get('status_message', 'Unknown error')}")
            return None

        movies = data.get("data", {}).get("movies", [])
        if not movies:
            log_error(f"No movies found for {imdb_id}")
            return None

        torrents = movies[0].get("torrents", [])
        if not torrents:
            log_error(f"No torrents available for {imdb_id}")
            return None

        def torrent_sort_key(t):
            quality_order = {"1080p": 2, "720p": 1}
            return (-quality_order.get(t.get("quality", ""), 0), t.get("type") != "web")

        torrents.sort(key=torrent_sort_key)
        return torrents[0].get("url")
    except requests.RequestException as e:
        log_error(f"Request error fetching torrent: {e}")
        return None
    except (ValueError, KeyError) as e:
        log_error(f"Parse error fetching torrent: {e}")
        return None


def download_torrent(url, dest_dir, attempt=1):
    os.makedirs(dest_dir, exist_ok=True)

    aria2_path = shutil.which("aria2c") or "aria2c"

    cmd = [
        aria2_path,
        "--dir",
        dest_dir,
        "--seed-time=0",
        f"--max-connection-per-server={MAX_CONNECTION_PER_SERVER}",
        "--summary-interval=0",
        "--console-log-level=warn",
        url,
    ]
    log_info(f"Downloading (attempt {attempt}): {url}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        log_error("aria2c not found! Please install aria2")
        return False
    if result.returncode != 0:
        log_error(f"aria2c failed: {result.stderr}")
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
            log_info(f"Retrying in {wait_time}s...")
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
        log_info(f"Movie saved: {new_path}")
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
                log_info(f"Download complete: {imdb_id}")
            else:
                STATUS[imdb_id] = "error: media not found"
                log_error(f"Media not found after download: {imdb_id}")
        else:
            STATUS[imdb_id] = "error: torrent failed"
            log_error(f"Download failed after retries: {imdb_id}")
    except Exception as e:
        STATUS[imdb_id] = f"error: {e}"
        log_error(f"Exception in download_worker: {e}")


def get_top_movies(force_refresh=False):
    current_time = time.time()
    if (
        not force_refresh
        and MOVIES_CACHE["data"]
        and (current_time - MOVIES_CACHE["timestamp"]) < MOVIES_CACHE_TTL
    ):
        return MOVIES_CACHE["data"]

    try:
        r = requests.get(TOP_250_URL, timeout=15)
        r.raise_for_status()

        try:
            movies = r.json()
        except ValueError as e:
            log_error(f"Failed to parse top250 JSON: {e}")
            return MOVIES_CACHE["data"] or []

        processed = []
        for i, m in enumerate(movies):
            imdb_url = m.get("imdb_url", "")
            imdb_id = imdb_url.replace("/title/", "").rstrip("/") if imdb_url else None
            if imdb_id and imdb_id.startswith("tt"):
                processed.append(
                    {
                        "rank": i + 1,
                        "imdb_id": imdb_id,
                        "title": m.get("name", "Unknown"),
                        "year": m.get("year", ""),
                        "rating": m.get("rating", 0),
                        "poster": m.get("thumb_url", ""),
                        "genres": m.get("genre", []),
                        "description": m.get("desc", ""),
                    }
                )
        MOVIES_CACHE["data"] = processed
        MOVIES_CACHE["timestamp"] = current_time
        log_info(f"Loaded {len(processed)} movies from IMDb Top 250")
        return processed
    except requests.RequestException as e:
        log_error(f"Error fetching top movies: {e}")
        return MOVIES_CACHE["data"] or []


@app.route("/")
@limiter.limit(f"{RATE_LIMIT_REQUESTS} per {RATE_LIMIT_WINDOW} second")
def serve_movie():
    imdb_id = request.args.get("id")
    raw_mode = request.args.get("raw", "").lower() == "true"

    if not imdb_id:
        return render_template("index.html")

    if not (
        imdb_id.startswith("tt")
        and imdb_id[2:].isdigit()
        and 7 <= len(imdb_id[2:]) <= 9
    ):
        return render_template("index.html", error="Invalid IMDb ID format")

    media_path_mp4 = os.path.join(MEDIA_DIR, imdb_id, f"{imdb_id}.mp4")
    media_path_mkv = os.path.join(MEDIA_DIR, imdb_id, f"{imdb_id}.mkv")
    media_path = media_path_mp4 if os.path.exists(media_path_mp4) else media_path_mkv

    if os.path.exists(media_path):
        log_info(f"Serving: {media_path}")

        if raw_mode:
            return send_file(media_path, mimetype="video/mp4")

        movie_title = imdb_id
        for m in get_top_movies():
            if m["imdb_id"] == imdb_id:
                movie_title = m.get("title", imdb_id)
                break

        return render_template("player.html", imdb_id=imdb_id, movie_title=movie_title)

    lock = get_lock(imdb_id)
    with lock:
        if imdb_id in STATUS and STATUS[imdb_id] in ("downloading", "queued"):
            pass
        else:
            torrent_url = get_yts_torrent(imdb_id)
            if not torrent_url:
                log_error(f"Movie not found on YTS: {imdb_id}")
                return render_template(
                    "download.html", imdb_id=imdb_id, error="Movie not found on YTS"
                )
            thread = threading.Thread(
                target=download_worker, args=(imdb_id, torrent_url), daemon=True
            )
            thread.start()
            log_info(f"Started download: {imdb_id}")
            STATUS[imdb_id] = "queued"

    return render_template("download.html", imdb_id=imdb_id)


@app.route("/status")
@limiter.limit(f"{RATE_LIMIT_REQUESTS} per {RATE_LIMIT_WINDOW} second")
def check_status():
    imdb_id = request.args.get("id")
    if not imdb_id:
        abort(400, description="Missing IMDb ID.")
    status = STATUS.get(imdb_id, "not found")
    return {"id": imdb_id, "status": status}


@app.route("/api/movies")
@limiter.limit(f"{RATE_LIMIT_REQUESTS} per {RATE_LIMIT_WINDOW} second")
def api_movies():
    force = request.args.get("refresh", "").lower() == "true"
    movies = get_top_movies(force_refresh=force)
    return jsonify({"movies": movies, "count": len(movies)})


@app.route("/health")
def health_check():
    def get_dir_size(path):
        total = 0
        if os.path.exists(path):
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
        return total

    media_size = get_dir_size(MEDIA_DIR)
    try:
        total, _, _ = shutil.disk_usage(".")
        disk_used_percent = round((media_size / total) * 100, 1) if total > 0 else 0
    except OSError:
        disk_used_percent = 0

    media_count = 0
    if os.path.exists(MEDIA_DIR):
        try:
            media_count = len(
                [
                    d
                    for d in os.listdir(MEDIA_DIR)
                    if os.path.isdir(os.path.join(MEDIA_DIR, d))
                ]
            )
        except OSError:
            media_count = 0

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
    debug = os.getenv("DEBUG", "false").lower() == "true"
    log_info(f"Server starting on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)
