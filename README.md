# Mendix Commit Review Service

Receives Mendix Pipeline webhooks, fetches the git diff, reviews it with an LLM of your choice (Claude, OpenAI, Gemini, Azure OpenAI, ...), and posts results to Microsoft Teams.

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
| `LLM_MODEL` | Model string voor LiteLLM (zie voorbeelden hieronder) |
| `TEAMS_WEBHOOK_URL` | Incoming Webhook URL from a Teams channel connector |
| `WEBHOOK_SECRET` | Shared secret configured in the Mendix Pipeline POST Request step |
| `ALLOWED_APP_IDS` | Comma-separated list of allowed Mendix App GUIDs |

### LLM provider kiezen

Stel `LLM_MODEL` in en voeg de bijbehorende API key toe:

| Provider | `LLM_MODEL` | API key env var |
|----------|-------------|-----------------|
| Anthropic Claude | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o` | `OPENAI_API_KEY` |
| Google Gemini | `gemini/gemini-1.5-pro` | `GEMINI_API_KEY` |
| Azure OpenAI | `azure/gpt-4o` | `AZURE_API_KEY` + `AZURE_API_BASE` + `AZURE_API_VERSION` |

Zie ook [LiteLLM providers](https://docs.litellm.ai/docs/providers) voor alle opties.

## Testing with curl

Generate a valid signature and send a test request:

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
