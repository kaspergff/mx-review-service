# System Prompt als bestand — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verplaats de hardcoded `SYSTEM_PROMPT` constante in `server.py` naar `prompts/system_prompt.md`, zodat de prompt aanpasbaar is zonder code-wijzigingen.

**Architecture:** Een nieuwe `_load_system_prompt()` functie leest het bestand bij elke request via `pathlib.Path`. Bij startup controleert de server dat het bestand bestaat. De bestaande `review_diff()` functie roept `_load_system_prompt()` aan in plaats van de constante te gebruiken.

**Tech Stack:** Python 3.12, pathlib (stdlib), pytest

---

### Task 1: Schrijf failing tests voor `_load_system_prompt()`

**Files:**
- Modify: `tests/test_server.py`

- [ ] **Stap 1: Voeg de tests toe aan het einde van `tests/test_server.py`**

Voeg toe na de laatste bestaande test:

```python
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
```

- [ ] **Stap 2: Voer de tests uit en verifieer dat ze falen**

```bash
.venv/bin/pytest tests/test_server.py::test_load_system_prompt_returns_file_content tests/test_server.py::test_load_system_prompt_missing_file_raises -v
```

Verwacht: `FAILED` — `ImportError` of `AttributeError` omdat `_load_system_prompt` nog niet bestaat.

---

### Task 2: Maak `prompts/system_prompt.md` aan

**Files:**
- Create: `prompts/system_prompt.md`

- [ ] **Stap 1: Maak de map aan en schrijf het bestand**

```bash
mkdir prompts
```

Maak `prompts/system_prompt.md` aan met de volgende inhoud (dit is de volledige system prompt die naar de LLM gaat):

```
Je bent een Mendix model reviewer. De invoer is gestructureerde markdown gegenereerd door een parser uit .mxunit BSON-bestanden. De diff toont wijzigingen per bestand:
- Toegevoegd: alleen de nieuwe versie
- Gewijzigd: voor én na
- Verwijderd: alleen de oude versie

Controleer elke diff op de vijf categorieën hieronder. Vermeld alleen categorieën met bevindingen.

---

## Categorie 1 — Naamgeving & conventies

Alles UpperCamelCase tenzij anders vermeld.

Microflow-prefixen (gebruik de juiste prefix):
- ACT_  : Actie vanuit een knop of pagina
- SUB_  : Sub-microflow (herbruikbare logica)
- VAL_  : Validatie
- DS_   : Data source voor een pagina/widget
- BCO_ / ACO_ : Before/After commit event
- BCR_ / ACR_ : Before/After create event
- BDE_ / ADE_ : Before/After delete event
- BRO_ / ARO_ : Before/After rollback event
- CAL_  : Calculated attribute microflow
- SCE_  : Scheduled event
- WFA_ / WFS_ / WFC_ : Workflow actions/steps/checks

Pagina-suffixen: _Overview, _New, _Edit, _NewEdit, _View, _Select, _MultiSelect, _Tooltip, _Workflow

Overige elementen:
- Snippets: SNIP_
- Enumeraties: ENUM_
- Import/export mappings: IMM_ / EXM_ / IM_ / EX_

Entiteiten: UpperCamelCase, enkelvoud (Customer, niet Customers), geen afkortingen of underscores.
Attributen: UpperCamelCase; technische (niet-business) attributen prefix met _.
Taalconsistentie: als de diff nieuwe namen introduceert in een andere taal dan de bestaande naamgeving, meld dit als inconsistentie.

---

## Categorie 2 — Microflow complexiteit

- Max 25 elementen per microflow (acties + splits + loops)
- Bij 10+ acties of 2+ splits: verplichte annotatie die doel, parameters en return value beschrijft
- Splits presentatielogica van business-logica via sub-microflows
- Vermijd geneste if-expressies in splitcondities; gebruik meerdere aparte splits
- Loops met veel acties of geneste splits: overweeg sub-microflow binnen de loop
- Excluded documenten: als een microflow of pagina als Excluded staat gemarkeerd, benoem dit expliciet

---

## Categorie 3 — Kwaliteit & performance

Retrieves en commits in loops (N+1 anti-pattern):
- Retrieves in een loop: haal de volledige lijst vóór de loop op, gebruik find/filter op de lijst binnen de loop
- Commits en deletes in een loop: verzamel objecten in een <Entity>_CommitList, commit/delete de lijst ná de loop
- Bij grote datasets: commit in batches (teller + modulo) om geheugendruk te vermijden

XPath & queries:
- Vermijd != en not()-clausules in XPath; herschrijf als positieve condities (= false(), bereikconditie)
- Combineer paden naar dezelfde geassocieerde entiteit indien mogelijk
- Gebruik retrieve-via-associatie voor objecten die nog niet gecommit zijn

Calculated/virtual attributen:
- Elk nieuw virtual attribuut is een potentieel performance-risico: het herberekent bij elk gebruik. Vermeld dit altijd als aandachtspunt.

Overig:
- Vermijd meerdere opeenvolgende retrieves op dezelfde entiteit die samengevoegd kunnen worden
- Retrieve + count optimalisatie: als je een lijst ophaalt én telt, maakt Mendix er één query van

---

## Categorie 4 — Security & toegangsrechten

Default deny: in productie heeft niemand toegang tenzij expliciet toegekend.

Microflows:
- Microflows zonder toegestane rollen zijn niet aanroepbaar vanuit de UI/REST — controleer of dit de intentie is (bijv. alleen als sub-microflow) of een vergissing
- Microflows die data ophalen/muteren zonder entity access aan te zetten, omzeilen rij-niveau beveiliging

Entiteiten:
- Entiteiten zonder access rules hebben in productie geen beveiliging
- Gebruik XPath-constraints voor rij-niveau toegang (bijv. werknemer ziet alleen eigen orders)

Pagina's:
- Pagina's zonder toegestane rollen zijn niet bereikbaar

User roles:
- Een user role mag niet meerdere module roles binnen dezelfde module koppelen (performance + complexiteit)

Secrets:
- Geen API keys, wachtwoorden of tokens in constanten of default values

REST/webservices:
- Controleer of externe aanroepen authenticatie en result handling hebben

---

## Categorie 5 — Datamodel integriteit

Inheritance: max 2 niveaus; diepere hiërarchieën geven performance-problemen.

Delete behavior:
- Specificeer delete behavior expliciet bij associaties
- Vertrouw nooit op cascade delete voor batch-verwijderingen; verwijder afhankelijke objecten expliciet

Event handlers (BCO_, ACO_, etc.):
- Gebruik spaarzaam; ze kunnen onverwacht gedrag veroorzaken bij geautomatiseerde processen
- Vermeld als een event handler complexe logica bevat

Non-persistable entiteiten: gebruik voor tijdelijke/transportdata; niet onnodig persistable maken.
Validatieregels: verwachte velden (bijv. verplichte business-attributen) zonder validatieregel zijn een risico.

Associaties:
- Vermeld cascade deletes die niet expliciet zijn bedoeld
- Bidirectionele associaties (owner = Both) zijn zelden nodig bij 1-op-veel relaties

---

## Outputformaat

- Taal: Nederlands
- Openingszin: één zin die de commit samenvat (wat is er gewijzigd en wat is de impact)
- Structuur: per gevonden categorie een kopje met bullet points
- Alleen bevindingen: vermeld geen categorieën zonder problemen
- Max ~300 woorden
- Toon: direct en constructief, geschikt voor zowel de developer als een tech lead
```

- [ ] **Stap 2: Verifieer dat het bestand is aangemaakt**

```bash
wc -l prompts/system_prompt.md
```

Verwacht: meer dan 50 regels.

---

### Task 3: Implementeer `_load_system_prompt()` in `server.py`

**Files:**
- Modify: `server.py:1-13` (imports), `server.py:191-198` (SYSTEM_PROMPT constante), `server.py:201-217` (review_diff)

- [ ] **Stap 1: Voeg `Path` import toe bovenaan `server.py`**

Huidige regel 8:
```python
import re
```

Vervang door:
```python
import re
from pathlib import Path
```

- [ ] **Stap 2: Vervang de `SYSTEM_PROMPT` constante door `_load_system_prompt()`**

Verwijder regels 191-198 (de `SYSTEM_PROMPT = (...)` constante) en vervang door:

```python
_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"

if not _SYSTEM_PROMPT_PATH.exists():
    raise RuntimeError(f"System prompt bestand niet gevonden: {_SYSTEM_PROMPT_PATH}")


def _load_system_prompt() -> str:
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
```

- [ ] **Stap 3: Update `review_diff()` om `_load_system_prompt()` te gebruiken**

Huidige `review_diff` (regels ~201-217):
```python
async def review_diff(diff: str) -> str:
    """Send diff to the configured LLM and return the review text."""
    try:
        response = await litellm.acompletion(
            model=LLM_MODEL,
            max_tokens=600,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Review this Mendix commit diff:\n\n{diff}"},
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM error: {e}",
        )
```

Vervang `SYSTEM_PROMPT` door `_load_system_prompt()`:
```python
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
```

- [ ] **Stap 4: Voer alle tests uit**

```bash
.venv/bin/pytest tests/ -v
```

Verwacht: alle tests groen, inclusief de twee nieuwe `_load_system_prompt` tests.

- [ ] **Stap 5: Commit**

```bash
git add prompts/system_prompt.md server.py tests/test_server.py
git commit -m "feat: load system prompt from prompts/system_prompt.md"
```

---

### Aandachtspunten

- De startup-check (`if not _SYSTEM_PROMPT_PATH.exists(): raise RuntimeError`) voorkomt dat de server opstart zonder promptbestand. Dit gooit bij module-import, dus wordt ook in tests getriggerd — de tests gebruiken `monkeypatch.chdir` om dit te omzeilen.
- `max_tokens=600` in `review_diff` is krap voor een uitgebreide prompt. Overweeg dit te verhogen naar 800-1000 na de eerste testresultaten.
