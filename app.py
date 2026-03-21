import os
import logging
import threading
import time
import shutil
import subprocess
import requests
from functools import wraps
from flask import Flask, request, send_file, abort, render_template_string, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

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
MOVIES_CACHE_TTL = int(os.getenv("MOVIES_CACHE_TTL", "21600"))
TOP_250_URL = (
    "https://raw.githubusercontent.com/theapache64/top250/master/top250_min.json"
)

MOVIES_CACHE = {"data": None, "timestamp": 0}

MOVIE_GRID_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IMDb Top 250 Movies</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0f;
            color: #e5e5e5;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            min-height: 100vh;
        }
        .header {
            position: sticky;
            top: 0;
            z-index: 100;
            background: linear-gradient(to bottom, #0a0a0f 0%, #0a0a0f 80%, transparent 100%);
            padding: 20px 40px 40px;
        }
        .header-content {
            max-width: 1600px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 20px;
        }
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .logo svg { width: 40px; height: 40px; }
        .logo h1 {
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(135deg, #f5c518 0%, #ffc107 50%, #ff9800 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .search-container {
            position: relative;
            width: 100%;
            max-width: 400px;
        }
        .search-input {
            width: 100%;
            padding: 12px 20px 12px 45px;
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 12px;
            color: #fff;
            font-size: 15px;
            outline: none;
            transition: all 0.3s ease;
        }
        .search-input:focus {
            background: rgba(255,255,255,0.12);
            border-color: rgba(245, 197, 24, 0.5);
            box-shadow: 0 0 20px rgba(245, 197, 24, 0.15);
        }
        .search-input::placeholder { color: rgba(255,255,255,0.4); }
        .search-icon {
            position: absolute;
            left: 16px;
            top: 50%;
            transform: translateY(-50%);
            color: rgba(255,255,255,0.4);
        }
        .container {
            max-width: 1600px;
            margin: 0 auto;
            padding: 0 40px 60px;
        }
        .section-title {
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 24px;
            color: #fff;
        }
        .movies-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 24px;
        }
        @media (min-width: 1600px) { .movies-grid { grid-template-columns: repeat(6, 1fr); } }
        @media (max-width: 768px) { .movies-grid { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 16px; } .container { padding: 0 20px 40px; } .header { padding: 15px 20px 30px; } .header-content { justify-content: center; } }
        .movie-card {
            position: relative;
            border-radius: 12px;
            overflow: hidden;
            cursor: pointer;
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            background: #16161d;
        }
        .movie-card:hover {
            transform: scale(1.08) translateY(-8px);
            z-index: 10;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6), 0 0 60px rgba(245, 197, 24, 0.1);
        }
        .movie-poster {
            aspect-ratio: 2/3;
            width: 100%;
            object-fit: cover;
            display: block;
        }
        .movie-overlay {
            position: absolute;
            inset: 0;
            background: linear-gradient(to top, rgba(0,0,0,0.95) 0%, transparent 50%);
            opacity: 0;
            transition: opacity 0.3s ease;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            padding: 20px 15px 15px;
        }
        .movie-card:hover .movie-overlay { opacity: 1; }
        .movie-rank {
            position: absolute;
            top: 10px;
            left: 10px;
            background: rgba(0,0,0,0.85);
            color: #f5c518;
            font-weight: 700;
            font-size: 14px;
            padding: 4px 10px;
            border-radius: 6px;
            z-index: 5;
        }
        .movie-info { color: #fff; }
        .movie-title {
            font-size: 15px;
            font-weight: 600;
            line-height: 1.3;
            margin-bottom: 6px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .movie-meta {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 13px;
            color: rgba(255,255,255,0.7);
        }
        .movie-rating {
            display: flex;
            align-items: center;
            gap: 4px;
            color: #f5c518;
            font-weight: 500;
        }
        .movie-rating svg { width: 14px; height: 14px; }
        .movie-genres {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 10px;
        }
        .genre-tag {
            background: rgba(255,255,255,0.15);
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            color: rgba(255,255,255,0.8);
        }
        .download-hint {
            margin-top: 12px;
            background: linear-gradient(135deg, #f5c518, #ff9800);
            color: #000;
            font-weight: 600;
            font-size: 13px;
            padding: 8px 16px;
            border-radius: 8px;
            text-align: center;
        }
        .loading {
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 60vh;
            color: rgba(255,255,255,0.5);
        }
        .spinner {
            width: 48px;
            height: 48px;
            border: 3px solid rgba(255,255,255,0.1);
            border-top-color: #f5c518;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .hidden { display: none !important; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <div class="logo">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" stroke="#f5c518"/>
                </svg>
                <h1>IMDb Top 250</h1>
            </div>
            <div class="search-container">
                <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
                <input type="text" class="search-input" id="searchInput" placeholder="Search movies...">
            </div>
        </div>
    </div>
    <div class="container">
        <h2 class="section-title">Top Rated Movies of All Time</h2>
        <div class="movies-grid" id="moviesGrid">
            {% for movie in movies %}
            <div class="movie-card" data-imdb="{{ movie.imdb_id }}" data-title="{{ movie.title|lower }}" onclick="downloadMovie('{{ movie.imdb_id }}')">
                <span class="movie-rank">#{{ movie.rank }}</span>
                <img class="movie-poster" src="{{ movie.poster }}" alt="{{ movie.title }}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 150%22><rect fill=%22%2316161d%22 width=%22100%22 height=%22150%22/><text x=%2250%22 y=%2275%22 text-anchor=%22middle%22 fill=%22%23666%22 font-size=%2212%22>No Image</text></svg>'">
                <div class="movie-overlay">
                    <div class="movie-info">
                        <div class="movie-title">{{ movie.title }}</div>
                        <div class="movie-meta">
                            <span>{{ movie.year }}</span>
                            <span class="movie-rating">
                                <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                                {{ movie.rating }}
                            </span>
                        </div>
                        <div class="movie-genres">
                            {% for genre in movie.genres[:3] %}
                            <span class="genre-tag">{{ genre }}</span>
                            {% endfor %}
                        </div>
                        <div class="download-hint">Click to Download</div>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    <script>
        const searchInput = document.getElementById('searchInput');
        const cards = document.querySelectorAll('.movie-card');
        searchInput.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            cards.forEach(card => {
                const title = card.dataset.title;
                card.classList.toggle('hidden', !title.includes(query));
            });
        });
        function downloadMovie(imdbId) {
            window.location.href = '/?id=' + imdbId;
        }
    </script>
</body>
</html>
"""

DOWNLOAD_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Downloading... | Movie.Stream</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0f;
            color: #e5e5e5;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 40px 20px;
        }
        .container {
            text-align: center;
            max-width: 500px;
        }
        .icon {
            width: 80px;
            height: 80px;
            margin-bottom: 30px;
            animation: pulse 2s ease-in-out infinite;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); opacity: 0.8; }
            50% { transform: scale(1.1); opacity: 1; }
        }
        h1 { font-size: 24px; font-weight: 600; margin-bottom: 12px; color: #fff; }
        .status { font-size: 18px; color: #f5c518; margin-bottom: 8px; min-height: 28px; }
        .movie-id { font-size: 14px; color: rgba(255,255,255,0.5); margin-bottom: 30px; font-family: monospace; }
        .progress-bar {
            width: 100%;
            height: 6px;
            background: rgba(255,255,255,0.1);
            border-radius: 3px;
            overflow: hidden;
            margin-bottom: 20px;
        }
        .progress { height: 100%; background: linear-gradient(90deg, #f5c518, #ff9800); width: 0%; transition: width 0.5s ease; border-radius: 3px; }
        .spinner {
            width: 40px;
            height: 40px;
            border: 3px solid rgba(255,255,255,0.1);
            border-top-color: #f5c518;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .back-link {
            display: inline-block;
            margin-top: 20px;
            color: rgba(255,255,255,0.6);
            text-decoration: none;
            font-size: 14px;
            transition: color 0.2s;
        }
        .back-link:hover { color: #f5c518; }
        .done { color: #4caf50 !important; }
    </style>
</head>
<body>
    <div class="container">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#f5c518" stroke-width="2">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/>
        </svg>
        <h1>Downloading Movie</h1>
        <div class="status" id="status">{{ status }}</div>
        <div class="movie-id">{{ imdb_id }}</div>
        <div class="progress-bar"><div class="progress" id="progress"></div></div>
        <div class="spinner" id="spinner"></div>
        <a href="/" class="back-link">← Back to Movie List</a>
    </div>
    <script>
        const imdbId = '{{ imdb_id }}';
        const statusEl = document.getElementById('status');
        const progressEl = document.getElementById('progress');
        const spinnerEl = document.getElementById('spinner');
        async function checkStatus() {
            try {
                const res = await fetch('/status?id=' + imdbId);
                const data = await res.json();
                statusEl.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
                if (data.status === 'done') {
                    statusEl.classList.add('done');
                    spinnerEl.style.display = 'none';
                    progressEl.style.width = '100%';
                    window.location.href = '/?id=' + imdbId;
                } else if (data.status === 'error') {
                    spinnerEl.style.display = 'none';
                    statusEl.style.color = '#f44336';
                } else {
                    const states = {'queued': 20, 'downloading': 50};
                    progressEl.style.width = (states[data.status] || 10) + '%';
                }
            } catch (e) { console.error(e); }
            setTimeout(checkStatus, 3000);
        }
        checkStatus();
    </script>
</body>
</html>
"""

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


def get_yts_torrent(imdb_id):
    try:
        url = f"{YTS_API_URL}/list_movies.json?query_term={imdb_id}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        movies = data.get("data", {}).get("movies", [])
        if not movies:
            return None
        torrents = movies[0].get("torrents", [])

        def torrent_sort_key(t):
            quality_order = {"1080p": 2, "720p": 1}
            return (-quality_order.get(t["quality"], 0), t["type"] != "web")

        torrents.sort(key=torrent_sort_key)
        return torrents[0]["url"]
    except requests.RequestException as e:
        log_error(f"Request error fetching torrent: {e}")
        return None
    except (ValueError, KeyError) as e:
        log_error(f"Parse error fetching torrent: {e}")
        return None


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
        movies = r.json()
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
        log_info(f"✅ Loaded {len(processed)} movies from IMDb Top 250")
        return processed
    except requests.RequestException as e:
        log_error(f"Request error fetching top movies: {e}")
        return MOVIES_CACHE["data"] or []


def download_torrent(url, dest_dir, attempt=1):
    os.makedirs(dest_dir, exist_ok=True)

    import shutil as _shutil

    aria2_path = _shutil.which("aria2c") or "aria2c"

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
    log_info(f"⬇️  Starting aria2c (attempt {attempt}): {url}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        log_error(f"aria2c not found! Please install aria2: https://aria2.github.io/")
        return False
    if result.returncode != 0:
        log_error(f"aria2c failed with code {result.returncode}: {result.stderr}")
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
        movies = get_top_movies()
        movies_json = jsonify({"movies": movies}).get_data(as_text=True)
        return render_template_string(
            MOVIE_GRID_TEMPLATE, movies=movies, movies_json=movies_json
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
        DOWNLOAD_PAGE_TEMPLATE,
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
    debug = os.getenv("DEBUG", "false").lower() == "true"
    log_info(f"🚀 Server starting on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)
