import base64
import hashlib
import hmac
import time
from unittest.mock import patch, MagicMock
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from pydantic import ValidationError

from server import app, verify_signature, WEBHOOK_SECRET, ReviewRequest

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def _make_sig(webhook_id: str, timestamp: str, body: bytes) -> str:
    msg = f"{webhook_id}.{timestamp}.".encode() + body
    mac = hmac.new(WEBHOOK_SECRET.encode(), msg, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def test_verify_signature_valid():
    ts = str(int(time.time()))
    body = b'{"hello":"world"}'
    sig = _make_sig("id-1", ts, body)
    # Should not raise
    verify_signature("id-1", ts, sig, body)


def test_verify_signature_bad_sig():
    ts = str(int(time.time()))
    body = b'{"hello":"world"}'
    with pytest.raises(HTTPException) as exc:
        verify_signature("id-1", ts, "v1,badsig==", body)
    assert exc.value.status_code == 401


def test_verify_signature_replay():
    old_ts = str(int(time.time()) - 400)  # 6.6 minutes ago
    body = b'{"hello":"world"}'
    sig = _make_sig("id-1", old_ts, body)
    with pytest.raises(HTTPException) as exc:
        verify_signature("id-1", old_ts, sig, body)
    assert exc.value.status_code == 401


_allowed = __import__('server').ALLOWED_APP_IDS
VALID_APP_ID = next(iter(_allowed)) if _allowed else "8c909cbd-88ab-4a42-bcd2-3b48fc314ff4"


def test_review_request_valid():
    r = ReviewRequest(
        appId=VALID_APP_ID,
        before="a" * 40,
        after="b" * 40,
        branchName="main",
        authorName="Alice",
        commitMessage="Fix bug",
    )
    assert r.appId == VALID_APP_ID


def test_review_request_invalid_app_id():
    import server as _server
    with patch.object(_server, "ALLOWED_APP_IDS", {"8c909cbd-88ab-4a42-bcd2-3b48fc314ff4"}):
        with pytest.raises(ValidationError):
            ReviewRequest(
                appId="not-a-real-app-id",
                before="a" * 40,
                after="b" * 40,
                branchName="main",
                authorName="Alice",
                commitMessage="Fix bug",
            )


def test_review_request_invalid_commit_hash():
    with pytest.raises(ValidationError):
        ReviewRequest(
            appId=VALID_APP_ID,
            before="short",
            after="b" * 40,
            branchName="main",
            authorName="Alice",
            commitMessage="Fix bug",
        )


_VALID_APP_ID = VALID_APP_ID


@pytest.mark.asyncio
async def test_post_to_teams_sends_adaptive_card():
    import json
    from server import post_to_teams

    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200

    async def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["body"] = json
        return mock_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = fake_post

    with patch("server.httpx.AsyncClient", return_value=mock_client):
        await post_to_teams(
            author="Alice",
            commit_hash="abc123",
            commit_message="Fix bug",
            branch="main",
            review="- Looks good",
        )

    card = captured["body"]
    assert card["type"] == "message"
    facts = card["attachments"][0]["content"]["body"][0]["facts"]
    fact_titles = [f["title"] for f in facts]
    assert "Author" in fact_titles
    assert "Commit" in fact_titles


@pytest.mark.asyncio
async def test_post_to_teams_raises_on_failure():
    from server import post_to_teams

    mock_response = MagicMock()
    mock_response.status_code = 400

    async def fake_post(url, json=None, **kwargs):
        return mock_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = fake_post

    with patch("server.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc:
            await post_to_teams("A", "abc", "msg", "main", "review")
    assert exc.value.status_code == 502


VALID_APP_ID_STR = VALID_APP_ID


def _make_review_headers(body: bytes) -> dict:
    ts = str(int(time.time()))
    wid = "test-id-1"
    sig = _make_sig(wid, ts, body)
    return {
        "webhook-id": wid,
        "webhook-timestamp": ts,
        "webhook-signature": sig,
        "content-type": "application/json",
    }


def test_review_endpoint_returns_202():
    import json
    body = json.dumps({
        "appId": VALID_APP_ID_STR,
        "before": "a" * 40,
        "after": "b" * 40,
        "branchName": "main",
        "authorName": "Alice",
        "commitMessage": "Fix bug",
    }).encode()

    with patch("server._run_review", new_callable=AsyncMock):
        response = client.post("/review", content=body, headers=_make_review_headers(body))

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}


def test_review_endpoint_missing_signature_headers():
    import json
    body = json.dumps({
        "appId": VALID_APP_ID_STR,
        "before": "a" * 40,
        "after": "b" * 40,
        "branchName": "main",
        "authorName": "A",
        "commitMessage": "m",
    }).encode()
    response = client.post("/review", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 401
