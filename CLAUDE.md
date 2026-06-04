# CLAUDE conversation rules
When reporting information to me, be extremely concise and sacrifice grammar for sake of concision.

# mx-review-service

FastAPI service die Mendix Pipeline webhooks ontvangt, de commit reviewt via een agentic LLM-loop, en het resultaat post naar Microsoft Teams.

## Stack

- Python 3.12, FastAPI, uvicorn
- LiteLLM (configurable LLM: Claude, OpenAI, Gemini, Azure OpenAI)
- mxcli v0.12.0 — standalone Go binary, geen JDK nodig
- httpx, python-dotenv
- pytest + pytest-asyncio

## Projectstructuur

```
server.py               # FastAPI app — webhook endpoint, background task
agent/
  loop.py               # agentic review loop (LiteLLM tool use, max 25 calls, timeout)
  tools.py              # read-only mxcli tools + LiteLLM TOOL_SCHEMAS
  repo.py               # git clone, .mpr discovery, cleanup
prompts/
  system_prompt.md      # instructies voor de navigerende agent
tests/
  test_server.py
  test_agent_loop.py
  test_agent_tools.py
  test_agent_repo.py
Dockerfile
requirements.txt
.env.example
```

## Lokaal draaien

```bash
uv venv .venv && uv pip install -r requirements.txt --python .venv/bin/python
cp .env.example .env  # vul waarden in
.venv/bin/uvicorn server:app --reload --port 8000
```

Lokaal testen zonder HTTP (rechtstreeks `_run_review` aanroepen):
```bash
.venv/bin/python test_local.py
```

## Tests

```bash
.venv/bin/pytest tests/ -v
```

## Omgevingsvariabelen

| Variabele | Beschrijving |
|-----------|-------------|
| `MX_PAT` | Mendix Personal Access Token |
| `LLM_MODEL` | LiteLLM model string (bijv. `claude-sonnet-4-20250514`, `gpt-4o`, `gemini/gemini-1.5-pro`, `azure/gpt-4o`) |
| `TEAMS_WEBHOOK_URL` | Incoming Webhook URL van een Teams kanaal |
| `WEBHOOK_SECRET` | Gedeeld geheim voor HMAC-SHA256 verificatie |
| `ALLOWED_APP_IDS` | Kommagescheiden lijst van toegestane Mendix App GUIDs |
| `REVIEW_TIMEOUT_SECONDS` | Max duur van de agent loop (default: `300`) |
| `MX_LOCAL_REPO` | Pad naar lokale Mendix repo (slaat clone over) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / ... | API key van de gekozen LLM provider |

## Request flow

```
POST /review
  → HMAC-SHA256 verificatie (replay protection 5 min)
  → input validatie (appId allowlist, commit hash formaat)
  → 202 Accepted terug aan Mendix webhook
  → background task:
      git clone --depth 50 (of MX_LOCAL_REPO)
      agent loop: LLM roept mxcli tools aan om project te navigeren
        get_diff()        → MDL-diff van gewijzigde elementen
        get_context(name) → definitie + callers + entiteiten + pagina's
        lint_project()    → kwaliteitsregels
        search(query)     → zoeken in captions/expressies
      → Teams Adaptive Card
```

## mxcli tools (read-only)

De agent heeft uitsluitend read-only tools. Schrijf-commando's van mxcli zijn structureel niet geïmplementeerd.

| Tool | mxcli commando |
|---|---|
| `get_diff()` | `mxcli diff-local -p app.mpr --ref before..after` |
| `get_context(name, depth=2)` | `mxcli context -p app.mpr <name> --depth <depth>` |
| `lint_project()` | `mxcli lint -p app.mpr` |
| `search(query)` | `mxcli search -p app.mpr <query>` |

## TODO

- Teams Adaptive Card verder uitwerken.
- mxcli pinnen op nieuwe versie bij updates (huidig: v0.12.0 in Dockerfile).
