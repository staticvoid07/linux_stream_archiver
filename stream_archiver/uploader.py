"""Dedicated upload worker thread with queue. Uploads one file at a time."""

import gc
import os
import queue
import pickle
import logging
import threading
from dataclasses import dataclass
from datetime import datetime

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from .helpers import format_bytes

logger = logging.getLogger(__name__)


@dataclass
class UploadJob:
    """Represents a file queued for upload."""
    filepath: str
    channel_name: str
    stream_title: str
    game_name: str
    recorded_at: str
    playlist_id: str


class UploadWorker:
    """Single daemon thread that uploads files from a queue to YouTube."""

    def __init__(self, config, upload_history, notifier,
                 shutdown_event: threading.Event):
        self.config = config
        self.history = upload_history
        self.notifier = notifier
        self.shutdown_event = shutdown_event
        self.queue = queue.Queue()

        # Observable state for status endpoint
        self.current_job: UploadJob | None = None
        self.progress: float = 0.0  # 0.0 to 1.0

        self._thread = threading.Thread(
            target=self._run, daemon=True, name="upload-worker"
        )

    def start(self):
        self._thread.start()

    def enqueue(self, job: UploadJob):
        """Add a file to the upload queue. Thread-safe."""
        logger.info(f"Queued for upload: {os.path.basename(job.filepath)}")
        self.queue.put(job)

    @property
    def queue_size(self) -> int:
        return self.queue.qsize()

    @property
    def is_uploading(self) -> bool:
        return self.current_job is not None

    def _get_credentials(self):
        """Get or refresh YouTube API credentials. Returns None if unavailable."""
        creds = None
        token_file = self.config.youtube_token_file

        if os.path.exists(token_file):
            with open(token_file, "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    with open(token_file, "wb") as f:
                        pickle.dump(creds, f)
                    return creds
                except Exception:
                    logger.warning("YouTube token refresh failed")
                    os.remove(token_file)
                    creds = None

            if not creds:
                logger.error(
                    "No valid YouTube token. "
                    "Run: python -m stream_archiver --auth-youtube"
                )
                return None

        return creds

    def _upload_one(self, job: UploadJob):
        """Upload a single file to YouTube."""
        self.current_job = job
        self.progress = 0.0
        filepath = job.filepath
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)

        logger.info(f"Uploading: {filename} ({format_bytes(file_size)})")
        self.notifier.send(
            "Upload Starting",
            f"{job.channel_name}: {filename[:40]}...\nSize: {format_bytes(file_size)}",
            "upload_start", channel_name=job.channel_name,
        )

        creds = self._get_credentials()
        if not creds:
            self.notifier.send("Upload Failed", "No YouTube credentials", "upload_fail")
            self.current_job = None
            return

        try:
            youtube = build("youtube", "v3", credentials=creds)

            # Build a human-friendly title: "Stream Title - Channel (Jan 15, 2026)"
            date_str = ""
            if job.recorded_at:
                try:
                    dt = datetime.fromisoformat(job.recorded_at)
                    date_str = dt.strftime("%b %d, %Y")
                except ValueError:
                    date_str = ""
            if job.stream_title:
                title = f"{job.stream_title} - {job.channel_name}"
            else:
                title = f"{job.game_name} - {job.channel_name}"
            if date_str:
                title = f"{title} ({date_str})"
            title = title[:100]

            description = (
                f"Twitch stream from {job.channel_name}\n"
                f"Game: {job.game_name}\n"
                f"Title: {job.stream_title}"
            )

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": self.config.youtube_category,
                },
                "status": {
                    "privacyStatus": self.config.youtube_privacy,
                    "selfDeclaredMadeForKids": False,
                },
            }

            media = MediaFileUpload(
                filepath, mimetype="video/x-matroska",
                resumable=True, chunksize=10 * 1024 * 1024,
            )

            request = youtube.videos().insert(
                part=",".join(body.keys()), body=body, media_body=media,
            )

            response = None
            while response is None:
                if self.shutdown_event.is_set():
                    logger.info("Upload cancelled due to shutdown")
                    del media, request
                    gc.collect()
                    return
                status, response = request.next_chunk()
                if status:
                    self.progress = status.progress()

            video_id = response["id"]
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info(f"Upload completed: {filename} -> {video_url}")

            self.notifier.send(
                "Upload Complete",
                f"{job.channel_name}: {video_url}",
                "upload_end", channel_name=job.channel_name,
                launch_url=video_url,
            )

            self.history.mark_uploaded(filepath, video_id)

            if job.playlist_id:
                self._add_to_playlist(youtube, video_id, job.playlist_id)

            # Release file handles then delete
            del media, request
            gc.collect()
            self._delete_file(filepath)

        except HttpError as e:
            logger.error(f"YouTube API error uploading {filename}: {e}")
            self.notifier.send(
                "Upload Failed", f"{job.channel_name}: API error",
                "upload_fail", channel_name=job.channel_name,
            )
        except Exception as e:
            logger.error(f"Error uploading {filename}: {e}", exc_info=True)
            self.notifier.send(
                "Upload Failed", f"{job.channel_name}: {str(e)[:50]}",
                "upload_fail", channel_name=job.channel_name,
            )
        finally:
            self.current_job = None
            self.progress = 0.0

    def _add_to_playlist(self, youtube, video_id: str, playlist_id: str):
        """Add uploaded video to a YouTube playlist."""
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    }
                },
            ).execute()
            logger.info(f"Added {video_id} to playlist {playlist_id}")
        except HttpError as e:
            logger.error(f"Error adding to playlist: {e}")

    def _delete_file(self, filepath: str):
        """Delete the recording file after successful upload."""
        filename = os.path.basename(filepath)
        try:
            os.remove(filepath)
            logger.info(f"Deleted: {filename}")
        except OSError as e:
            logger.error(f"Could not delete {filename}: {e}")

    def _run(self):
        """Main loop: pull jobs from queue, upload one at a time."""
        logger.info("Upload worker started")
        while not self.shutdown_event.is_set():
            try:
                job = self.queue.get(timeout=2)
            except queue.Empty:
                continue

            # Skip if already uploaded
            if self.history.is_uploaded(job.filepath):
                logger.info(f"Already uploaded, skipping: {os.path.basename(job.filepath)}")
                continue

            # Skip if file missing or empty
            if not os.path.exists(job.filepath) or os.path.getsize(job.filepath) == 0:
                logger.warning(f"File missing or empty: {job.filepath}")
                continue

            self._upload_one(job)

        logger.info("Upload worker stopped")
