# Mendix Commit Review Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a secure FastAPI service that receives Mendix Pipeline webhooks, fetches git diffs, reviews them with Claude, and posts results to Microsoft Teams.

**Architecture:** A single FastAPI app (`server.py`) handles webhook verification, input validation, git cloning in a temp dir, Claude API calls, and Teams posting — all in one async request handler. Security is layered: HMAC-SHA256 replay-protected signature verification runs before any git or external I/O. Subprocess calls use list arguments (no `shell=True`) to prevent injection.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx (async), python-dotenv, pytest + pytest-asyncio, respx (httpx mocking)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `server.py` | FastAPI app: signature verification, validation, git clone/diff, Claude call, Teams post |
| `tests/test_server.py` | All unit/integration tests using httpx TestClient + respx mocks |
| `requirements.txt` | Pinned production + dev dependencies |
| `.env.example` | Template showing all required env vars |
| `.gitignore` | Exclude `.env`, `__pycache__`, etc. |
| `README.md` | Local setup, curl test commands, ngrok, Mendix pipeline config |

---

### Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`

- [ ] **Step 1: Write `requirements.txt`**

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
httpx==0.27.0
python-dotenv==1.0.1
pytest==8.2.0
pytest-asyncio==0.23.6
respx==0.21.1
```

- [ ] **Step 2: Write `.env.example`**

```
MX_PAT=your-mendix-personal-access-token
CLAUDE_API_KEY=sk-ant-...
TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/...
WEBHOOK_SECRET=your-hmac-secret-32-bytes-min
ALLOWED_APP_IDS=8c909cbd-88ab-4a42-bcd2-3b48fc314ff4,another-app-id
```

- [ ] **Step 3: Write `.gitignore`**

```
.env
__pycache__/
*.py[cod]
.pytest_cache/
*.egg-info/
dist/
.venv/
venv/
```

- [ ] **Step 4: Install dependencies**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Expected: All packages install without errors.

- [ ] **Step 5: Commit**

```bash
git init
git add requirements.txt .env.example .gitignore
git commit -m "chore: project scaffolding"
```

---

### Task 2: FastAPI Skeleton + Health Endpoint

**Files:**
- Create: `server.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py
import pytest
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_server.py::test_health -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Write minimal `server.py`**

```python
import os
import hmac
import hashlib
import time
import re
import subprocess
import tempfile
import shutil
from contextlib import asynccontextmanager
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

app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_server.py::test_health -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: FastAPI skeleton with health endpoint"
```

---

### Task 3: HMAC-SHA256 Signature Verification

**Files:**
- Modify: `server.py` — add `verify_signature()` function
- Modify: `tests/test_server.py` — add signature tests

The Mendix webhook sends three headers:
- `webhook-id` — unique delivery ID
- `webhook-timestamp` — Unix seconds as a string
- `webhook-signature` — `v1,<base64-hmac>` where HMAC = SHA-256 of `{webhook-id}.{webhook-timestamp}.{raw-body}` signed with `WEBHOOK_SECRET`.

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_server.py
import base64
import hashlib
import hmac
import time

from server import verify_signature, WEBHOOK_SECRET


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py::test_verify_signature_valid tests/test_server.py::test_verify_signature_bad_sig tests/test_server.py::test_verify_signature_replay -v
```

Expected: FAIL — `ImportError: cannot import name 'verify_signature'`

- [ ] **Step 3: Add `verify_signature()` to `server.py`** (add after the constants, before the app definition)

```python
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

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(signature_header, expected_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
```

Add `import base64` to the imports at the top of `server.py`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_server.py::test_verify_signature_valid tests/test_server.py::test_verify_signature_bad_sig tests/test_server.py::test_verify_signature_replay -v
```

Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: HMAC-SHA256 signature verification with replay protection"
```

---

### Task 4: Request Model + Input Validation

**Files:**
- Modify: `server.py` — add `ReviewRequest` Pydantic model
- Modify: `tests/test_server.py` — add validation tests

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_server.py
from server import ReviewRequest
from pydantic import ValidationError

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py::test_review_request_valid tests/test_server.py::test_review_request_invalid_app_id tests/test_server.py::test_review_request_invalid_commit_hash -v
```

Expected: FAIL — `ImportError: cannot import name 'ReviewRequest'`

- [ ] **Step 3: Add `ReviewRequest` to `server.py`** (add after imports, before `verify_signature`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_server.py::test_review_request_valid tests/test_server.py::test_review_request_invalid_app_id tests/test_server.py::test_review_request_invalid_commit_hash -v
```

Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: request model with appId allowlist and commit hash validation"
```

---

### Task 5: Git Clone + Diff Extraction

**Files:**
- Modify: `server.py` — add `get_diff()` function
- Modify: `tests/test_server.py` — add diff tests using `unittest.mock`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_server.py
from unittest.mock import patch, MagicMock
from server import get_diff

VALID_APP_ID = next(iter(__import__('server').ALLOWED_APP_IDS))

def test_get_diff_returns_truncated_output():
    long_output = "x" * 20_000
    mock_result = MagicMock()
    mock_result.stdout = long_output

    with patch("server.subprocess.run", return_value=mock_result) as mock_run:
        with patch("server.tempfile.mkdtemp", return_value="/tmp/fake"):
            with patch("server.shutil.rmtree"):
                result = get_diff(VALID_APP_ID, "a" * 40, "b" * 40)

    assert len(result) == 15_000
    # Verify no shell=True was used
    for call in mock_run.call_args_list:
        args, kwargs = call
        assert not kwargs.get("shell", False)


def test_get_diff_subprocess_error_raises():
    with patch("server.subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")) as _:
        with patch("server.tempfile.mkdtemp", return_value="/tmp/fake"):
            with patch("server.shutil.rmtree"):
                with pytest.raises(HTTPException) as exc:
                    get_diff(VALID_APP_ID, "a" * 40, "b" * 40)
    assert exc.value.status_code == 502
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py::test_get_diff_returns_truncated_output tests/test_server.py::test_get_diff_subprocess_error_raises -v
```

Expected: FAIL — `ImportError: cannot import name 'get_diff'`

- [ ] **Step 3: Add `get_diff()` to `server.py`** (add after `ReviewRequest`)

```python
def get_diff(app_id: str, before: str, after: str) -> str:
    """Clone the Mendix teamserver repo (depth 2) and return the mprcontents diff, capped at DIFF_CHAR_LIMIT."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_server.py::test_get_diff_returns_truncated_output tests/test_server.py::test_get_diff_subprocess_error_raises -v
```

Expected: Both PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: git clone and diff extraction with cleanup and injection prevention"
```

---

### Task 6: Claude API Integration

**Files:**
- Modify: `server.py` — add `review_diff()` async function
- Modify: `tests/test_server.py` — add Claude mock tests

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_server.py
import respx
import httpx as _httpx

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py::test_review_diff_returns_text tests/test_server.py::test_review_diff_raises_on_api_error -v
```

Expected: FAIL — `ImportError: cannot import name 'review_diff'`

- [ ] **Step 3: Add `review_diff()` to `server.py`** (add after `get_diff`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_server.py::test_review_diff_returns_text tests/test_server.py::test_review_diff_raises_on_api_error -v
```

Expected: Both PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: Claude API integration for diff review"
```

---

### Task 7: Microsoft Teams Adaptive Card Posting

**Files:**
- Modify: `server.py` — add `post_to_teams()` async function
- Modify: `tests/test_server.py` — add Teams mock tests

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_server.py
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
        # Verify Adaptive Card structure
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py::test_post_to_teams_sends_adaptive_card tests/test_server.py::test_post_to_teams_raises_on_failure -v
```

Expected: FAIL — `ImportError: cannot import name 'post_to_teams'`

- [ ] **Step 3: Add `post_to_teams()` to `server.py`** (add after `review_diff`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_server.py::test_post_to_teams_sends_adaptive_card tests/test_server.py::test_post_to_teams_raises_on_failure -v
```

Expected: Both PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: Teams Adaptive Card webhook posting"
```

---

### Task 8: POST /review Endpoint (Wiring Everything Together)

**Files:**
- Modify: `server.py` — add the `/review` endpoint
- Modify: `tests/test_server.py` — integration test for the full request flow

The `/review` endpoint must:
1. Read raw body bytes (needed for HMAC verification)
2. Verify signature headers
3. Parse + validate JSON body as `ReviewRequest`
4. Call `get_diff()` (sync, in a threadpool via `asyncio.run_in_executor`)
5. Call `review_diff()` (async)
6. Call `post_to_teams()` (async)
7. Return `{"status": "ok"}`

- [ ] **Step 1: Write the failing integration test**

```python
# Add to tests/test_server.py
import asyncio
from unittest.mock import AsyncMock, patch

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
    body = json.dumps({"appId": VALID_APP_ID_STR, "before": "a"*40, "after": "b"*40,
                       "branchName": "main", "authorName": "A", "commitMessage": "m"}).encode()
    response = client.post("/review", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py::test_review_endpoint_full_flow tests/test_server.py::test_review_endpoint_missing_signature_headers -v
```

Expected: FAIL — no `/review` route defined

- [ ] **Step 3: Add the `/review` endpoint to `server.py`** (add at the bottom, after `post_to_teams`)

```python
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

    import asyncio
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
```

- [ ] **Step 4: Run ALL tests**

```bash
pytest tests/test_server.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: POST /review endpoint wiring all components"
```

---

### Task 9: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Mendix Commit Review Service

Receives Mendix Pipeline webhooks, fetches the git diff, reviews it with Claude, and posts results to Microsoft Teams.

## Local Setup

```bash
git clone <this-repo> && cd mendix-review-service
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Fill in all values in .env
```

## Running the Server

```bash
uvicorn server:app --reload --port 8000
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MX_PAT` | Mendix Personal Access Token with repo read access |
| `CLAUDE_API_KEY` | Anthropic API key (`sk-ant-...`) |
| `TEAMS_WEBHOOK_URL` | Incoming Webhook URL from a Teams channel connector |
| `WEBHOOK_SECRET` | Shared secret configured in the Mendix Pipeline POST Request step |
| `ALLOWED_APP_IDS` | Comma-separated list of allowed Mendix App GUIDs |

## Testing with curl

Generate a valid signature:

```bash
BODY='{"appId":"8c909cbd-88ab-4a42-bcd2-3b48fc314ff4","before":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","after":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","branchName":"main","authorName":"Test","commitMessage":"Test commit"}'
TS=$(date +%s)
WID="test-$(uuidgen)"
SECRET="your-webhook-secret"
SIG="v1,$(echo -n "${WID}.${TS}.${BODY}" | openssl dgst -sha256 -hmac "$SECRET" -binary | base64)"

curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -H "webhook-id: $WID" \
  -H "webhook-timestamp: $TS" \
  -H "webhook-signature: $SIG" \
  -d "$BODY"
```

## Exposing Locally with ngrok

```bash
ngrok http 8000
```

Copy the `https://` forwarding URL — use it as the webhook URL in Mendix.

## Configuring the Mendix Pipeline

1. Open your app in Mendix Portal → **Pipelines**.
2. Add a **POST Request** step after your commit trigger.
3. Set the URL to: `https://<your-ngrok-or-production-url>/review`
4. Set the **Secret** field to the value of your `WEBHOOK_SECRET` env var — Mendix uses this to sign the `webhook-signature` header.
5. The payload Mendix sends matches the `ReviewRequest` schema above.

## Running Tests

```bash
pytest tests/ -v
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup, curl testing, ngrok and Mendix pipeline instructions"
```

---

## Self-Review Against Spec

| Spec Requirement | Covered In |
|-----------------|-----------|
| POST /review endpoint | Task 8 |
| HMAC-SHA256 signature verification | Task 3 |
| Replay attack protection (5 min window) | Task 3 |
| Validate appId against ALLOWED_APP_IDS | Task 4 |
| Validate commit hashes are 40 hex chars | Task 4 |
| git clone --depth 2 with PAT | Task 5 |
| git diff scoped to mprcontents/ | Task 5 |
| Cap diff at 15,000 chars | Task 5 |
| Clean up temp dir in finally block | Task 5 |
| Claude API call with system prompt | Task 6 |
| Max 400 words, bullet points (system prompt) | Task 6 |
| Teams Adaptive Card with fact set | Task 7 |
| GET /health returning {"status": "ok"} | Task 2 |
| No shell=True | Task 5 (verified in test) |
| docs_url=None, redoc_url=None | Task 2 |
| .env in .gitignore | Task 1 |
| README with setup, curl, ngrok, Mendix config | Task 9 |
| .env.example with all vars | Task 1 |
| requirements.txt | Task 1 |
