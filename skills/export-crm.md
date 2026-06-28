---
name: export-crm
description: Bestätigte Kontakte (Auto-Export aus triage-mail mit confidence ≥ 0.85 plus die im Review-Report bestätigten Fälle aus decisions.json) nach kontakte.csv und kontakte.json schreiben. Dedup per E-Mail, Lauf-Wasserzeichen aktualisieren.
---

# export-crm

Schreibe die finale Kontaktliste. Diese Phase erzeugt keine neuen Daten — sie konsolidiert nur Bestätigtes.

## Inputs
- Auto-Export-Items aus `triage-mail` (`route: "export"`).
- `decisions.json` aus dem Review-Report (von Robert bestätigte Fälle).
- Bestehende `kontakte.json` (für Dedup) und `state.json` (Lauf-Wasserzeichen).

## Ablauf
1. Auto- und Review-bestätigte Kontakte zusammenführen.
2. **Dedup per E-Mail** (case-insensitive): existiert der Kontakt bereits, nicht duplizieren; ggf. fehlende Felder ergänzen, vorhandene wörtliche Werte nicht überschreiben.
3. Nach `kontakte.csv` (Spalten siehe `templates/output-schema.md`) **und** `kontakte.json` schreiben.
4. Lauf-Wasserzeichen in `state.json` auf den neuesten `received`-Zeitstempel setzen (für „seit letztem Lauf").

## Regeln
- Keine Felder erfinden oder füllen, die leer geliefert wurden.
- Idempotent: ein zweiter Lauf über dieselben Mails ändert die Ausgabe nicht.
- CSV UTF-8 mit Header; JSON ein Array von Kontaktobjekten nach Schema.

## Output-Spalten (CSV)
`name,email,phone,interest,category,note,received,confidence,source`
(`source` = `auto` oder `review`)
