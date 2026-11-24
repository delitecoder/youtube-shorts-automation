#!/usr/bin/env python3
"""Run ONCE on your own computer (needs a browser) to create the OAuth token for a profile.

    python scripts/authorize.py --profile pipeline  --client-secret client_secret.json --out pipeline_token.json
    python scripts/authorize.py --profile publisher --client-secret client_secret.json --out publisher_token.json

The token file is written with 0600 permissions and is never printed. Store its contents in the
protected secret store (see docs/deployment.md), then delete the local file.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ytauto.integrations.google_auth import PROFILES  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(a.client_secret, PROFILES[a.profile]["scopes"])
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        sys.exit("No refresh token returned; remove the app's access at myaccount.google.com and retry.")
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(creds.to_json())
    print(f"Saved {a.profile} token to {a.out} (scopes: {len(PROFILES[a.profile]['scopes'])}). Do not commit it.")


if __name__ == "__main__":
    main()
