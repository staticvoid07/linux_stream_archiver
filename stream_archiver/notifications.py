"""Webhook notification dispatcher (Discord, Slack, generic HTTP POST)."""

import logging
import requests

logger = logging.getLogger(__name__)

# Discord embed colors per event type
_DISCORD_COLORS = {
    "stream_online": 0x9147FF,   # Twitch purple
    "recording_start": 0xE74C3C, # Red
    "recording_end": 0x2ECC71,   # Green
    "upload_start": 0x95A5A6,    # Grey
    "upload_end": 0x3498DB,      # Blue
    "upload_fail": 0xE74C3C,     # Red
    "error": 0xE74C3C,
}


class Notifier:
    """Webhook notification sender. No-op when WEBHOOK_URL is unset."""

    def __init__(self, config):
        self.config = config
        self._session = requests.Session()

    def _is_enabled(self, notification_type: str) -> bool:
        flag_name = f"notify_{notification_type}"
        return getattr(self.config, flag_name, True)

    def send(self, title: str, message: str, notification_type: str = "info",
             channel_name: str = None, profile_image_url: str = None,
             launch_url: str = None):
        """Send a webhook notification."""
        if not self._is_enabled(notification_type):
            return
        if not self.config.webhook_url:
            return
        try:
            wtype = self.config.webhook_type
            if wtype == "discord":
                self._send_discord(title, message, notification_type, launch_url)
            elif wtype == "slack":
                self._send_slack(title, message, launch_url)
            else:
                self._send_generic(title, message)
        except Exception as e:
            logger.debug(f"Webhook notification failed: {e}")

    def _send_discord(self, title: str, message: str,
                      notification_type: str, launch_url: str = None):
        color = _DISCORD_COLORS.get(notification_type, 0x95A5A6)
        embed = {"title": title, "description": message, "color": color}
        if launch_url:
            embed["url"] = launch_url
        self._session.post(
            self.config.webhook_url,
            json={"embeds": [embed]},
            timeout=10,
        ).raise_for_status()

    def _send_slack(self, title: str, message: str, launch_url: str = None):
        text = f"*{title}*\n{message}"
        if launch_url:
            text += f"\n{launch_url}"
        self._session.post(
            self.config.webhook_url,
            json={"text": text},
            timeout=10,
        ).raise_for_status()

    def _send_generic(self, title: str, message: str):
        self._session.post(
            self.config.webhook_url,
            json={"title": title, "message": message},
            timeout=10,
        ).raise_for_status()

    def send_stream_online(self, channel_name: str, stream_info: dict,
                           profile_image_url: str = None):
        """Notification for a streamer going live."""
        game = stream_info.get("game_name", "Unknown")
        title_text = stream_info.get("title", "")[:80]
        viewers = stream_info.get("viewer_count", 0)

        self.send(
            title=f"{channel_name} is LIVE!",
            message=f"Playing: {game}\n{title_text}\n{viewers:,} viewers",
            notification_type="stream_online",
            channel_name=channel_name,
            profile_image_url=profile_image_url,
        )
