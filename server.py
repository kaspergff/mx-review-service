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

app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"status": "ok"}
