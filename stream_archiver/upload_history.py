"""Thread-safe upload history tracker backed by JSON file."""

import json
import os
import threading
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class UploadHistory:
    """Thread-safe upload history. Same JSON format as v1 for compatibility."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"uploaded": {}}

    def _save(self):
        """Must be called under self._lock."""
        try:
            with open(self.filepath, "w") as f:
                json.dump(self._data, f, indent=2)
        except IOError as e:
            logger.error(f"Could not save upload history: {e}")

    def is_uploaded(self, filepath: str) -> bool:
        filename = os.path.basename(filepath)
        with self._lock:
            return filename in self._data.get("uploaded", {})

    def mark_uploaded(self, filepath: str, video_id: str):
        filename = os.path.basename(filepath)
        with self._lock:
            self._data["uploaded"][filename] = {
                "video_id": video_id,
                "uploaded_at": datetime.now().isoformat(),
                "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            }
            self._save()

    def get_pending_files(self, download_folder: str) -> list[str]:
        """Get .mkv files in download folder that haven't been uploaded."""
        pending = []
        dl = Path(download_folder)
        if dl.exists():
            with self._lock:
                uploaded = self._data.get("uploaded", {})
                for fp in dl.glob("*.mkv"):
                    if fp.name not in uploaded:
                        pending.append(str(fp))
        return sorted(pending)
