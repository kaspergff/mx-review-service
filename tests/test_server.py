import base64
import hashlib
import hmac
import time
from unittest.mock import patch, MagicMock
import subprocess

import pytest
import respx
import httpx as _httpx
from fastapi.testclient import TestClient
from fastapi import HTTPException
from pydantic import ValidationError

from server import app, verify_signature, WEBHOOK_SECRET, ReviewRequest, get_diff

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

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


VALID_APP_ID = next(iter(__import__('server').ALLOWED_APP_IDS))


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


_VALID_APP_ID = next(iter(__import__('server').ALLOWED_APP_IDS))


def test_get_diff_returns_truncated_output():
    long_output = "x" * 20_000
    mock_result = MagicMock()
    mock_result.stdout = long_output

    with patch("server.subprocess.run", return_value=mock_result) as mock_run:
        with patch("server.tempfile.mkdtemp", return_value="/tmp/fake"):
            with patch("server.shutil.rmtree"):
                result = get_diff(_VALID_APP_ID, "a" * 40, "b" * 40)

    assert len(result) == 15_000
    # Verify no shell=True was used
    for call in mock_run.call_args_list:
        _, kwargs = call
        assert not kwargs.get("shell", False)


def test_get_diff_subprocess_error_raises():
    with patch("server.subprocess.run", side_effect=subprocess.CalledProcessError(1, "git", stderr="clone failed")):
        with patch("server.tempfile.mkdtemp", return_value="/tmp/fake"):
            with patch("server.shutil.rmtree"):
                with pytest.raises(HTTPException) as exc:
                    get_diff(_VALID_APP_ID, "a" * 40, "b" * 40)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_review_diff_returns_text():
    fake_response = {
        "content": [{"type": "text", "text": "- Good naming\n- No issues found"}]
    }
    with respx.mock:
        respx.post(CLAUDE_API_URL).mock(return_value=_httpx.Response(200, json=fake_response))
        from server import review_diff
        result = await review_diff("some diff text")
    assert result == "- Good naming\n- No issues found"


@pytest.mark.asyncio
async def test_review_diff_raises_on_api_error():
    with respx.mock:
        respx.post(CLAUDE_API_URL).mock(return_value=_httpx.Response(500, json={"error": "oops"}))
        from server import review_diff
        with pytest.raises(HTTPException) as exc:
            await review_diff("some diff text")
    assert exc.value.status_code == 502


TEAMS_URL = "https://teams.example.com/webhook"


@pytest.mark.asyncio
async def test_post_to_teams_sends_adaptive_card():
    import server
    original_url = server.TEAMS_WEBHOOK_URL
    server.TEAMS_WEBHOOK_URL = TEAMS_URL

    with respx.mock:
        route = respx.post(TEAMS_URL).mock(return_value=_httpx.Response(200, text="1"))
        from server import post_to_teams
        await post_to_teams(
            author="Alice",
            commit_hash="abc123",
            commit_message="Fix bug",
            branch="main",
            review="- Looks good",
        )
        assert route.called
        body = route.calls[0].request.content
        import json
        card = json.loads(body)
        assert card["type"] == "message"
        facts = card["attachments"][0]["content"]["body"][0]["facts"]
        fact_titles = [f["title"] for f in facts]
        assert "Author" in fact_titles
        assert "Commit" in fact_titles

    server.TEAMS_WEBHOOK_URL = original_url


@pytest.mark.asyncio
async def test_post_to_teams_raises_on_failure():
    import server
    original_url = server.TEAMS_WEBHOOK_URL
    server.TEAMS_WEBHOOK_URL = TEAMS_URL

    with respx.mock:
        respx.post(TEAMS_URL).mock(return_value=_httpx.Response(400, text="bad"))
        from server import post_to_teams
        with pytest.raises(HTTPException) as exc:
            await post_to_teams("A", "abc", "msg", "main", "review")
        assert exc.value.status_code == 502

    server.TEAMS_WEBHOOK_URL = original_url
