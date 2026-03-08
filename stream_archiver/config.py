"""Configuration loading from .env with multi-channel support."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load .env from the stream_archiver package directory (not the parent)
_package_dir = os.path.dirname(os.path.abspath(__file__))
_env_path = os.path.join(_package_dir, ".env")
load_dotenv(dotenv_path=_env_path)


@dataclass
class ChannelConfig:
    """Configuration for a single monitored channel."""
    name: str
    quality: str = "best"
    playlist_id: str = ""
    check_interval: int = 60


@dataclass
class AppConfig:
    """Application-wide configuration."""

    # Twitch API (shared across all channels)
    twitch_client_id: str = ""
    twitch_client_secret: str = ""

    # YouTube (shared - one account)
    youtube_client_secrets: str = "client_secrets.json"
    youtube_token_file: str = "youtube_token.pickle"
    youtube_category: str = "20"
    youtube_privacy: str = "unlisted"
    youtube_scopes: list = field(default_factory=lambda: [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
    ])

    # Paths
    download_folder: str = "./twitch_downloads"
    upload_history_file: str = "upload_history.json"
    log_file: str = "stream_archiver.log"
    status_file: str = "status.json"

    # Global defaults
    check_interval: int = 60
    stream_quality: str = "best"

    # Logging
    log_to_file: bool = True

    # HTTP status server
    status_server_enabled: bool = True
    status_server_host: str = "127.0.0.1"
    status_server_port: int = 8080

    # Webhook notifications
    webhook_url: str = ""
    webhook_type: str = "discord"  # "discord", "slack", or "generic"

    # Notification flags
    notify_stream_online: bool = True
    notify_recording_start: bool = True
    notify_recording_end: bool = False
    notify_upload_start: bool = False
    notify_upload_end: bool = True
    notify_upload_fail: bool = True
    notify_error: bool = True

    # Channels
    channels: list = field(default_factory=list)


def _resolve_path(relative_path: str) -> str:
    """Resolve a path relative to the package directory."""
    if os.path.isabs(relative_path):
        return relative_path
    return os.path.normpath(os.path.join(_package_dir, relative_path))


def load_config() -> AppConfig:
    """Load configuration from environment variables."""
    config = AppConfig(
        twitch_client_id=os.getenv("TWITCH_CLIENT_ID", ""),
        twitch_client_secret=os.getenv("TWITCH_CLIENT_SECRET", ""),
        youtube_client_secrets=_resolve_path(
            os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json")),
        youtube_token_file=_resolve_path(
            os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.pickle")),
        youtube_category=os.getenv("YOUTUBE_CATEGORY", "20"),
        youtube_privacy=os.getenv("YOUTUBE_PRIVACY", "unlisted"),
        download_folder=_resolve_path(
            os.getenv("DOWNLOAD_FOLDER", "./twitch_downloads")),
        upload_history_file=_resolve_path(
            os.getenv("UPLOAD_HISTORY_FILE", "upload_history.json")),
        log_file=_resolve_path(
            os.getenv("LOG_FILE", "stream_archiver.log")),
        status_file=_resolve_path(
            os.getenv("STATUS_FILE", "status.json")),
        check_interval=int(os.getenv("CHECK_INTERVAL", "60")),
        stream_quality=os.getenv("STREAM_QUALITY", "best"),
        log_to_file=os.getenv("LOG_TO_FILE", "true").lower() == "true",
        status_server_enabled=os.getenv("STATUS_SERVER_ENABLED", "true").lower() == "true",
        status_server_host=os.getenv("STATUS_SERVER_HOST", "127.0.0.1"),
        status_server_port=int(os.getenv("STATUS_SERVER_PORT", "8080")),
        webhook_url=os.getenv("WEBHOOK_URL", ""),
        webhook_type=os.getenv("WEBHOOK_TYPE", "discord"),
        notify_stream_online=os.getenv("NOTIFY_STREAM_ONLINE", "true").lower() == "true",
        notify_recording_start=os.getenv("NOTIFY_RECORDING_START", "true").lower() == "true",
        notify_recording_end=os.getenv("NOTIFY_RECORDING_END", "false").lower() == "true",
        notify_upload_start=os.getenv("NOTIFY_UPLOAD_START", "false").lower() == "true",
        notify_upload_end=os.getenv("NOTIFY_UPLOAD_END", "true").lower() == "true",
        notify_upload_fail=os.getenv("NOTIFY_UPLOAD_FAIL", "true").lower() == "true",
        notify_error=os.getenv("NOTIFY_ERROR", "true").lower() == "true",
    )

    # Parse channel list
    channels_str = os.getenv("TWITCH_CHANNELS", "")
    channel_names = [c.strip() for c in channels_str.split(",") if c.strip()]

    global_playlist = os.getenv("YOUTUBE_PLAYLIST_ID", "")

    for name in channel_names:
        upper_name = name.upper().replace("-", "_")
        ch = ChannelConfig(
            name=name,
            quality=os.getenv(f"CHANNEL_{upper_name}_QUALITY", config.stream_quality),
            playlist_id=os.getenv(f"CHANNEL_{upper_name}_PLAYLIST", global_playlist),
            check_interval=int(os.getenv(
                f"CHANNEL_{upper_name}_INTERVAL", str(config.check_interval)
            )),
        )
        config.channels.append(ch)

    return config
