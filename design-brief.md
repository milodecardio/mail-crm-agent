# Design Brief: mail-crm-agent

> Kurs-Lead-CRM-Agent für Robert. Scannt das Outlook-Postfach, erkennt echte Kurs-Interessenten, extrahiert ausschließlich belegbare Kontaktdaten, kategorisiert sie und routet jeden unklaren Fall in einen interaktiven Review-Report — statt zu raten.

## Classification
- **Type:** Operational/Workflow (primär) · Internal-Knowledge (sekundär — personenbezogene Daten Dritter)
- **Tier:** 3 — hochgestuft von Tier 2, weil die Anforderung lautet: Halluzinationen vollständig ausschließen, perfekte/belegbare Ergebnisse, DSGVO-relevante Daten. Tier 3 bedeutet hier: volle Fehler-Taxonomie, harte Eval-Gates, adversariale Tests, Source-of-Truth-Präzedenz. (Der Systemprompt bleibt trotzdem schlank — Risiko wird über Skills, Validatoren und Evals gemanagt, nicht über Prompt-Länge.)

## Deployment Target
- **Standalone-Workspace**
- Zielpfad: `generated-agents/mail-crm-agent/`
- Begründung: Der Agent ist das eigenständige Hauptwerkzeug (eigener Login, eigener Lauf-Loop, eigener Output), kein Helfer in einem fremden Codebase. Start via `cd generated-agents/mail-crm-agent && claude`.

## Scope
**Was er tut:**
- Holt Outlook-Posteingangsmails (Zeitraum oder „seit letztem Lauf") über Microsoft Graph (`Mail.Read`, MSAL Device-Flow mit Token-Cache — aus bestehendem Code übernommen).
- Klassifiziert jede Mail kontextbasiert: relevant (echter Kurs-Bezug) vs. irrelevant — ersetzt die starre Keyword-Logik.
- Extrahiert Kontaktdaten **ausschließlich wörtlich** aus Text/Metadaten (Name, E-Mail, Telefon, Interesse, Notiz).
- Kategorisiert: `neuer_interessent` · `bestandskunde` · `organisation` · `absage` · `irrelevant`.
- **Confidence-Routing:** sichere Treffer → direkt CSV + JSON. Unklare Fälle → **interaktiver Review-Report** (HTML, Checkboxen, je Fall eine belegte Empfehlung in max. 3 Sätzen).
- Läuft **geplant/automatisch** (z. B. täglich), mit Dedup und „seit letztem Lauf"-Wasserzeichen.

**Was er NICHT tut:**
- Keine Kontaktdaten erfinden oder „plausibel ergänzen".
- Keine endgültige Entscheidung bei unklaren Mails (das entscheidest du im Report).
- Keine Antwortmails senden, keine Schreibzugriffe auf externe Systeme.
- Keine besonderen Datenkategorien (Gesundheit, Religion etc.) extrahieren, selbst wenn im Text.

## Top Failure Modes (nach Kosten gerankt)
1. **Halluzinierte Kontaktdaten** (erfundene Nummer/Name/Adresse) — *kritisch*. Verhindert durch: Regel „nur wörtliche Substrings der Quelle"; **deterministischer Substring-Validator** (Script prüft, dass jedes Feld 1:1 im Quelltext vorkommt — sonst leer); Eval-Hard-Gate (erfundenes Feld = no-ship).
2. **Fehlklassifikation** (Newsletter → CRM, oder echter Lead verloren) — verhindert durch: Confidence-Schwelle + Unklar→Review-Report (Mensch im Loop); gute + schlechte Beispiele im Skill; Exclude-Heuristik als Vorfilter, nicht als Letztentscheid.
3. **Halluzinierte Empfehlung im Report** — verhindert durch: Empfehlung muss ein **konkretes Signal aus der Mail** zitieren, max. 3 Sätze; Eval-Gate prüft Beleg-Bezug.
4. **Vertraulichkeit / DSGVO** (Überspeicherung personenbezogener Daten) — verhindert durch: Datensparsamkeit (nur definierte Felder), Confidentiality-Boundaries, Verbot besonderer Kategorien.
5. **Duplikate / veraltete Kontakte bei geplanten Läufen** — verhindert durch: Dedup per E-Mail, Lauf-Wasserzeichen, idempotenter Export.

## Skill Plan
| Skill | Zweck | Inputs | Hauptrisiko |
|---|---|---|---|
| `triage-mail` | Eine Mail klassifizieren (relevant + Kategorie + Confidence) und Kontaktfelder **wörtlich** extrahieren; Confidence-Routing entscheiden | von Graph: Absender, Betreff, Body, Datum · aus Dateien: Kategorie-Defs, Exclude-Liste, Schwellen | Halluzination / Fehlklassifikation |
| `review-report` | Interaktiven HTML-Report für unklare Fälle bauen: Checkboxen je Entscheidungsoption + belegte Empfehlung (max. 3 Sätze); Auswahl wird als `decisions.json` exportierbar | unklare Items aus `triage-mail` | halluzinierte/zu lange Empfehlung |
| `export-crm` | Bestätigte Kontakte nach `kontakte.csv` **und** `kontakte.json` schreiben; Dedup; Lauf-Wasserzeichen aktualisieren | bestätigte Kontakte (auto + aus `decisions.json`) | Format/Dedup-Fehler |

(3 Skills bewusst statt mehr — `extract` ist immer Teilschritt von `triage`, daher zusammengefasst mit zwei getrennten, klar markierten Validierungsschritten.)

## Knowledge Architecture
- `CLAUDE.md` (Tier 3, 80–150 Z.) — Identität, Anti-Halluzinations-Kernregeln, Confidence-Routing, DSGVO-Datensparsamkeit, Ambiguity-Strategie, Context-Loading, Forbidden Outputs.
- `guide.md` — Domänenwissen: Roberts Kurskontext (Ukulele/Gitarre/VHS), exakte Kategorie-Definitionen, Exclude-Muster, Confidence-Schwellen, Telefon-Format-Regeln, Verbatim-Extraktionsregeln. Volatile Fakten mit `[Review: YYYY-MM]`.
- `skills/*.md` — die 3 Skills mit je gutem + schlechtem Beispiel und Klärungs-Triggern.
- `templates/` — `review-report.html` (Report-Gerüst), `current-facts.md` (aktuelles Kursangebot/Saison, quartalsweise), `output-schema.md` (CSV-Spalten + JSON-Schema).
- `evals/test-cases.md` — Hard Gates + bewertete Dimensionen + Edge + Adversarial (Tier-3-Vollabdeckung).
- `scripts/` — `fetch_mail.py` (Graph-Abruf, aus bestehendem Code), `verify_verbatim.py` (Substring-Validator), `export.py` (CSV/JSON + Dedup), `run.py` (Orchestrator für geplante Läufe).

## Tool Requirements
- **Tools/Scripts (deterministisch):** Graph-Mailabruf, Verbatim-Substring-Check, Telefon-Format-Validierung, CSV/JSON-Export + Dedup, Scheduling. „Never send an LLM to do a linter's job."
- **Prompt/LLM (Urteil):** Relevanz-Klassifikation, Kategorisierung, Empfehlungstext im Report.

## Ambiguity Strategy
Operational + Null-Halluzination → zwei feste Regeln:
1. **Extraktion:** Steht ein Feld nicht wörtlich in der Quelle → Feld leer. Niemals ableiten/ergänzen.
2. **Klassifikation:** `confidence ≥ Schwelle` (Default 0.85) → Auto-Export. Darunter → Review-Report, nie stille Endentscheidung.

## Source-of-Truth Precedence (Tier 3)
1. **Wörtlicher Mailtext + Metadaten** — einzige Quelle für Kontaktdaten.
2. **Roberts Kategorie-Definitionen & Exclude-Liste** (`guide.md`).
3. **Allgemeinwissen** — nur für Sprachverständnis, nie zum Erzeugen von Daten.

Bei Konflikt: wörtliche Quelle gewinnt; fehlt sie → Feld leer + Review-Report. Konflikt wird im Report vermerkt.

## Forbidden Outputs
- Jedes Kontaktfeld, das nicht wörtlich in der Quellmail steht.
- Endgültige Kategorisierung einer unklaren Mail ohne Routing in den Report.
- Empfehlung > 3 Sätze oder ohne konkreten Mail-Beleg.
- Besondere Datenkategorien (Gesundheit, Religion, politische Meinung etc.).
- Automatisch gesendete Mails oder Schreibzugriffe auf externe Systeme.

## Open Questions
1. **Scheduler:** macOS-`launchd`/`cron`, der `run.py` täglich startet (Default-Vorschlag), oder dein bestehender App-Loop? → Default: cron-Eintrag, den ich mitliefere.
2. **Confidence-Schwelle:** Default **0.85** für Auto-Export (darunter Review). OK so?
3. **Review-Report-Mechanik:** Self-contained HTML, du hakst Optionen ab und exportierst per Button ein `decisions.json`, das `export-crm` beim nächsten Lauf einliest (kein Server nötig). Passt das, oder soll der Report an die bestehende Flask-Oberfläche (Port 5050) andocken?
