# Design: Senior Dev Review System Prompt

**Date:** 2026-05-29
**Status:** Approved

## Probleem

De huidige system prompt werkt als een linter: vaste prefixen, drempelwaarden, checklists. Hij mist de redeneerlaag van een senior developer — wat gaat er daadwerkelijk mis, en hoe erg is dat?

## Beslissingen

- **Aanpak:** Severity-first flat list (🔴/🟡/🟢), gesorteerd op ernst
- **Max 8 bevindingen** per review, ~300 tokens output
- **Geen naamgeving/conventies** — volledig weggelaten
- **Geen categoriekopjes** — bevindingen spreken voor zich
- **Security basis:** The S-Unit Top 10 (Mendix-specifiek)

## Redeneergebieden voor de LLM

### Security (S-Unit Top 10)
- Entity access zonder of met onjuiste XPath constraints (TSU-02)
- Microflow access rights + "Apply entity access" misbruik (TSU-03)
- Hardcoded secrets in constants of default values (TSU-09)
- Published REST/web services zonder authenticatie (TSU-04)
- Consumed integrations zonder input validatie (TSU-05)
- Custom Java met XPath injection of sudo-context misbruik (TSU-08)
- User roles met te brede module-koppelingen (TSU-01)
- Custom auth: insecure login handlers of request handlers (TSU-07)

### Data-integriteit
- Commits halverwege een microflow zonder rollback-mogelijkheid
- Verplichte velden zonder validatieregel
- Delete behavior niet expliciet → potentieel orphaned objects
- Event handlers met onverwachte cascade-effecten

### Business Logic
- Verkeerde split-condities (off-by-one, null-check ontbreekt)
- Validatie die omzeild kan worden (bijv. alleen client-side)
- Edge cases: lege lijsten, nul-waarden die niet worden afgevangen
- Scheduled events zonder error handling

### Reliability
- Externe calls zonder timeout of error handling
- Loops zonder exit-conditie of met potentieel oneindige iteraties
- N+1 patronen bij datasets die in productie groot worden
- Publiek aanroepbare microflows zonder input-validatie

## Outputformaat

```
<één zin: wat is gewijzigd en wat is de impact>

🔴 <bestandsnaam> — <bevinding>
🟡 <bestandsnaam> — <bevinding>
🟢 <bestandsnaam> — <bevinding>
```

- Taal: Nederlands
- Toon: direct, technisch
- Alleen problemen — geen positieve observaties
- Als niets: alleen "Geen bevindingen."

## Archief

Vorige prompt opgeslagen als `prompts/system_prompt_v1.md`.
