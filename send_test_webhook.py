#!/usr/bin/env python3
"""
Lokaal testen zonder Mendix pipeline.

Gebruik:
  1. Zorg dat de server draait:
       .venv/bin/uvicorn server:app --reload --port 8000

  2. Zet in je .env:
       MX_LOCAL_REPO=/pad/naar/jouw/mendix-project
       MX_PAT=local-test          # willekeurige waarde
       ALLOWED_APP_IDS=test-app-00000000-0000-0000-0000-000000000001
       WEBHOOK_SECRET=test-secret
       TEAMS_WEBHOOK_URL=         # leeg → print naar stdout

  3. Voer dit script uit:
       .venv/bin/python send_test_webhook.py [--before <hash>] [--after <hash>]

  Zonder --before/--after worden automatisch de twee laatste commits gebruikt.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

SERVER_URL = os.environ.get("TEST_SERVER_URL", "http://localhost:8000")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "test-secret")
APP_ID = os.environ.get("TEST_APP_ID") or next(
    iter(os.environ.get("ALLOWED_APP_IDS", "test-app-00000000-0000-0000-0000-000000000001").split(",")),
    "test-app-00000000-0000-0000-0000-000000000001",
)
LOCAL_REPO = os.environ.get("MX_LOCAL_REPO", "")


def latest_two_commits(repo_path: str) -> tuple[str, str]:
    """Geeft (before, after) terug: de twee laatste commits in de repo."""
    log = subprocess.check_output(
        ["git", "-C", repo_path, "log", "--format=%H", "-2"]
    ).decode().strip().splitlines()
    if len(log) < 2:
        print("Fout: de repo heeft minder dan 2 commits.", file=sys.stderr)
        sys.exit(1)
    after, before = log[0], log[1]
    return before, after


def send_webhook(before: str, after: str, url: str = SERVER_URL):
    payload = {
        "appId": APP_ID,
        "before": before,
        "after": after,
        "branchName": "main",
        "authorName": "Test User",
        "commitMessage": "Lokale testcommit",
    }
    body = json.dumps(payload).encode()
    webhook_id = str(uuid.uuid4())
    ts = str(int(time.time()))

    msg = f"{webhook_id}.{ts}.".encode() + body
    mac = hmac.new(WEBHOOK_SECRET.encode(), msg, hashlib.sha256).digest()
    sig = "v1," + base64.b64encode(mac).decode()

    headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": ts,
        "webhook-signature": sig,
        "content-type": "application/json",
    }

    print(f"\nPOST {url}/review")
    print(f"appId: {APP_ID}  before: {before[:12]}  after: {after[:12]}")
    response = httpx.post(f"{url}/review", content=body, headers=headers, timeout=120)
    print(f"Status: {response.status_code}")
    if response.status_code != 200:
        print(f"Body: {response.text}")
    return response.status_code == 200


def main():
    parser = argparse.ArgumentParser(description="Test mx-review-service lokaal")
    parser.add_argument("--before", help="before commit hash (40 hex tekens)")
    parser.add_argument("--after", help="after commit hash (40 hex tekens)")
    parser.add_argument("--url", default=SERVER_URL, help=f"Server URL (default: {SERVER_URL})")
    args = parser.parse_args()

    before, after = args.before, args.after

    if not before or not after:
        if not LOCAL_REPO:
            print("Stel MX_LOCAL_REPO in je .env in, of geef --before en --after op.", file=sys.stderr)
            sys.exit(1)
        print(f"Geen hashes opgegeven — gebruik de twee laatste commits uit {LOCAL_REPO}")
        before, after = latest_two_commits(LOCAL_REPO)
        print(f"before: {before}\nafter:  {after}")

    print("\n=== Webhook verzenden ===")
    ok = send_webhook(before, after, args.url)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
