"""Headless YouTube OAuth flow for initial token setup.

Run once interactively via SSH:
    python -m stream_archiver --auth-youtube

The script prints an authorization URL. Open it in any browser (on any machine),
complete the Google sign-in, then paste the returned code back here.
The token is saved to disk and will auto-refresh indefinitely from that point.
"""

import os
import logging

logger = logging.getLogger(__name__)


def run_auth_flow(config):
    """Interactive headless OAuth via run_console(). Saves token to disk."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets = config.youtube_client_secrets
    if not os.path.exists(secrets):
        print(f"\nERROR: client_secrets.json not found at: {secrets}")
        print("Download it from Google Cloud Console:")
        print("  APIs & Services > Credentials > Create Credentials > OAuth client ID")
        print("  Application type: Desktop app")
        return

    print("\n=== YouTube Authentication ===")
    print("This will open a browser authorization URL.")
    print("If running on a remote server, copy the URL to a local browser,")
    print("complete the sign-in, then paste the authorization code back here.\n")

    flow = InstalledAppFlow.from_client_secrets_file(secrets, config.youtube_scopes)

    # run_console() prints the auth URL and reads the code from stdin.
    # Works over SSH with no display required.
    creds = flow.run_console()

    token_file = config.youtube_token_file

    # Ensure parent directory exists
    token_dir = os.path.dirname(token_file)
    if token_dir:
        os.makedirs(token_dir, exist_ok=True)

    import pickle
    with open(token_file, "wb") as f:
        pickle.dump(creds, f)

    # Restrict token file permissions (contains sensitive OAuth credentials)
    os.chmod(token_file, 0o600)

    print(f"\nAuthentication successful!")
    print(f"Token saved to: {token_file}")
    print("\nYou can now start the service:")
    print("  python -m stream_archiver")
    print("  # or: sudo systemctl start stream-archiver")
