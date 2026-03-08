"""Entry point: python -m stream_archiver"""

import os
import sys
import signal
import time
import argparse
import subprocess
import logging

from .config import load_config
from .helpers import setup_logging


def main():
    parser = argparse.ArgumentParser(
        description="Stream Archiver - Multi-Channel Twitch Recorder & YouTube Uploader",
    )
    parser.add_argument("--test-twitch", action="store_true",
                        help="Test Twitch API connection")
    parser.add_argument("--test-youtube", action="store_true",
                        help="Test YouTube API connection")
    parser.add_argument("--upload-pending", action="store_true",
                        help="Upload pending files and exit")
    parser.add_argument("--auth-youtube", action="store_true",
                        help="Run headless YouTube OAuth flow and save token")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config.log_file, config.log_to_file)
    logger = logging.getLogger(__name__)

    # YouTube auth setup (interactive, run once via SSH)
    if args.auth_youtube:
        from .youtube_auth import run_auth_flow
        run_auth_flow(config)
        return

    # Handle test modes
    if args.test_twitch:
        _test_twitch(config)
        return
    if args.test_youtube:
        _test_youtube(config)
        return

    # Validate
    if not config.twitch_client_id or not config.twitch_client_secret:
        print("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    if not config.channels:
        print("No channels configured. Set TWITCH_CHANNELS in .env")
        print("Example: TWITCH_CHANNELS=streamer1,streamer2,streamer3")
        sys.exit(1)

    # Check streamlink
    try:
        subprocess.run(["streamlink", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Streamlink is not installed! pip install streamlink")
        sys.exit(1)

    from .monitor import MonitorOrchestrator
    orchestrator = MonitorOrchestrator(config)

    # Handle upload-pending mode
    if args.upload_pending:
        pending = orchestrator.upload_history.get_pending_files(config.download_folder)
        if not pending:
            print("No pending uploads found.")
            return
        print(f"Found {len(pending)} pending file(s)")
        orchestrator.start_all()
        # Wait for upload queue to drain
        while not orchestrator.upload_worker.queue.empty() or orchestrator.upload_worker.is_uploading:
            time.sleep(2)
        orchestrator.shutdown()
        return

    # Signal handling for systemd SIGTERM and interactive Ctrl-C
    def _handle_signal(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        orchestrator.shutdown()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    orchestrator.start_all()

    channels = ", ".join(c.name for c in config.channels)
    logger.info(f"Stream Archiver started")
    logger.info(f"Monitoring: {channels}")
    logger.info(f"Download folder: {config.download_folder}")
    if config.status_server_enabled:
        logger.info(
            f"Status: http://{config.status_server_host}:{config.status_server_port}/status"
        )

    # Block main thread until shutdown
    while not orchestrator.shutdown_event.is_set():
        time.sleep(1)

    logger.info("Shutdown complete")


def _test_twitch(config):
    """Test Twitch API connection."""
    print("\nTesting Twitch API...")
    if not config.twitch_client_id or not config.twitch_client_secret:
        print("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set in .env")
        return

    from .twitch_api import TwitchAPI
    api = TwitchAPI(config.twitch_client_id, config.twitch_client_secret)

    try:
        api._get_token()
        print("Twitch API credentials are valid!")

        for ch in config.channels:
            info = api.get_stream_info(ch.name)
            if info:
                print(f"  {ch.name}: LIVE - {info.get('game_name', 'N/A')} "
                      f"({info.get('viewer_count', 0)} viewers)")
            else:
                print(f"  {ch.name}: Offline")
    except Exception as e:
        print(f"Twitch API error: {e}")


def _test_youtube(config):
    """Test YouTube API connection using saved token."""
    print("\nTesting YouTube API...")
    if not os.path.exists(config.youtube_client_secrets):
        print(f"Client secrets not found: {config.youtube_client_secrets}")
        print("Download from Google Cloud Console > APIs & Services > Credentials")
        return

    import pickle
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(config.youtube_token_file):
        with open(config.youtube_token_file, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(config.youtube_token_file, "wb") as f:
                pickle.dump(creds, f)
        else:
            print("No valid token. Run: python -m stream_archiver --auth-youtube")
            return

    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(part="snippet", mine=True).execute()
    if resp.get("items"):
        name = resp["items"][0]["snippet"]["title"]
        print(f"YouTube authentication successful!")
        print(f"Connected to channel: {name}")
    else:
        print("YouTube authentication failed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.getLogger(__name__).error(
            f"Unhandled exception: {e}", exc_info=True
        )
        sys.exit(1)
