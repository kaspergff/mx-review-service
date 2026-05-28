import base64
import hashlib
import hmac
import time
from unittest.mock import patch, MagicMock
import subprocess

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from pydantic import ValidationError

from server import app, verify_signature, WEBHOOK_SECRET, ReviewRequest, get_diff

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


def test_get_diff_returns_parsed_markdown():
    """get_diff moet leesbare markdown teruggeven, geen ruwe binary diff."""
    # clone: geen output nodig
    clone_result = MagicMock()
    clone_result.stdout = b""

    # name-status: één gewijzigd .mxunit bestand
    name_status_result = MagicMock()
    name_status_result.stdout = "M\tmprcontents/ab/cd/abcd1234-0000-0000-0000-000000000000.mxunit"

    # git show voor-versie: minimale geldige BSON voor een microflow
    import bson as _bson
    before_bson = _bson.encode({
        "$Type": "Microflows$Microflow",
        "Name": "ACT_Test",
        "MicroflowReturnType": {"$Type": "DataTypes$VoidType"},
        "MicroflowParameters": [],
        "ObjectCollection": {"Objects": []},
        "AllowedModuleRoles": [],
        "Documentation": "",
    })
    before_result = MagicMock()
    before_result.stdout = before_bson

    # git show na-versie: zelfde
    after_result = MagicMock()
    after_result.stdout = before_bson

    results = iter([clone_result, name_status_result, before_result, after_result])

    def fake_run(args, **kwargs):
        return next(results)

    with patch("server.subprocess.run", side_effect=fake_run) as mock_run:
        with patch("server.tempfile.mkdtemp", return_value="/tmp/fake"):
            with patch("server.shutil.rmtree"):
                result = get_diff(_VALID_APP_ID, "a" * 40, "b" * 40)

    assert "ACT_Test" in result
    assert "Gewijzigd" in result
    for call in mock_run.call_args_list:
        _, kwargs = call
        assert not kwargs.get("shell", False)


def test_get_diff_truncates_at_limit():
    """Output mag niet groter zijn dan DIFF_CHAR_LIMIT."""
    import bson as _bson
    big_bson = _bson.encode({
        "$Type": "Microflows$Microflow",
        "Name": "A" * 500,
        "MicroflowReturnType": {"$Type": "DataTypes$VoidType"},
        "MicroflowParameters": [],
        "ObjectCollection": {"Objects": []},
        "AllowedModuleRoles": [],
        "Documentation": "x" * 1000,
    })

    entries = "\n".join(
        f"M\tmprcontents/ab/cd/abcd{i:04d}-0000-0000-0000-000000000000.mxunit"
        for i in range(30)
    )

    def fake_run(args, **kwargs):
        r = MagicMock()
        # name-status call gebruikt text=True en verwacht str stdout
        if kwargs.get("text"):
            r.stdout = entries
        else:
            r.stdout = big_bson
        return r

    with patch("server.subprocess.run", side_effect=fake_run):
        with patch("server.tempfile.mkdtemp", return_value="/tmp/fake"):
            with patch("server.shutil.rmtree"):
                result = get_diff(_VALID_APP_ID, "a" * 40, "b" * 40)

    assert len(result) <= 15_000


def test_get_diff_subprocess_error_raises():
    with patch("server.subprocess.run", side_effect=subprocess.CalledProcessError(1, "git", stderr="clone failed")):
        with patch("server.tempfile.mkdtemp", return_value="/tmp/fake"):
            with patch("server.shutil.rmtree"):
                with pytest.raises(HTTPException) as exc:
                    get_diff(_VALID_APP_ID, "a" * 40, "b" * 40)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_review_diff_returns_text():
    fake_message = MagicMock()
    fake_message.content = "- Good naming\n- No issues found"
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=fake_response):
        from server import review_diff
        result = await review_diff("some diff text")
    assert result == "- Good naming\n- No issues found"


@pytest.mark.asyncio
async def test_review_diff_raises_on_api_error():
    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("connection error")):
        from server import review_diff
        with pytest.raises(HTTPException) as exc:
            await review_diff("some diff text")
    assert exc.value.status_code == 502


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


import asyncio
from unittest.mock import AsyncMock

VALID_APP_ID_STR = next(iter(__import__('server').ALLOWED_APP_IDS))


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


def test_review_endpoint_full_flow():
    import json
    body = json.dumps({
        "appId": VALID_APP_ID_STR,
        "before": "a" * 40,
        "after": "b" * 40,
        "branchName": "main",
        "authorName": "Alice",
        "commitMessage": "Fix bug",
    }).encode()

    with patch("server.get_diff", return_value="diff output") as mock_diff, \
         patch("server.review_diff", new_callable=AsyncMock, return_value="- Looks good") as mock_review, \
         patch("server.post_to_teams", new_callable=AsyncMock) as mock_teams:

        response = client.post("/review", content=body, headers=_make_review_headers(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    mock_diff.assert_called_once_with(VALID_APP_ID_STR, "a" * 40, "b" * 40)
    mock_review.assert_called_once_with("diff output")
    mock_teams.assert_called_once()


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


# ---------------------------------------------------------------------------
# _load_system_prompt tests
# ---------------------------------------------------------------------------

def test_load_system_prompt_returns_file_content(tmp_path, monkeypatch):
    """_load_system_prompt leest het bestand en geeft de inhoud terug."""
    prompt_file = tmp_path / "system_prompt.md"
    prompt_file.write_text("Je bent een reviewer.\n\nFocus op security.")

    import server as _server
    monkeypatch.setattr(_server, "_SYSTEM_PROMPT_PATH", prompt_file)

    from server import _load_system_prompt
    result = _load_system_prompt()
    assert result == "Je bent een reviewer.\n\nFocus op security."


def test_load_system_prompt_missing_file_raises(tmp_path, monkeypatch):
    """_load_system_prompt gooit FileNotFoundError als het bestand niet bestaat."""
    missing = tmp_path / "does_not_exist.md"

    import server as _server
    monkeypatch.setattr(_server, "_SYSTEM_PROMPT_PATH", missing)

    from server import _load_system_prompt
    with pytest.raises(FileNotFoundError):
        _load_system_prompt()
