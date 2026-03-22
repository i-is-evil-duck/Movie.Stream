import os
import time
import shutil
import threading
import logging
from config import MEDIA_DIR, TMP_DIR

logger = logging.getLogger(__name__)

def get_newest_mtime(folder_path):
    """Finds the most recent modification time of any file inside the folder."""
    newest = os.path.getmtime(folder_path)
    for root, _, files in os.walk(folder_path):
        for f in files:
            mtime = os.path.getmtime(os.path.join(root, f))
            if mtime > newest:
                newest = mtime
    return newest

def _cleanup_loop():
    while True:
        now = time.time()
        # 24 hours * 60 minutes * 60 seconds = 86400 seconds
        cutoff = now - 86400 

        for directory in [MEDIA_DIR, TMP_DIR]:
            if not os.path.exists(directory):
                continue
            
            for folder_name in os.listdir(directory):
                folder_path = os.path.join(directory, folder_name)
                if not os.path.isdir(folder_path):
                    continue
                    
                # Check how old the movie is
                mtime = get_newest_mtime(folder_path)
                
                if mtime < cutoff:
                    try:
                        shutil.rmtree(folder_path)
                        logger.info(f"Janitor auto-deleted expired movie: {folder_name}")
                    except Exception as e:
                        logger.error(f"Janitor failed to delete {folder_name}: {e}")
                        
        # Go to sleep for 1 hour before checking again
        time.sleep(3600)

def start_cleanup_thread():
    """Spawns the janitor as a background daemon thread."""
    thread = threading.Thread(target=_cleanup_loop, daemon=True)
    thread.start()