# Output-Schema — mail-crm-agent

Verbindliches Format für Triage-Urteil, CSV und JSON.

## Triage-Urteil (ein JSON-Objekt je Mail)
```json
{
  "relevant": true,
  "category": "neuer_interessent | bestandskunde | organisation | absage | irrelevant",
  "confidence": 0.0,
  "route": "export | review | drop",
  "contact": {
    "name": "", "email": "", "phone": "", "interest": "", "note": ""
  },
  "evidence": {
    "name": "", "phone": "", "interest": ""
  },
  "reason": ""
}
```
- `contact`-Felder: leer (`""`), wenn nicht wörtlich belegbar.
- `evidence`: wörtlicher Quell-Snippet je nicht-leerem belegpflichtigem Feld (name/phone/interest). Wird von `verify_verbatim.py` geprüft.
- `route`: `export` nur bei `relevant=true` und `confidence ≥ 0.85`; sonst `review`; `irrelevant` → `drop`.

## kontakte.json (Array)
```json
[
  {
    "name": "", "email": "", "phone": "", "interest": "",
    "category": "", "note": "", "received": "2026-06-10T08:30:00Z",
    "confidence": 0.95, "source": "auto"
  }
]
```

## kontakte.csv (Spalten, UTF-8, mit Header)
`name,email,phone,interest,category,note,received,confidence,source`

## state.json (Lauf-Wasserzeichen)
```json
{ "last_received": "2026-06-10T08:30:00Z", "last_run": "2026-06-14T06:00:00Z" }
```
