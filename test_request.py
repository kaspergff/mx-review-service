#!/usr/bin/env python3
"""Stuur een test review-request naar de lokale server."""
import base64
import hashlib
import hmac
import json
import os
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
URL = "http://localhost:8000/review"

body = json.dumps({
    "appId": "test-app-id",
    "before": "c8a09f4228e7bc2b962bf64a2001fbcbb9d59c05",
    "after": "bf1cf757aceca25fcc7475d12cd3720835313c3c",
    "branchName": "main",
    "authorName": "Test User",
    "commitMessage": "Migratie upload versimpeld",
}).encode()

webhook_id = "test-webhook-001"
timestamp = str(int(time.time()))
msg = f"{webhook_id}.{timestamp}.".encode() + body
mac = hmac.new(WEBHOOK_SECRET.encode(), msg, hashlib.sha256).digest()
signature = "v1," + base64.b64encode(mac).decode()

headers = {
    "Content-Type": "application/json",
    "webhook-id": webhook_id,
    "webhook-timestamp": timestamp,
    "webhook-signature": signature,
}

print(f"POST {URL}")
resp = httpx.post(URL, content=body, headers=headers, timeout=120)
print(f"Status: {resp.status_code}")
print(resp.text)
