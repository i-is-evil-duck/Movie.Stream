import os
import time
import shutil
import threading
import libtorrent as lt  # type: ignore
import logging
from config import TMP_DIR, MEDIA_DIR

logger = logging.getLogger(__name__)

# Libtorrent session
ses = lt.session()
ses.listen_on(6881, 6891)

STATUS = {}

def get_largest_video_file(torrent_info):
    """Finds the video file within the torrent to prioritize."""
    files = torrent_info.files()
    largest_file_idx = -1
    largest_size = 0
    for i in range(files.num_files()):
        size = files.file_size(i)
        if size > largest_size and files.file_path(i).lower().endswith(('.mp4', '.mkv', '.avi')):
            largest_size = size
            largest_file_idx = i
    return largest_file_idx

def download_worker(imdb_id, magnet_uri):
    temp_dir = os.path.join(TMP_DIR, imdb_id)
    os.makedirs(temp_dir, exist_ok=True)
    
    params = {
        'save_path': temp_dir,
        'storage_mode': lt.storage_mode_t(2),
    }
    handle = lt.add_magnet_uri(ses, magnet_uri, params)
    
    STATUS[imdb_id] = "queued"
    
    # Wait for metadata
    while not handle.has_metadata():
        time.sleep(1)
        
    torrent_info = handle.get_torrent_info()
    
    # Force sequential downloading for streaming
    handle.set_sequential_download(True)
    STATUS[imdb_id] = "downloading"
    
    video_idx = get_largest_video_file(torrent_info)
    
    # We allow early playback once ~2% of the file is downloaded sequentially
    early_playback_ready = False
    
    while not handle.status().is_seeding:
        s = handle.status()
        
        # If we have downloaded enough sequential pieces, mark it as ready for player
        if not early_playback_ready and s.progress >= 0.02:
            early_playback_ready = True
            STATUS[imdb_id] = "done" # Tell frontend it's ready to stream
            logger.info(f"{imdb_id} is ready for early playback")

        time.sleep(2)

    # Once fully downloaded, move to media folder
    if handle.status().is_seeding:
        STATUS[imdb_id] = "done"
        move_to_media(imdb_id, temp_dir)
        ses.remove_torrent(handle)

def move_to_media(imdb_id, source_dir):
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
        logger.info(f"Moved completed media to: {new_path}")