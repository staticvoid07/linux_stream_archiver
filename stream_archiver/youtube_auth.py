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

logger = logging.getLogger(__name__)

AUTH_PORT = 8085


def run_auth_flow(config):
    """OAuth via local server + SSH port forwarding. Saves token to disk."""
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
    print(f"\n    ssh -L {AUTH_PORT}:localhost:{AUTH_PORT} <your_user>@<your_server_ip>\n")
    print(f"STEP 2 - Keep that tunnel open, then press Enter here to continue.")
    input()

    flow = InstalledAppFlow.from_client_secrets_file(secrets, config.youtube_scopes)

    print(f"Starting auth server on localhost:{AUTH_PORT} ...")
    print(f"A URL will appear below. Open it in your local browser.\n")

    # open_browser=False because there's no browser on the server.
    # The SSH tunnel forwards localhost:AUTH_PORT on your local machine
    # to localhost:AUTH_PORT on the server, so the OAuth redirect works.
    creds = flow.run_local_server(port=AUTH_PORT, open_browser=False)

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
