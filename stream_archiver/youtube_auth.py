"""Headless YouTube OAuth flow for initial token setup.

Uses SSH port forwarding so the OAuth callback reaches the server without a browser.

Setup (two terminals needed):

  Terminal 1 - on the SERVER:
      python -m stream_archiver --auth-youtube

  Terminal 2 - on your LOCAL machine:
      ssh -L 8085:localhost:8085 your_user@your_server_ip

Then open the printed URL in your local browser. The OAuth redirect to
localhost:8085 is forwarded through the SSH tunnel back to the server.
Token is saved to disk and auto-refreshes indefinitely after that.
"""

import os
import pickle
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

AUTH_PORT = 8085
_auth_code = [None]
_auth_event = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _auth_code[0] = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authentication successful! You can close this tab.</h1>"
            )
            _auth_event.set()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h1>Missing authorization code.</h1>")

    def log_message(self, format, *args):
        pass  # Suppress request logs


def run_auth_flow(config):
    """OAuth via local callback server + SSH port forwarding. Saves token to disk."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets = config.youtube_client_secrets
    if not os.path.exists(secrets):
        print(f"\nERROR: client_secrets.json not found at: {secrets}")
        print("Download it from Google Cloud Console:")
        print("  APIs & Services > Credentials > Create Credentials > OAuth client ID")
        print("  Application type: Desktop app")
        return

    print("\n=== YouTube Authentication ===")
    print(f"STEP 1 - Open a NEW terminal on your LOCAL machine and run:")
    print(f"\n    ssh -L {AUTH_PORT}:localhost:{AUTH_PORT} your_user@your_server_ip\n")
    print("STEP 2 - Keep that tunnel open, then press Enter here to continue.")
    input()

    redirect_uri = f"http://localhost:{AUTH_PORT}/"

    flow = InstalledAppFlow.from_client_secrets_file(secrets, config.youtube_scopes)
    flow.redirect_uri = redirect_uri

    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

    # Start local HTTP server to catch the OAuth callback
    server = HTTPServer(("localhost", AUTH_PORT), _CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    print(f"\nOpen this URL in your local browser:\n\n  {auth_url}\n")
    print("Waiting for authorization (timeout: 5 minutes)...")

    if not _auth_event.wait(timeout=300):
        print("Timed out waiting for authorization.")
        server.shutdown()
        return

    server.shutdown()

    flow.fetch_token(code=_auth_code[0])
    creds = flow.credentials

    token_file = config.youtube_token_file
    token_dir = os.path.dirname(token_file)
    if token_dir:
        os.makedirs(token_dir, exist_ok=True)

    with open(token_file, "wb") as f:
        pickle.dump(creds, f)

    os.chmod(token_file, 0o600)

    print(f"\nAuthentication successful!")
    print(f"Token saved to: {token_file}")
    print("\nYou can now start the service:")
    print("  python -m stream_archiver")
    print("  # or: sudo systemctl start stream-archiver")
