import asyncio
import logging
import os
import base64
import hmac
import hashlib
import time
import re
import shutil
import tempfile

logger = logging.getLogger(__name__)

import httpx
from agent.loop import run_agent
from agent.repo import clone_repo, find_mpr
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv()

MX_PAT = os.environ.get("MX_PAT", "")
LLM_MODEL = os.environ["LLM_MODEL"]
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
_raw_app_ids = os.environ.get("ALLOWED_APP_IDS", "")
ALLOWED_APP_IDS: set[str] = set(_raw_app_ids.split(",")) - {""} if _raw_app_ids else set()
MX_GIT_BASE_URL = os.environ.get("MX_GIT_BASE_URL", "https://git.api.mendix.com")
MX_LOCAL_REPO = os.environ.get("MX_LOCAL_REPO", "")
REVIEW_TIMEOUT_SECONDS = int(os.environ.get("REVIEW_TIMEOUT_SECONDS", "300"))


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

    sigs = [s.strip() for s in signature_header.split() if s.strip()]
    if not any(hmac.compare_digest(sig, expected_sig) for sig in sigs):
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
        if ALLOWED_APP_IDS and v not in ALLOWED_APP_IDS:
            raise ValueError(f"appId '{v}' is not in ALLOWED_APP_IDS")
        return v

    @field_validator("before", "after")
    @classmethod
    def valid_commit_hash(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", v):
            raise ValueError("Commit hash must be exactly 40 lowercase hex characters")
        return v


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
                        {"type": "TextBlock", "text": "**Code Review**", "weight": "Bolder"},
                        {"type": "TextBlock", "text": review, "wrap": True},
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


async def _run_review(payload: ReviewRequest) -> None:
    """Background task: clone repo, run agent, post to Teams."""
    tmp_dir = None
    try:
        if MX_LOCAL_REPO:
            repo_path = MX_LOCAL_REPO
        else:
            tmp_dir = tempfile.mkdtemp()
            await asyncio.to_thread(
                clone_repo, payload.appId, tmp_dir, MX_GIT_BASE_URL, MX_PAT
            )
            repo_path = tmp_dir

        mpr_path = find_mpr(repo_path)
        review_text = await run_agent(
            payload=payload,
            repo_path=repo_path,
            mpr_path=mpr_path,
            model=LLM_MODEL,
            timeout=REVIEW_TIMEOUT_SECONDS,
        )

        if TEAMS_WEBHOOK_URL:
            await post_to_teams(
                author=payload.authorName,
                commit_hash=payload.after,
                commit_message=payload.commitMessage,
                branch=payload.branchName,
                review=review_text,
            )
        else:
            logger.info(
                "[review] %s %s %s\n%s",
                payload.authorName, payload.branchName, payload.after[:12], review_text,
            )
    except Exception:
        logger.exception("Review failed for commit %s", payload.after[:12])
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/review", status_code=202)
async def review(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
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

    background_tasks.add_task(_run_review, payload)
    return JSONResponse({"status": "accepted"}, status_code=202)
