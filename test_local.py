"""
Lokale integratietest: roept _run_review direct aan met echte commits.
Vereist: .env gevuld (LLM_MODEL, ANTHROPIC_API_KEY of andere provider).

Gebruik:
    .venv/bin/python test_local.py
"""
import asyncio
import logging
from unittest.mock import MagicMock

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

import subprocess, os
repo = os.environ.get("MX_LOCAL_REPO", "")
if not repo:
    raise SystemExit("Fout: MX_LOCAL_REPO is niet ingesteld in .env")

def _git(ref: str) -> str:
    return subprocess.check_output(["git", "-C", repo, "rev-parse", ref], text=True).strip()

BEFORE = _git("HEAD~1")
AFTER  = _git("HEAD")


async def main():
    from server import _run_review, ReviewRequest

    payload = MagicMock(spec=ReviewRequest)
    payload.appId = "test-app"
    payload.before = BEFORE
    payload.after = AFTER
    payload.branchName = "main"
    payload.authorName = "Test"
    payload.commitMessage = "engels -> nederlands"

    print(f"\nReviewing {BEFORE[:12]}..{AFTER[:12]}\n")
    await _run_review(payload)
    print("\nKlaar — check output hierboven of Teams webhook.")


asyncio.run(main())
