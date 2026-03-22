import os
import time
import requests
import urllib.parse
import threading
from flask import Blueprint, request, render_template, abort, jsonify, make_response
from config import MEDIA_DIR, TMP_DIR, YTS_API_URL, TRACKERS, TOP_250_URL, MOVIES_CACHE_TTL
from torrent import STATUS, download_worker
import logging

logger = logging.getLogger(__name__)
routes = Blueprint('routes', __name__)

MOVIES_CACHE = {"data": None, "timestamp": 0}

def is_valid_imdb_id(imdb_id):
    return bool(imdb_id and imdb_id.startswith("tt") and imdb_id[2:].isdigit())

def get_yts_torrent(imdb_id):
    try:
        url = f"{YTS_API_URL}/movie_details.json?imdb_id={imdb_id}"
        r = requests.get(url, timeout=10)
        data = r.json().get("data", {}).get("movie", {})
        torrents = data.get("torrents", [])
        if not torrents: return None
        torrents.sort(key=lambda t: (-({"1080p": 2, "720p": 1}.get(t.get("quality", ""), 0))))
        torrent = torrents[0]
        dn = urllib.parse.quote(data.get("title", "movie"))
        tr = "&tr=".join(TRACKERS)
        return f"magnet:?xt=urn:btih:{torrent['hash']}&dn={dn}&tr={tr}"
    except Exception:
        return None

def find_movie_file(imdb_id):
    """Checks both completed media folder and temporary download folder"""
    media_dir_path = os.path.join(MEDIA_DIR, imdb_id)
    tmp_dir_path = os.path.join(TMP_DIR, imdb_id)
    
    # Check completed folder
    if os.path.exists(media_dir_path):
        for f in os.listdir(media_dir_path):
            if f.endswith(('.mp4', '.mkv')):
                return 'media', os.path.join(imdb_id, f)
                
    # Check temporary folder
    if os.path.exists(tmp_dir_path):
        for root, _, files in os.walk(tmp_dir_path):
            for f in files:
                if f.endswith(('.mp4', '.mkv')):
                    rel_path = os.path.relpath(os.path.join(root, f), TMP_DIR)
                    return 'tmp', rel_path
    return None, None

def get_top_movies(force_refresh=False):
    current_time = time.time()
    if not force_refresh and MOVIES_CACHE["data"] and (current_time - MOVIES_CACHE["timestamp"]) < MOVIES_CACHE_TTL:
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
                processed.append({
                    "rank": i + 1,
                    "imdb_id": imdb_id,
                    "title": m.get("name", "Unknown"),
                    "year": m.get("year", ""),
                    "rating": m.get("rating", 0),
                    "poster": m.get("thumb_url", ""),
                    "genres": m.get("genre", []),
                    "description": m.get("desc", ""),
                })
        MOVIES_CACHE["data"] = processed
        MOVIES_CACHE["timestamp"] = current_time
        return processed
    except Exception as e:
        logger.error(f"Error fetching top movies: {e}")
        return MOVIES_CACHE["data"] or []

@routes.route("/")
def index():
    imdb_id = request.args.get("id")
    if imdb_id and is_valid_imdb_id(imdb_id):
        folder, filepath = find_movie_file(imdb_id)
        if folder:
            return render_template("player.html", imdb_id=imdb_id, movie_title=imdb_id)

        if imdb_id not in STATUS or STATUS[imdb_id] == 'error':
            torrent_url = get_yts_torrent(imdb_id)
            if not torrent_url:
                return render_template("download.html", imdb_id=imdb_id)
            threading.Thread(target=download_worker, args=(imdb_id, torrent_url), daemon=True).start()

        return render_template("download.html", imdb_id=imdb_id)
    return render_template("index.html")

@routes.route("/api/movies")
def api_movies():
    force = request.args.get("refresh", "").lower() == "true"
    movies = get_top_movies(force_refresh=force)
    return jsonify({"movies": movies, "count": len(movies)})

@routes.route("/player")
def player():
    imdb_id = request.args.get("id")
    folder, filepath = find_movie_file(imdb_id)
    
    # If file isn't ready yet, trap them back on the loading page
    if not folder:
        return render_template("download.html", imdb_id=imdb_id)
        
    return render_template("player.html", imdb_id=imdb_id, movie_title=imdb_id)

@routes.route("/watch")
def watch():
    imdb_id = request.args.get("id")
    folder, filepath = find_movie_file(imdb_id)
    
    if not folder:
        abort(404, description="Movie not found")

    response = make_response("")
    response.headers['Content-Type'] = 'video/mp4'
    
    # URL-encode the filepath to safely handle spaces and brackets
    safe_filepath = urllib.parse.quote(filepath)
    
    if folder == 'media':
        response.headers['X-Accel-Redirect'] = f"/media/{safe_filepath}"
    else:
        response.headers['X-Accel-Redirect'] = f"/tmp/{safe_filepath}"
        
    return response

# This is the function that went missing! 
@routes.route("/status")
def check_status():
    imdb_id = request.args.get("id")
    status = STATUS.get(imdb_id, "not found")
    return {"id": imdb_id, "status": status}