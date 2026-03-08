"""Thread-safe Twitch API client for stream checking."""

import time
import threading
import logging
import requests

logger = logging.getLogger(__name__)


class TwitchAPI:
    """Shared Twitch API client. Thread-safe token management."""

    TOKEN_REFRESH_MARGIN = 300  # Refresh 5 min before expiry

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None
        self._token_expires_at = 0
        self._lock = threading.Lock()

    def _refresh_token(self):
        """Get a new app access token from Twitch."""
        resp = requests.post("https://id.twitch.tv/oauth2/token", params={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        })
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600)
        logger.info("Obtained new Twitch access token")

    def _get_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        with self._lock:
            if not self._token or time.time() >= self._token_expires_at - self.TOKEN_REFRESH_MARGIN:
                self._refresh_token()
            return self._token

    def _headers(self) -> dict:
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self._get_token()}",
        }

    def get_stream_info(self, channel_name: str) -> dict | None:
        """Check if a channel is live. Returns stream info dict or None."""
        resp = requests.get(
            f"https://api.twitch.tv/helix/streams?user_login={channel_name}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        if data["data"]:
            return data["data"][0]
        return None

    def get_user_info(self, channel_name: str) -> dict | None:
        """Get user info (for profile image URL in notifications)."""
        resp = requests.get(
            f"https://api.twitch.tv/helix/users?login={channel_name}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        if data["data"]:
            return data["data"][0]
        return None
