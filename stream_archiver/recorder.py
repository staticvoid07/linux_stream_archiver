"""Manages one streamlink recording subprocess."""

import os
import time
import subprocess
import logging
from pathlib import Path
from datetime import datetime

from .helpers import format_time

logger = logging.getLogger(__name__)


class StreamRecorder:
    """Manages one streamlink recording session."""

    LIVENESS_CHECK_INTERVAL = 120   # Check if stream still live every 2 min
    OFFLINE_GRACE_CHECKS = 3        # Need 3 consecutive offline checks
    STALE_TIMEOUT = 300             # File not growing for 5 min = stale

    def __init__(self, channel_name: str, stream_info: dict,
                 download_folder: str, quality: str):
        self.channel_name = channel_name
        self.stream_info = stream_info
        self.download_folder = download_folder
        self.quality = quality

        self.process = None
        self.filepath = None
        self.start_time = None

        # Health tracking
        self._last_liveness_check = 0
        self._consecutive_offline = 0
        self._last_file_size_change = 0
        self._last_known_file_size = 0

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def elapsed(self) -> float:
        if self.start_time:
            return time.time() - self.start_time
        return 0

    @property
    def file_size(self) -> int:
        if self.filepath and os.path.exists(self.filepath):
            try:
                return os.path.getsize(self.filepath)
            except OSError:
                return 0
        return 0

    def start(self) -> str:
        """Start recording. Returns the filepath being recorded to."""
        Path(self.download_folder).mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stream_title = self.stream_info.get("title", "stream")[:50]
        # Sanitize characters illegal in Linux filenames
        for ch in '/\x00':
            stream_title = stream_title.replace(ch, "")
        stream_title = stream_title.replace(":", " -")
        filename = f"{self.channel_name}_{timestamp}_{stream_title}.mkv"
        self.filepath = os.path.join(self.download_folder, filename)

        cmd = [
            "streamlink",
            "--twitch-disable-ads",
            "--twitch-low-latency",
            "-o", self.filepath,
            f"https://www.twitch.tv/{self.channel_name}",
            self.quality,
        ]

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.start_time = time.time()
        self._last_liveness_check = time.time()
        self._last_file_size_change = time.time()

        logger.info(f"[{self.channel_name}] Recording started: {filename} "
                     f"(PID: {self.process.pid})")
        return self.filepath

    def check_health(self, is_stream_live_fn) -> bool:
        """Check recording health. Returns False if recording should stop.

        Args:
            is_stream_live_fn: callable returning bool (is stream still live?)
        """
        if not self.is_running:
            return False

        now = time.time()

        # Stale file check
        current_size = self.file_size
        if current_size != self._last_known_file_size:
            self._last_known_file_size = current_size
            self._last_file_size_change = now
        elif current_size > 0 and now - self._last_file_size_change > self.STALE_TIMEOUT:
            logger.warning(f"[{self.channel_name}] Recording stale: file unchanged for "
                           f"{format_time(now - self._last_file_size_change)}")
            self.stop("stale recording (file stopped growing)")
            return False

        # Periodic liveness check
        if now - self._last_liveness_check >= self.LIVENESS_CHECK_INTERVAL:
            self._last_liveness_check = now
            try:
                if not is_stream_live_fn():
                    self._consecutive_offline += 1
                    logger.info(f"[{self.channel_name}] Stream appears offline "
                                f"({self._consecutive_offline}/{self.OFFLINE_GRACE_CHECKS})")
                    if self._consecutive_offline >= self.OFFLINE_GRACE_CHECKS:
                        logger.warning(f"[{self.channel_name}] Stream confirmed offline, stopping")
                        self.stop("stream went offline")
                        return False
                else:
                    if self._consecutive_offline > 0:
                        logger.info(f"[{self.channel_name}] Stream back online")
                    self._consecutive_offline = 0
            except Exception:
                pass  # Network error, don't count as offline

        return True

    def stop(self, reason: str = "unknown"):
        """Stop the recording gracefully."""
        if not self.process:
            return

        logger.info(f"[{self.channel_name}] Stopping recording: {reason}")
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                logger.warning(f"[{self.channel_name}] Recording didn't stop, killing")
                self.process.kill()
                self.process.wait(timeout=5)
        except Exception as e:
            logger.error(f"[{self.channel_name}] Error stopping recording: {e}")

    def collect_exit(self):
        """Collect process exit info. Call after is_running becomes False."""
        if not self.process:
            return
        try:
            self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate()
        except Exception:
            pass
