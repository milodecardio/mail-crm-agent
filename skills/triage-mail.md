---
name: triage-mail
description: Eine einzelne Outlook-Mail klassifizieren (relevant + Kategorie + Confidence) und Kontaktfelder ausschließlich wörtlich extrahieren. Entscheidet das Confidence-Routing (Auto-Export vs. Review-Report).
---

# triage-mail

Bewerte genau **eine** Mail und gib ein strukturiertes Urteil zurück. Kein Begleittext.

## Inputs

**Vom Mailabruf (Graph):**
- `from_name`, `from_email`, `subject`, `body` (HTML-bereinigt), `received` (ISO)

**Aus Projektdateien:**
- `guide.md` — Kategorie-Definitionen, Exclude-Muster, Confidence-Schwellen, Verbatim- und Telefon-Regeln.

## Ablauf

1. **Vorfilter prüfen** (Exclude-Muster aus `guide.md`): Trifft ein Muster zu, prüfe den Kontext — ist es eine reine Automatik-/Massen-Mail → `irrelevant`. Persönlicher Mensch trotz `@vhs` → weiter.
2. **Relevanz & Kategorie** kontextbasiert bestimmen (siehe Kategorie-Tabelle).
3. **Kontaktfelder wörtlich extrahieren.** Jeder Wert muss als Substring in `body`, `subject` oder Metadaten vorkommen. Fehlt der Beleg → Feld `""`. Telefon nach den Telefon-Regeln; verwirf Nicht-Telefonnummern.
4. **Confidence** vergeben (0.0–1.0). Nur-Betreff-Mails → niedrig.
5. **Routing:** `confidence ≥ 0.85` und `relevant=true` → `route: "export"`; sonst `route: "review"`. `irrelevant` → `route: "drop"`.

## Klärungs-Trigger (→ route: review)
- Persönlich wirkende Mail, aber Kursbezug nur vage.
- Body fehlt oder ist abgeschnitten.
- Mehrdeutige Adresse (z. B. `info@`-Sammeladresse mit persönlichem Text).
- Telefon-Kandidat unklar (könnte Bestellnummer sein).

## Output (ein JSON-Objekt, siehe templates/output-schema.md)

```json
{
  "relevant": true,
  "category": "neuer_interessent",
  "confidence": 0.93,
  "route": "export",
  "contact": { "name": "", "email": "", "phone": "", "interest": "", "note": "" },
  "evidence": { "name": "", "phone": "", "interest": "" },
  "reason": ""
}
```
`evidence`: für jedes nicht-leere belegpflichtige Feld der wörtliche Quell-Snippet, in dem der Wert steht (wird von `verify_verbatim.py` geprüft).

## Gutes Beispiel
**Mail:** `Sabine Keller <s.keller@gmx.de>` — Betreff „Ukulele für Anfänger?" — Body: „… Bieten Sie im Herbst einen Ukulele-Anfängerkurs an? … Erreichbar unter 0151 23456789. Viele Grüße, Sabine Keller"
```json
{
  "relevant": true, "category": "neuer_interessent", "confidence": 0.95, "route": "export",
  "contact": {"name":"Sabine Keller","email":"s.keller@gmx.de","phone":"0151 23456789","interest":"Ukulele-Anfängerkurs","note":"Fragt nach Ukulele-Anfängerkurs im Herbst."},
  "evidence": {"name":"Viele Grüße, Sabine Keller","phone":"Erreichbar unter 0151 23456789","interest":"einen Ukulele-Anfängerkurs"},
  "reason": "Persönliche Erstanfrage mit klarem Kursinteresse und belegten Kontaktdaten."
}
```

## Schlechtes Beispiel (NICHT so)
**Mail:** Body nennt keinen Namen, Adresse `kontakt2024@gmx.de`, keine Telefonnummer.
```json
{ "contact": {"name":"Kontakt 2024","phone":"+49 ...","interest":"Gitarre"} }
```
Falsch: `name` aus der Adresse rekonstruiert, `phone` erfunden, `interest` nicht belegt. Richtig wäre `name:""`, `phone:""`, und `interest` nur wenn wörtlich genannt — sonst `route: review`.
