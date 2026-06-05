# README Redesign Spec — mx-review-service

**Date:** 2026-06-05
**Status:** Approved

## Goal

Fully rewrite the README in English with a clear structure suited for both internal developers and external contributors. Replace the current patchy, mixed-language document with a well-structured reference.

## Decisions

- Language: English throughout
- Audience: Both internal devs (fast onboarding) and external (enough context to understand the project)
- Request flow: Mermaid diagram
- Setup tooling: `uv` (not `pip`)

## Sections (in order)

### 1. Hero
- Project name + one-liner description
- 4 bullet capability highlights: multi-LLM support, agentic mxcli navigation, Teams Adaptive Card output, HMAC-signed webhooks

### 2. How it works
- Mermaid `sequenceDiagram` showing: Mendix → webhook → HMAC verify → background task → git clone → agent loop → Teams
- Short paragraph explaining the agentic loop: LLM calls mxcli tools (get_diff, get_context, lint_project, search) iteratively, max 25 tool calls, configurable timeout

### 3. Prerequisites
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for package management
- mxcli v0.12.0 binary (available in the Dockerfile; download separately for local use)
- API key for chosen LLM provider

### 4. Quick start
5 steps max:
1. Clone + `uv venv .venv && uv pip install -r requirements.txt --python .venv/bin/python`
2. `cp .env.example .env` and fill values
3. `.venv/bin/uvicorn server:app --reload --port 8000`
4. Expose with ngrok
5. Point Mendix Pipeline POST step at the URL

### 5. Configuration reference
Full env var table including all variables:
- `MX_PAT`, `LLM_MODEL`, `TEAMS_WEBHOOK_URL`, `WEBHOOK_SECRET`, `ALLOWED_APP_IDS`
- `REVIEW_TIMEOUT_SECONDS` (default: 300)
- `MX_LOCAL_REPO` (skips git clone, uses local path)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` / Azure vars

### 6. LLM providers
Provider table with updated model IDs:
- Anthropic: `claude-sonnet-4-6`
- OpenAI: `gpt-4o`
- Gemini: `gemini/gemini-1.5-pro`
- Azure OpenAI: `azure/gpt-4o`
Link to LiteLLM docs.

### 7. Docker / Podman
- Build + run with Docker
- Note Podman compatibility (rootless)
- mxcli v0.12.0 included in image

### 8. Mendix Pipeline setup
Step-by-step: Portal → Pipelines → POST Request step → URL → Secret field

### 9. Local testing
- curl example with HMAC signature generation
- `test_local.py` for running without HTTP

### 10. Running tests
```bash
.venv/bin/pytest tests/ -v
```

## Out of scope
- Badges (no CI/CD configured)
- Contribution guide
- Changelog
