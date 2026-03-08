"""Lightweight HTTP server that exposes current status as JSON.

Serves GET /status (or / or /status.json) returning live orchestrator state.
Runs in a daemon thread. No external dependencies (uses http.server).
"""

import json
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger(__name__)

_VALID_PATHS = {"/", "/status", "/status.json"}


class _Handler(BaseHTTPRequestHandler):
    orchestrator = None

    def do_GET(self):
        if self.path not in _VALID_PATHS:
            self.send_response(404)
            self.end_headers()
            return

        data = self.orchestrator._build_status_dict()
        body = json.dumps(data, indent=2).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Suppress per-request access logs


class StatusServer:
    """HTTP status server running in a daemon thread."""

    def __init__(self, orchestrator, host: str, port: int):
        _Handler.orchestrator = orchestrator
        self._server = HTTPServer((host, port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="status-server",
        )

    def start(self):
        host, port = self._server.server_address
        logger.info(f"Status server listening on http://{host}:{port}/status")
        self._thread.start()

    def stop(self):
        self._server.shutdown()
