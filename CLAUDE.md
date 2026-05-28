# mx-review-service

FastAPI service die Mendix Pipeline webhooks ontvangt, de commit reviewt via een LLM, en het resultaat post naar Microsoft Teams.

## Stack

- Python 3.12, FastAPI, uvicorn
- LiteLLM (configurable LLM: Claude, OpenAI, Gemini, Azure OpenAI)
- httpx, pymongo (BSON parsing), python-dotenv
- pytest + pytest-asyncio

## Projectstructuur

```
server.py           # FastAPI app — alle endpoints en business logic
mendix/parser.py    # .mxunit BSON parser → leesbare markdown voor de LLM
tests/test_server.py
requirements.txt
.env.example
```

## Lokaal draaien

```bash
uv venv .venv && uv pip install -r requirements.txt --python .venv/bin/python
cp .env.example .env  # vul waarden in
.venv/bin/uvicorn server:app --reload --port 8000
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
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / ... | API key van de gekozen LLM provider |

## Request flow

```
POST /review
  → HMAC-SHA256 verificatie (replay protection 5 min)
  → input validatie (appId allowlist, commit hash formaat)
  → git clone --depth 2 + parse gewijzigde .mxunit bestanden naar markdown
  → LLM review via LiteLLM
  → Teams Adaptive Card
```

## mendix/parser.py

Parseert `.mxunit` BSON bestanden zonder `mx.exe`. Extraheert per document:
- **Microflows**: acties, splits (met expressies), loops, XPath constraints, list operations (filter/find/sort), attribute changes, control flow
- **Domain model**: entiteiten, attributen, associaties (incl. delete behavior), access rules
- **Pages/Snippets/Enumeraties/Constanten**: basisinfo + toegestane rollen
