# Design: Agent-based Mendix Code Review

## Probleemstelling

De huidige service stuurt een statische diff als één grote blob naar een LLM. Dit heeft drie problemen:

1. **Kwaliteit** — het model kan niet doorvragen of verder kijken buiten de diff
2. **Tokenlimiet** — grote commits worden hard afgekapt (`DIFF_CHAR_LIMIT = 15_000`)
3. **Beperkte analyse** — de zelfgeschreven BSON-parser mist context: geen callers, geen impact-analyse, geen cross-module zicht

## Oplossing

Vervang de statische diff-aanpak door een agent loop. Het model krijgt read-only tools waarmee het het Mendix project zelf kan bevragen via **mxcli** — een standalone Go binary die `.mpr` bestanden kan queryen zonder Mendix Studio Pro of mx.exe.

## Architectuur

### Request flow

```
POST /review
  → HMAC verificatie + input validatie       (ongewijzigd)
  → antwoord 202 Accepted aan Mendix webhook  ← nieuw
  → verwerk review als background task:
      git clone (shallow, incl. .mpr)
      agent loop (max 25 tool-calls, REVIEW_TIMEOUT_SECONDS)
      post review naar Teams
      cleanup clone
```

### Waarom 202 + background

De agent loop kan 3-5 minuten duren (clone + mxcli queries + meerdere LLM turns). Mendix webhooks verwachten een snelle response. Door direct 202 terug te sturen en async te verwerken vermijden we retries en timeouts aan de Mendix-kant.

### Nieuwe bestandsstructuur

```
agent/
  loop.py      # agentic loop: LiteLLM tool use, max-calls en timeout bewaking
  tools.py     # tool-definities (read-only mxcli + git diff)
  repo.py      # git clone, pad naar .mpr, cleanup
prompts/
  system_prompt.md   # herschreven voor navigerende agent
```

### Verwijderd

- `mendix/parser.py` en bijbehorende tests
- `get_diff()`, `_parse_mxunit_at()`, `_format_mxunit_change()` uit `server.py`
- `review_diff()` uit `server.py` (vervangen door `agent/loop.py`)

## Tools

Het model krijgt uitsluitend read-only tools. Schrijf-commando's van mxcli (`create`, `execute`, MDL-scripts) worden structureel niet geïmplementeerd — niet als beveiligingslaag, maar simpelweg niet aanwezig.

| Tool | Commando | Beschrijving |
|---|---|---|
| `list_changed_files()` | `git diff --name-status before..after` | Welke elementen zijn gewijzigd |
| `describe_element(name)` | `mxcli describe <name>` | Volledige definitie van een element |
| `find_refs(name)` | `mxcli refs <name>` | Alle inkomende + uitgaande referenties (blast radius: wat gebruikt dit element, wat gebruikt het zelf) |
| `find_callers(name)` | `mxcli callers <name>` | Welke flows roepen dit aan (gerichte call-graph, ondersteunt `--transitive`) |
| `find_callees(name)` | `mxcli callees <name>` | Wat roept dit element aan |
| `lint_project()` | `mxcli lint` | Kwaliteitsregels over het project |
| `search(query)` | `mxcli search <query>` | Zoek door log messages, captions, expressies |

## Agent loop gedrag

- **Max 25 tool-calls** per review — dekt een commit van 10 elementen grondig
- **Configureerbare timeout** via `REVIEW_TIMEOUT_SECONDS` (default: 300s)
- Bij overschrijding van calls of timeout: partial review naar Teams met waarschuwing, geen harde fout
- LiteLLM tool use — werkt met Claude, OpenAI, Gemini, Azure OpenAI

## Systeem prompt

De bestaande prompt is geschreven voor een statische diff en wordt herschreven voor een navigerende agent. Kern van de nieuwe instructies:

- Begin altijd met `list_changed_files`
- Beslis zelf wat riskant lijkt en onderzoek dat dieper
- Wees selectief: niet elk element heeft `callers` + `impact` nodig
- Geef de final review in hetzelfde formaat: samenvatting + 🔴/🟡/🟢 bullets, max 8 bevindingen, Nederlands

## mxcli en Docker

mxcli is een standalone Go binary. JDK is **waarschijnlijk niet vereist** voor read-only queries (`describe`, `lint`, `callers`). JDK is alleen nodig voor MxBuild (compileren), wat buiten scope is.

**Aanname:** geen JDK in het Docker image.

**Verificatiestap (eerste taak in implementatieplan):** test `mxcli describe <element>` in een Alpine container zonder JDK. Als het faalt, voeg `eclipse-temurin:21-jre-alpine` toe als base.

### Dockerfile (schets)

```dockerfile
FROM python:3.12-slim
RUN apt-get install -y git
RUN curl -L https://github.com/mendixlabs/mxcli/releases/latest/download/mxcli-linux-amd64 -o /usr/local/bin/mxcli \
    && chmod +x /usr/local/bin/mxcli
COPY . /app
```

## Nieuwe omgevingsvariabelen

| Variabele | Beschrijving | Default |
|---|---|---|
| `REVIEW_TIMEOUT_SECONDS` | Max duur van de agent loop | `300` |

## Tests

- `agent/tools.py`: unit tests met gemockt mxcli subprocess
- `agent/loop.py`: integration tests met gemockte LiteLLM responses en tool-calls
- `server.py`: bestaande webhook tests blijven grotendeels intact; mock de agent loop

## Open punten

- Verifieer of JDK nodig is voor mxcli read-only queries (zie verificatiestap)
- mxcli is v0.12.0 (actief project) — pin de versie in het Dockerfile
- Clone-tijd voor grote `.mpr` bestanden in de cloud nog onbekend; overweeg `--filter=blob:none` voor snellere shallow clone als `.mpr` groot is
