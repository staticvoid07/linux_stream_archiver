"""Utility functions: logging setup, formatting."""

import logging
import sys


def setup_logging(log_file: str, log_to_file: bool = True):
    """Set up logging. Stdout for journald; optional file for persistent logs."""
    handlers = [logging.StreamHandler(sys.stdout)]

    if log_to_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )

    # Reduce noise from HTTP libraries
    logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def format_bytes(b) -> str:
    """Format bytes to human readable string."""
    b = float(b)
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def format_time(seconds: float) -> str:
    """Format seconds to human readable string."""
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"
