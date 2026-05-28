import asyncio
import os
import base64
import hmac
import hashlib
import time
import re
import subprocess
import tempfile
import shutil
from typing import Any

import httpx
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv()

MX_PAT = os.environ["MX_PAT"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
TEAMS_WEBHOOK_URL = os.environ["TEAMS_WEBHOOK_URL"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
ALLOWED_APP_IDS = set(os.environ["ALLOWED_APP_IDS"].split(","))

DIFF_CHAR_LIMIT = 15_000


def verify_signature(webhook_id: str, timestamp: str, signature_header: str, body: bytes) -> None:
    """Verify Mendix HMAC-SHA256 webhook signature and reject replays > 5 min."""
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid timestamp")

    if abs(time.time() - ts) > 300:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Request too old")

    msg = f"{webhook_id}.{timestamp}.".encode() + body
    expected_mac = hmac.new(WEBHOOK_SECRET.encode(), msg, hashlib.sha256).digest()
    expected_sig = "v1," + base64.b64encode(expected_mac).decode()

    if not hmac.compare_digest(signature_header, expected_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")


class ReviewRequest(BaseModel):
    appId: str
    before: str
    after: str
    branchName: str
    authorName: str
    commitMessage: str

    @field_validator("appId")
    @classmethod
    def app_id_allowed(cls, v: str) -> str:
        if v not in ALLOWED_APP_IDS:
            raise ValueError(f"appId '{v}' is not in ALLOWED_APP_IDS")
        return v

    @field_validator("before", "after")
    @classmethod
    def valid_commit_hash(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", v):
            raise ValueError("Commit hash must be exactly 40 lowercase hex characters")
        return v


def get_diff(app_id: str, before: str, after: str) -> str:
    """Clone repo and return mprcontents diff, capped at DIFF_CHAR_LIMIT."""
    repo_url = f"https://pat:{MX_PAT}@git.api.mendix.com/{app_id}.git"
    tmp_dir = tempfile.mkdtemp()
    try:
        subprocess.run(
            ["git", "clone", "--depth", "2", repo_url, tmp_dir],
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            ["git", "-C", tmp_dir, "diff", f"{before}..{after}", "--", "mprcontents/"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout[:DIFF_CHAR_LIMIT]
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Git operation failed: {e.stderr}",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


CLAUDE_SYSTEM_PROMPT = (
    "You are a Mendix model reviewer. Review the provided git diff of Mendix model contents. "
    "Focus on: naming conventions (PascalCase for microflows, camelCase for variables), "
    "microflow complexity (avoid deeply nested logic, prefer sub-microflows), "
    "domain model changes (check entity names, associations, and data types), "
    "and security issues (input validation, access rules). "
    "Be concise. Max 400 words. Use bullet points."
)


async def review_diff(diff: str) -> str:
    """Send diff to Claude and return the review text."""
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 600,
        "system": CLAUDE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"Review this Mendix commit diff:\n\n{diff}"}],
    }
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Claude API error {response.status_code}",
        )
    return response.json()["content"][0]["text"]


async def post_to_teams(
    author: str,
    commit_hash: str,
    commit_message: str,
    branch: str,
    review: str,
) -> None:
    """Post an Adaptive Card to the configured Teams webhook."""
    card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Author", "value": author},
                                {"title": "Branch", "value": branch},
                                {"title": "Commit", "value": commit_hash[:12]},
                                {"title": "Message", "value": commit_message},
                            ],
                        },
                        {
                            "type": "TextBlock",
                            "text": "**Code Review**",
                            "weight": "Bolder",
                        },
                        {
                            "type": "TextBlock",
                            "text": review,
                            "wrap": True,
                        },
                    ],
                },
            }
        ],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(TEAMS_WEBHOOK_URL, json=card)

    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Teams webhook error {response.status_code}",
        )


app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/review")
async def review(request: Request) -> JSONResponse:
    body = await request.body()

    webhook_id = request.headers.get("webhook-id")
    webhook_timestamp = request.headers.get("webhook-timestamp")
    webhook_signature = request.headers.get("webhook-signature")

    if not all([webhook_id, webhook_timestamp, webhook_signature]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature headers")

    verify_signature(webhook_id, webhook_timestamp, webhook_signature, body)

    try:
        payload = ReviewRequest.model_validate_json(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    loop = asyncio.get_event_loop()
    diff = await loop.run_in_executor(None, get_diff, payload.appId, payload.before, payload.after)

    review_text = await review_diff(diff)
    await post_to_teams(
        author=payload.authorName,
        commit_hash=payload.after,
        commit_message=payload.commitMessage,
        branch=payload.branchName,
        review=review_text,
    )

    return JSONResponse({"status": "ok"})
