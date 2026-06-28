# Entwicklungschronik – Mail CRM Agent

Chronologisches Archiv aller Entwicklungsschritte. Wird bei jeder Session durch Claude Code ergänzt.

---

## 2026-06-27

### Automatischer CSV-Export nach jedem Lauf
- Neue Funktion `export_csv_snapshot()`: speichert nach jedem erfolgreich abgeschlossenen Lauf automatisch eine CSV-Datei
- Dateiname enthält den Analysezeitraum: `kontakte_STARTDATUM_ENDDATUM.csv`
- Export wird im Log bestätigt (`💾 CSV gespeichert: …`)
- Nur bei regulärem Abschluss — abgebrochene/gestoppte Läufe exportieren nicht

### Build-Anzeige und Entwicklungschronik
- `BUILD`-Konstante: liest automatisch den Änderungszeitstempel von `app.py` — kein manuelles Pflegen nötig
- Build-Datum und -Uhrzeit werden in der UI unterhalb des Titels angezeigt
- `log_build_start()`: schreibt bei jedem Serverstart einen Eintrag in `build_history.json` (Startzeitpunkt + Build-Version) → automatische Laufzeitchronik

---

## 2026-06-28

### Multi-Postfach-Support (Shared Mailboxes)
- Drei Postfächer konfiguriert: `rb@`, `gitarre@`, `tenor@robert-beckert.de`
- Architektur: ein einziger Login (rb@) mit Scope `Mail.Read.Shared` — gilt für alle geteilten Postfächer
- URL-Builder auf `/users/{email}/mailFolders/inbox` umgestellt (statt `/me/...`)
- Agent scannt alle ausgewählten Postfächer sequenziell mit demselben Token
- UI: Postfach-Checkboxen im Zeitraum-Tile — Auswahl wird vor jedem Start übergeben
- Status zeigt aktuell gescanntes Postfach (`📬 Aktuelles Postfach: …`)
- `start_app.command` und `MailCRM.app` auf venv-Python umgestellt (verhindert Startfehler nach Umzug)
- Token-Cache-Migration: `token_cache.bin` → `token_cache_rb_robert-beckert_de.bin`

---

## 2026-06-14 (Initialversion)

### Grundarchitektur (MASTER PROMPT V6)
- Flask-Server auf Port 5050 mit Browser-UI
- Microsoft Graph API-Anbindung via MSAL (Device-Flow-Login, Token-Cache)
- Keyword-basiertes Relevanz-Filtering (`vhs`, `Ukulele`, `Gitarre`, `Kurs`)
- Exclude-Patterns für Automatik-Mails, Newsletter, System-Absender
- `upsert_contact()`: Deduplication per E-Mail-Adresse, Treffer-Zähler, Keyword- und Telefon-Merge
- Telefonnummer-Extraktion via Regex aus Mail-Body
- Dynamische Kontakt-Typen (`Ukulele/Gitarre-Interessent` etc.)
- CSV-Download auf Knopfdruck

### Persistenz & Robustheit
- Atomares Schreiben aller JSON-Dateien (`.tmp` + `os.replace`)
- `progress_state.json`: Fortsetzungspunkt bei Abbruch — Resume-Popup beim nächsten Start
- `contacts_data.json` + `logs_data.json`: überleben Neustarts
- Graceful Stop: speichert Zustand vor Prozessende

### macOS-Integration
- `start_app.command`: beendet alten Prozess auf Port 5050, startet App im Hintergrund, öffnet Browser
- `MailCRM.app`: Dock-startfähiges App-Bundle
