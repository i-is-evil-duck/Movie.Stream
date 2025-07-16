from flask import Flask, request, send_file, abort, render_template_string
import os, shutil, subprocess, requests, threading

app = Flask(__name__)
MEDIA_DIR = "media"
TMP_DIR = "tmp"
STATUS = {}

os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

def log(msg):
    print(msg, flush=True)

def get_yts_torrent(imdb_id):
    try:
        url = f"https://yts.mx/api/v2/list_movies.json?query_term={imdb_id}"
        r = requests.get(url, timeout=10)
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
    except Exception as e:
        log(f"❌ Error fetching torrent: {e}")
        return None

def download_torrent(url, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    cmd = [
        "aria2c", "--dir", dest_dir, "--seed-time=0",
        "--max-connection-per-server=5", "--summary-interval=0",
        "--console-log-level=warn", url
    ]
    log(f"⬇️  Starting aria2c: {url}")
    return subprocess.run(cmd).returncode == 0

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
        ext = os.path.splitext(movie_file)[-1]
        new_path = os.path.join(dest_dir, f"{imdb_id}{ext}")
        shutil.move(movie_file, new_path)
        shutil.rmtree(source_dir, ignore_errors=True)
        log(f"✅ Moved movie to: {new_path}")
        return new_path
    shutil.rmtree(source_dir, ignore_errors=True)
    return None

def download_worker(imdb_id, torrent_url):
    STATUS[imdb_id] = "downloading"
    temp_dir = os.path.join(TMP_DIR, imdb_id)
    try:
        if download_torrent(torrent_url, temp_dir):
            final_path = move_media(imdb_id, temp_dir)
            if final_path and os.path.exists(final_path):
                STATUS[imdb_id] = "done"
            else:
                STATUS[imdb_id] = "error: media not found"
        else:
            STATUS[imdb_id] = "error: torrent failed"
    except Exception as e:
        STATUS[imdb_id] = f"error: {e}"

@app.route('/')
def serve_movie():
    imdb_id = request.args.get('id')
    if not imdb_id or not imdb_id.startswith("tt") or not imdb_id[2:].isdigit() or not (7 <= len(imdb_id[2:]) <= 9):
        abort(400, description="Invalid IMDb ID format. Expected 'tt' followed by 7–9 digits.")

    media_path_mp4 = os.path.join(MEDIA_DIR, imdb_id, f"{imdb_id}.mp4")
    media_path_mkv = os.path.join(MEDIA_DIR, imdb_id, f"{imdb_id}.mkv")
    media_path = media_path_mp4 if os.path.exists(media_path_mp4) else media_path_mkv

    if os.path.exists(media_path):
        log(f"📺 Serving {media_path}")
        return send_file(media_path, mimetype="video/mp4")

    if imdb_id not in STATUS:
        torrent_url = get_yts_torrent(imdb_id)
        if not torrent_url:
            abort(404, description="Movie not found on YTS.")
        thread = threading.Thread(target=download_worker, args=(imdb_id, torrent_url), daemon=True)
        thread.start()
        log(f"🚀 Started background download for {imdb_id}")
        STATUS[imdb_id] = "queued"

    return render_template_string("""
        <h1>Downloading {{ imdb_id }}...</h1>
        <p>Status: {{ status }}</p>
        <p>please wait 2 mins.</p>
        <script>setTimeout(() => location.reload(), 120000);</script>
    """, imdb_id=imdb_id, status=STATUS.get(imdb_id, "starting..."))

@app.route('/status')
def check_status():
    imdb_id = request.args.get('id')
    status = STATUS.get(imdb_id, "not found")
    return {"id": imdb_id, "status": status}

if __name__ == '__main__':
    log("🚀 Server starting on http://127.0.0.1:8973")
    app.run(host='0.0.0.0', port=8973, threaded=True)
