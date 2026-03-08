"""Channel monitors and orchestrator. One monitor thread per channel."""

import json
import os
import time
import logging
import threading
from dataclasses import dataclass
from datetime import datetime

from .twitch_api import TwitchAPI
from .recorder import StreamRecorder
from .uploader import UploadJob, UploadWorker
from .upload_history import UploadHistory
from .notifications import Notifier
from .helpers import format_bytes, format_time

logger = logging.getLogger(__name__)


@dataclass
class ChannelStatus:
    """Observable status for one channel."""
    channel_name: str
    state: str = "idle"          # "idle", "recording", "offline"
    detail: str = "Starting..."
    game_name: str = ""
    stream_title: str = ""
    recording_elapsed: float = 0.0
    recording_file_size: int = 0
    viewer_count: int = 0


class ChannelMonitor:
    """Monitors one Twitch channel in its own thread."""

    def __init__(self, channel_config, app_config, twitch_api: TwitchAPI,
                 upload_queue, notifier: Notifier,
                 shutdown_event: threading.Event):
        self.ch_config = channel_config
        self.app_config = app_config
        self.twitch_api = twitch_api
        self.upload_queue = upload_queue
        self.notifier = notifier
        self.shutdown_event = shutdown_event

        self.status = ChannelStatus(channel_name=channel_config.name)
        self._recorder: StreamRecorder | None = None
        self._profile_image_url: str | None = None

        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=f"monitor-{channel_config.name}",
        )

    def start(self):
        self._thread.start()

    def _fetch_profile_image(self):
        """One-time fetch of channel profile image for notifications."""
        if self._profile_image_url is None:
            try:
                user = self.twitch_api.get_user_info(self.ch_config.name)
                if user:
                    self._profile_image_url = user.get("profile_image_url", "")
                else:
                    self._profile_image_url = ""
            except Exception:
                self._profile_image_url = ""

    def _run(self):
        name = self.ch_config.name
        logger.info(f"[{name}] Channel monitor started")
        self._fetch_profile_image()

        while not self.shutdown_event.is_set():
            try:
                if self._recorder and self._recorder.is_running:
                    # Currently recording - monitor health
                    self._update_recording_status()

                    healthy = self._recorder.check_health(
                        self._check_still_live
                    )

                    if not healthy or not self._recorder.is_running:
                        self._handle_recording_ended()

                    self._sleep(1)
                else:
                    if self._recorder:
                        # Process just exited naturally
                        self._handle_recording_ended()
                        continue

                    # Not recording - check if stream is live
                    self.status.state = "idle"
                    self.status.detail = "Checking..."

                    try:
                        stream_info = self.twitch_api.get_stream_info(name)
                    except Exception as e:
                        logger.error(f"[{name}] Error checking stream: {e}")
                        self.status.detail = "API error"
                        self._sleep(10)
                        continue

                    if stream_info:
                        self._start_recording(stream_info)
                    else:
                        self.status.state = "offline"
                        self.status.detail = "Offline"
                        self._sleep(self.ch_config.check_interval)

            except Exception as e:
                logger.error(f"[{name}] Error in monitor loop: {e}", exc_info=True)
                self.status.detail = f"Error: {str(e)[:30]}"
                self._sleep(10)

        # Shutdown: stop any active recording
        if self._recorder and self._recorder.is_running:
            self._recorder.stop("application shutdown")
            self._handle_recording_ended()

        logger.info(f"[{name}] Channel monitor stopped")

    def _sleep(self, seconds: float):
        """Sleep in 1-second increments, checking shutdown_event."""
        for _ in range(int(seconds)):
            if self.shutdown_event.is_set():
                return
            time.sleep(1)

    def _start_recording(self, stream_info: dict):
        name = self.ch_config.name
        game = stream_info.get("game_name", "Unknown")
        title = stream_info.get("title", "")
        viewers = stream_info.get("viewer_count", 0)

        self.notifier.send_stream_online(
            name, stream_info, self._profile_image_url
        )

        self._recorder = StreamRecorder(
            channel_name=name,
            stream_info=stream_info,
            download_folder=self.app_config.download_folder,
            quality=self.ch_config.quality,
        )
        self._recorder.start()

        self.status.state = "recording"
        self.status.game_name = game
        self.status.stream_title = title
        self.status.viewer_count = viewers
        self.status.detail = f"Recording: {game}"

        self.notifier.send(
            "Recording Started", f"{name}: {game}",
            "recording_start", channel_name=name,
        )

    def _update_recording_status(self):
        if self._recorder:
            self.status.recording_elapsed = self._recorder.elapsed
            self.status.recording_file_size = self._recorder.file_size

    def _check_still_live(self) -> bool:
        """Liveness callback for health checks. Also refreshes status."""
        info = self.twitch_api.get_stream_info(self.ch_config.name)
        if info:
            self.status.viewer_count = info.get("viewer_count", 0)
            self.status.game_name = info.get("game_name", "")
            self.status.stream_title = info.get("title", "")
        return info is not None

    def _handle_recording_ended(self):
        name = self.ch_config.name
        recorder = self._recorder
        self._recorder = None

        if not recorder:
            return

        recorder.collect_exit()

        self.status.state = "idle"
        self.status.detail = "Recording ended"
        self.status.recording_elapsed = 0
        self.status.recording_file_size = 0

        filepath = recorder.filepath
        if filepath and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            file_size = os.path.getsize(filepath)
            duration = format_time(recorder.elapsed)

            logger.info(f"[{name}] Recording finished: {os.path.basename(filepath)} "
                        f"(duration: {duration}, size: {format_bytes(file_size)})")

            self.notifier.send(
                "Recording Finished",
                f"{name}: {duration}, {format_bytes(file_size)}",
                "recording_end", channel_name=name,
            )

            # Enqueue for upload - does NOT block this thread
            job = UploadJob(
                filepath=filepath,
                channel_name=name,
                stream_title=recorder.stream_info.get("title", ""),
                game_name=recorder.stream_info.get("game_name", "Unknown"),
                recorded_at=datetime.now().isoformat(),
                playlist_id=self.ch_config.playlist_id,
            )
            self.upload_queue.put(job)
        else:
            # Short/empty recordings after a stream ends are normal (Twitch API
            # briefly reports the stream as live after it actually ended).
            # Only treat as a real error if the recording ran for a while.
            if recorder.elapsed > 30:
                logger.warning(f"[{name}] Recording file missing or empty "
                               f"after {format_time(recorder.elapsed)}")
                self.notifier.send(
                    "Recording Error", f"{name}: File empty or not found",
                    "error", channel_name=name,
                )
            else:
                logger.info(f"[{name}] Short/empty recording discarded "
                            f"(stream likely ended)")
                self.status.state = "offline"
                self.status.detail = "Stream ended"
                # Clean up the empty file if it exists
                if filepath and os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
                # Wait before rechecking to avoid rapid retry loop
                self._sleep(self.ch_config.check_interval)


class MonitorOrchestrator:
    """Creates and manages all channel monitors + the upload worker."""

    def __init__(self, config):
        self.config = config
        self.shutdown_event = threading.Event()
        self._status_server = None

        # Shared resources
        self.twitch_api = TwitchAPI(
            config.twitch_client_id, config.twitch_client_secret
        )
        self.upload_history = UploadHistory(config.upload_history_file)
        self.notifier = Notifier(config)
        self.upload_worker = UploadWorker(
            config, self.upload_history, self.notifier, self.shutdown_event
        )

        # One ChannelMonitor per configured channel
        self.channel_monitors: list[ChannelMonitor] = []
        for ch_config in config.channels:
            mon = ChannelMonitor(
                ch_config, config, self.twitch_api,
                self.upload_worker.queue, self.notifier, self.shutdown_event,
            )
            self.channel_monitors.append(mon)

    def _build_status_dict(self) -> dict:
        """Build a status dict for external consumption."""
        channels = []
        for mon in self.channel_monitors:
            s = mon.status
            ch = {
                "name": s.channel_name,
                "state": s.state,
                "detail": s.detail,
                "game": s.game_name,
                "stream_title": s.stream_title,
                "viewer_count": s.viewer_count,
                "quality": mon.ch_config.quality,
                "playlist_id": mon.ch_config.playlist_id,
                "recording_duration_seconds": round(s.recording_elapsed),
                "recording_duration": format_time(s.recording_elapsed) if s.recording_elapsed else "",
                "recording_size_bytes": s.recording_file_size,
                "recording_size": format_bytes(s.recording_file_size) if s.recording_file_size else "",
            }
            channels.append(ch)

        upload = {
            "is_uploading": self.upload_worker.is_uploading,
            "progress": round(self.upload_worker.progress * 100, 1),
            "queue_size": self.upload_worker.queue_size,
            "current_file": "",
            "current_channel": "",
        }
        if self.upload_worker.current_job:
            job = self.upload_worker.current_job
            upload["current_file"] = os.path.basename(job.filepath)
            upload["current_channel"] = job.channel_name

        return {
            "running": True,
            "pid": os.getpid(),
            "last_updated": datetime.now().isoformat(),
            "channels": channels,
            "upload": upload,
        }

    def _status_writer_loop(self):
        """Background thread that writes status.json every 5 seconds."""
        status_path = self.config.status_file
        while not self.shutdown_event.is_set():
            try:
                data = self._build_status_dict()
                tmp = status_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, status_path)
            except Exception:
                pass
            time.sleep(5)

        # Mark as not running on shutdown
        try:
            data = {"running": False, "last_updated": datetime.now().isoformat(),
                    "channels": [], "upload": {}}
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def start_all(self):
        """Start upload worker, channel monitors, status writer, and status server."""
        # Build lookup for channel configs
        channel_configs = {ch.name: ch for ch in self.config.channels}

        # Enqueue pending files from previous runs
        for filepath in self.upload_history.get_pending_files(
            self.config.download_folder
        ):
            basename = os.path.basename(filepath)
            name_no_ext = basename.rsplit(".", 1)[0]
            # Filename format: {channel}_{YYYYMMDD}_{HHMMSS}_{title}
            parts = name_no_ext.split("_", 3)

            channel = parts[0] if len(parts) >= 1 else "unknown"

            recorded_at = ""
            if len(parts) >= 3:
                try:
                    dt = datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
                    recorded_at = dt.isoformat()
                except ValueError:
                    pass

            stream_title = parts[3] if len(parts) >= 4 else ""

            ch_config = channel_configs.get(channel)
            playlist_id = ch_config.playlist_id if ch_config else ""

            job = UploadJob(
                filepath=filepath, channel_name=channel,
                stream_title=stream_title, game_name="",
                recorded_at=recorded_at, playlist_id=playlist_id,
            )
            self.upload_worker.enqueue(job)

        self.upload_worker.start()
        for mon in self.channel_monitors:
            mon.start()

        # Status file writer for external consumers (e.g. monitoring scripts)
        threading.Thread(
            target=self._status_writer_loop, daemon=True,
            name="status-writer",
        ).start()

        # HTTP status server
        if self.config.status_server_enabled:
            from .status_server import StatusServer
            self._status_server = StatusServer(
                self,
                self.config.status_server_host,
                self.config.status_server_port,
            )
            self._status_server.start()

        logger.info(f"Started monitoring {len(self.channel_monitors)} channel(s): "
                     f"{', '.join(c.name for c in self.config.channels)}")

    def shutdown(self):
        """Signal all threads to stop."""
        logger.info("Shutdown requested")
        self.shutdown_event.set()
        if self._status_server:
            self._status_server.stop()
        time.sleep(2)
