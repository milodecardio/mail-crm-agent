# Maintenance — mail-crm-agent

## Wenn der Agent einen Fehler macht
1. Output sofort korrigieren (im Review-Report oder in `kontakte.csv`/`kontakte.json`).
2. Fragen: wiederkehrendes Muster oder Einzelfall?
3. Wiederkehrend (Entscheidungsgrenze) → Regel in `CLAUDE.md` oder dem passenden Skill ergänzen.
4. Einzelfall → als Regressionstest in `evals/test-cases.md` aufnehmen.
5. Sprengt eine neue Regel das Zeilenbudget von `CLAUDE.md` → vorher eine schwächere Regel streichen.

## Geplanter Betrieb — cron (täglich 06:00)
Der vollständige headless Lauf (fetch + Triage + Verbatim-Check + Auto-Export) steckt in `scripts/run.py`:
```bash
# crontab -e
0 6 * * * cd "$HOME/AI/claude code/generated-agents/mail-crm-agent" && ANTHROPIC_API_KEY="sk-ant-..." /usr/bin/python3 scripts/run.py >> cron.log 2>&1
```
Morgens die unklaren Fälle im Dashboard prüfen (`python3 app.py` bzw. `start_app.command`). Beim allerersten Lauf öffnet der Microsoft-Device-Login einmalig (danach Token-Cache). Ohne `ANTHROPIC_API_KEY` läuft der konservative Fallback (alles Relevante → Review).

Alternative (macOS-nativ): `launchd`-Plist mit `StartCalendarInterval` auf 06:00, ruft dasselbe `run.py`.

## Preflight-Check (läuft bei jedem Start)
`app.py`, `run.py` und `process.py` rufen vor jeder Arbeit `scripts/preflight.py` auf:
- **Instanz-Sperre:** Läuft schon eine Instanz (Lockfile `.agent.lock` mit lebender PID) → Abbruch mit Hinweis. Verwaiste Locks abgestürzter Prozesse werden automatisch entfernt.
- **Schrott-Check:** Korrupte Status-JSONs werden nach `quarantine/` verschoben (kein Absturz); veraltete Zwischendateien (`mails.json`, `triage_results.json`) werden gemeldet.
- **Override:** `--force` überschreibt eine bestehende Sperre; `--clean` (bei `run.py`) entfernt Zwischendateien. Bleibt `.agent.lock` nach einem harten Absturz liegen, wird es beim nächsten Start als stale erkannt und entfernt.
- `quarantine/` gelegentlich sichten und leeren.

## Port / Start
- `app.py` bindet an `127.0.0.1` und sucht ab Port 5050 automatisch den nächsten freien — der Browser wird auf den tatsächlich genutzten Port geöffnet. Festen Port erzwingen: `PORT=8765 python3 app.py`.
- Startet nichts: prüfe, dass du im Agent-Ordner bist (der Launcher macht `cd "$(dirname "$0")"` — nicht mehr `~/ai-agent`).

## Observability-Signale
| Symptom | Zuerst prüfen | Dann prüfen |
|---|---|---|
| Erfundene Kontaktdaten tauchen auf | `verify_verbatim.py` läuft im Pfad? `evidence`-Mapping | Schwellen in `guide.md` |
| Zu viele Fälle im Review | Confidence-Schwelle (0.85) | Kategorie-Definitionen in `guide.md` |
| Echte Leads landen als irrelevant | Exclude-Muster (zu aggressiv?) | Kontextregeln in `triage-mail` |
| Bestell-/Kundennummern als Telefon | `NON_PHONE_CONTEXT`-Liste erweitern | Telefon-Regeln in `guide.md` |
| Duplikate im CRM | Dedup-Logik in `export.py` | E-Mail-Normalisierung |
| Veraltete Kursinfos | `[Review:]`-Marker in `guide.md` | `templates/current-facts.md` |

## Review-Kadenz
| Was | Frequenz | Aktion |
|---|---|---|
| `templates/current-facts.md` | Quartalsweise | Kursangebot, Saison aktualisieren |
| `guide.md` | Halbjährlich | Kategorien, Exclude-Muster, Telefonregeln prüfen |
| `skills/*.md` | Wenn Outputs driften | Beispiele/Trigger nachschärfen |
| `evals/test-cases.md` | Nach jedem relevanten Fehler | Regressionstest ergänzen |
| `CLAUDE.md` | Quartalsweise | Regeln streichen, die Claude ohnehin befolgt |
| Microsoft-Token | Bei Login-Fehler | `token_cache.bin` löschen → erneuter Device-Login |

## Pruning-Regeln
- Regel, die Claude konsequent ohne Anweisung befolgt → entfernen.
- Regel für einen Einzelfall, die nie wieder griff → entfernen.
- Zwei widersprüchliche Regeln → eine behalten, Begründung dokumentieren.
- `CLAUDE.md` über Budget → Inhalt in Skill/Template verschieben.
