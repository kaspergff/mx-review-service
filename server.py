import asyncio
import logging
import os
import base64
import hmac
import hashlib
import time
import re
from pathlib import Path
import subprocess
import tempfile
import shutil

logger = logging.getLogger(__name__)

import httpx
import litellm
from mendix.parser import parse_bytes, summarize, format_summary
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv()

MX_PAT = os.environ["MX_PAT"]
LLM_MODEL = os.environ["LLM_MODEL"]
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
ALLOWED_APP_IDS = set(os.environ["ALLOWED_APP_IDS"].split(","))
MX_GIT_BASE_URL = os.environ.get("MX_GIT_BASE_URL", "https://git.api.mendix.com")
MX_LOCAL_REPO = os.environ.get("MX_LOCAL_REPO", "")
if not ALLOWED_APP_IDS or "" in ALLOWED_APP_IDS:
    raise RuntimeError("ALLOWED_APP_IDS must be a non-empty comma-separated list of GUIDs")

DIFF_CHAR_LIMIT = 15_000
MAX_FILES_PER_DIFF = 50


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
        if v not in ALLOWED_APP_IDS:
            raise ValueError(f"appId '{v}' is not in ALLOWED_APP_IDS")
        return v

    @field_validator("before", "after")
    @classmethod
    def valid_commit_hash(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", v):
            raise ValueError("Commit hash must be exactly 40 lowercase hex characters")
        return v


def _git(args: list[str], cwd: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", cwd] + args, check=True, capture_output=True, **kwargs)


def _parse_mxunit_at(cwd: str, ref: str, path: str) -> dict | None:
    """Parseer een .mxunit bestand op een specifiek git commit ref. Geeft None bij fout."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "show", f"{ref}:{path}"],
            check=True, capture_output=True,
        )
        return summarize(parse_bytes(result.stdout))
    except Exception as e:
        logger.warning("Failed to parse %s at %s: %s", path, ref, e)
        return None


def _format_mxunit_change(path: str, before: dict | None, after: dict | None) -> str:
    """Formatteer een voor/na vergelijking van één .mxunit bestand als markdown."""
    filename = path.split('/')[-1]
    if before is None and after is None:
        return f"### Hernoemd/onleesbaar: `{filename}`\n_(niet parseerbaar)_"
    if before is None:
        label = f"### Toegevoegd: `{filename}`"
        return f"{label}\n{format_summary(after)}"
    if after is None:
        label = f"### Verwijderd: `{filename}`"
        return f"{label}\n{format_summary(before)}"
    label = f"### Gewijzigd: `{filename}`"
    return f"{label}\n**Voor:**\n{format_summary(before)}\n\n**Na:**\n{format_summary(after)}"


def get_diff(app_id: str, before: str, after: str) -> str:
    """
    Clone de Mendix repo, parseer gewijzigde .mxunit bestanden naar leesbare markdown,
    en voeg tekst-diffs toe voor java/js/scss bestanden. Gecapt op DIFF_CHAR_LIMIT tekens.
    """
    if MX_LOCAL_REPO:
        repo_url = f"file://{MX_LOCAL_REPO}"
    else:
        repo_url = f"{MX_GIT_BASE_URL}/{app_id}.git"
    auth_header = "Authorization: Basic " + base64.b64encode(f"pat:{MX_PAT}".encode()).decode()
    tmp_dir = tempfile.mkdtemp()
    try:
        clone_args = ["git", "clone", "--depth", "2", "--no-single-branch"]
        if not MX_LOCAL_REPO and MX_GIT_BASE_URL.startswith("https://"):
            clone_args += ["-c", f"http.{MX_GIT_BASE_URL}/.extraHeader={auth_header}"]
        clone_args += [repo_url, tmp_dir]
        subprocess.run(clone_args, check=True, capture_output=True, timeout=60)

        # Lijst van gewijzigde bestanden met status (A=added, M=modified, D=deleted)
        name_status = _git(
            ["diff", "--name-status", f"{before}..{after}"],
            tmp_dir, text=True,
        ).stdout.strip()

        if not name_status:
            return "Geen wijzigingen gevonden."

        sections: list[str] = []
        processed = 0

        for line in name_status.splitlines():
            if processed >= MAX_FILES_PER_DIFF:
                sections.append(f"_(meer dan {MAX_FILES_PER_DIFF} bestanden gewijzigd, rest overgeslagen)_")
                break

            parts = line.split('\t')
            if len(parts) < 2:
                continue
            change_type = parts[0].strip()

            # Renames: R<percent><TAB>old_path<TAB>new_path
            if change_type.startswith('R') and len(parts) == 3:
                old_path, new_path = parts[1].strip(), parts[2].strip()
                if new_path.endswith('.mxunit'):
                    before_doc = _parse_mxunit_at(tmp_dir, before, old_path)
                    after_doc = _parse_mxunit_at(tmp_dir, after, new_path)
                    sections.append(_format_mxunit_change(new_path, before_doc, after_doc))
                    processed += 1
                continue

            path = parts[1].strip()

            if path.endswith('.mxunit'):
                before_doc = _parse_mxunit_at(tmp_dir, before, path) if change_type != 'A' else None
                after_doc = _parse_mxunit_at(tmp_dir, after, path) if change_type != 'D' else None
                sections.append(_format_mxunit_change(path, before_doc, after_doc))
                processed += 1

            elif any(path.startswith(p) for p in ('javasource/', 'javascriptsource/', 'themesource/')):
                diff = _git(
                    ["diff", f"{before}..{after}", "--", path],
                    tmp_dir, text=True,
                ).stdout
                if diff.strip():
                    sections.append(f"### Tekstwijziging: `{path}`\n```\n{diff[:3000]}\n```")
                processed += 1

        return '\n\n'.join(sections)[:DIFF_CHAR_LIMIT]

    except subprocess.CalledProcessError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Git operation failed",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"

if not _SYSTEM_PROMPT_PATH.exists():
    raise RuntimeError(f"System prompt bestand niet gevonden: {_SYSTEM_PROMPT_PATH}")


def _load_system_prompt() -> str:
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


async def review_diff(diff: str) -> str:
    """Send diff to the configured LLM and return the review text."""
    try:
        response = await litellm.acompletion(
            model=LLM_MODEL,
            max_tokens=600,
            messages=[
                {"role": "system", "content": _load_system_prompt()},
                {"role": "user", "content": f"Review this Mendix commit diff:\n\n{diff}"},
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM error: {e}",
        )


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

    loop = asyncio.get_running_loop()
    diff = await loop.run_in_executor(None, get_diff, payload.appId, payload.before, payload.after)

    review_text = await review_diff(diff)
    if TEAMS_WEBHOOK_URL:
        await post_to_teams(
            author=payload.authorName,
            commit_hash=payload.after,
            commit_message=payload.commitMessage,
            branch=payload.branchName,
            review=review_text,
        )
    else:
        print(f"[review] author={payload.authorName} branch={payload.branchName} commit={payload.after[:12]}\n{review_text}")

    return JSONResponse({"status": "ok"})
