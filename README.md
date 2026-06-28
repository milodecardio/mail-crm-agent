# mail-crm-agent

Kurs-Lead-CRM-Agent für Robert. Liest das Outlook-Postfach, erkennt echte Kurs-Interessenten, extrahiert **ausschließlich belegbare** Kontaktdaten, kategorisiert sie und routet jeden unklaren Fall in einen interaktiven Review-Report — statt zu raten.

## Ein-Klick-App fürs Dock (am einfachsten)
`MailCRM.app` im Agent-Ordner ist der Ein-Klick-Launcher:
1. Im Finder den Agent-Ordner öffnen, **`MailCRM.app`** beim ersten Mal per **Rechtsklick → Öffnen** starten (einmalige Gatekeeper-Bestätigung für unsignierte Apps), danach normal per Doppelklick.
2. Ins **Dock ziehen** → künftig genügt ein Klick.
3. Es öffnet sich das Dashboard im Browser. Dort **Von/Bis-Datum** wählen und **▶ Run** klicken — der Lauf macht in einem Rutsch: Abruf → Triage → Verarbeiten → Anzeige.

Die Triage wählt automatisch den besten kostenfreien Weg: **Claude-Abo** (wenn die `claude`-CLI installiert ist) › **API-Key** (falls gesetzt) › **Heuristik-Fallback**. Welcher Modus lief, steht oben im Dashboard. Läuft die App schon, öffnet ein erneuter Klick einfach das bestehende Dashboard (keine zweite Instanz).

> Hinweis: `MailCRM.app` muss im Agent-Ordner bleiben (das Dock hält nur eine Verknüpfung). Logs stehen in `app.log`.

## Quick Start (Dashboard, Hybrid)
```bash
cd generated-agents/mail-crm-agent
pip install -r requirements.txt        # einmalig
python3 app.py                         # oder im Finder: start_app.command doppelklicken
```
Das Dashboard öffnet sich im Browser auf `http://127.0.0.1:5050` (oder dem nächsten freien Port). Dort: **Jetzt Mails prüfen**, offene Review-Fälle abhaken, **Übernehmen & exportieren**, CSV/JSON herunterladen.

### API-Key für automatische Triage
Für die LLM-Triage einen Anthropic-Key setzen:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# optional: export TRIAGE_MODEL="claude-sonnet-4-6"   (Standard) | "claude-haiku-4-5-20251001" (günstiger)
```
Ohne Key läuft ein **konservativer Fallback**: nichts wird erfunden, alles potenziell Relevante landet im Review.

## Ablauf
1. **Abruf** — `fetch_mail.fetch_messages()` holt neue Mails seit dem Lauf-Wasserzeichen (`state.json`).
2. **Triage** — `scripts/triage.py` klassifiziert je Mail (LLM oder Fallback) und extrahiert Kontaktfelder *wörtlich*.
3. **Verbatim-Check** — `scripts/verify_verbatim.py` leert jedes nicht belegbare Feld (deterministisch).
4. **Routing** — `confidence ≥ 0.85` → Auto-Export; darunter → Review-Queue.
5. **Review** — im Dashboard (oder eigenständig `review-report.html`): Checkboxen + belegte Empfehlung → `decisions.json`.
6. **Export** — `scripts/export.py` schreibt `kontakte.csv` + `kontakte.json` (Dedup, idempotent).

## Zeitraum-Testlauf — gesamte Mailbox, kostenfrei über Claude Code
Scannt **alle Server-Ordner außer Junk/Spam und Papierkorb** in einem Datumsbereich und
triagiert über dein Claude-Abo (kein API-Schlüssel, keine API-Kosten). Für den ersten Test
z. B. 01.01.2024–31.05.2026. (Bei Bedarf einbeziehen: `--include-junk`, `--include-deleted`.)

```bash
cd "generated-agents/mail-crm-agent"
# 1) Mails des Zeitraums holen (öffnet beim ersten Mal den Microsoft-Device-Login)
python3 scripts/fetch_mail.py --start 2024-01-01 --end 2026-05-31 > mails.json
```
```bash
# 2) In Claude Code triagieren lassen (nutzt dein Abo):
claude
> Lies mails.json. Triagiere jede Mail nach dem Skill triage-mail und schreibe
> das Array der Triage-JSONs (Schema: templates/output-schema.md, plus Feld
> "ref" = "<from_email>|<received>") nach triage_results.json.
```
```bash
# 3) Deterministisch verifizieren, routen, exportieren (kein LLM, kein Kosten):
python3 scripts/process.py --mails mails.json --triage triage_results.json
# 4) Ergebnisse prüfen: Dashboard öffnen
python3 app.py
```
Sichere Treffer stehen dann in `kontakte.csv`/`kontakte.json`, unklare Fälle in der Review-Queue
des Dashboards. Der Verbatim-Check stellt auch hier sicher: nichts wird erfunden.

## Betriebsmodi
- **Hybrid (empfohlen):** cron startet nachts `scripts/run.py` (fetch + Triage + Auto-Export), morgens prüfst du die Review-Queue im Dashboard. Siehe MAINTENANCE.md.
- **Manuell:** Dashboard öffnen → „Jetzt Mails prüfen".
- **In Claude Code:** `cd … && claude` und die Skills `triage-mail` / `review-report` / `export-crm` direkt nutzen.

## Wichtige Dateien
- `app.py` — Flask-Dashboard (robuster Port: 127.0.0.1 + Auto-Fallback).
- `scripts/run.py` — headless Orchestrator (für cron).
- `scripts/triage.py` — LLM-Triage + Fallback.
- `scripts/verify_verbatim.py` — deterministische Anti-Halluzination.
- `scripts/fetch_mail.py`, `scripts/export.py` — Graph-Abruf, CSV/JSON-Export.

## Maintenance
Siehe `MAINTENANCE.md` für Review-Kadenz und Fehlerbehandlung.
