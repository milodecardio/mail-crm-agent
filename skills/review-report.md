---
name: review-report
description: Aus allen unklaren Triage-Fällen (route=review) einen interaktiven HTML-Report bauen — je Fall Checkboxen mit Entscheidungsoptionen und einer belegten Empfehlung (max. 3 Sätze). Auswahl wird als decisions.json exportierbar.
---

# review-report

Erzeuge einen eigenständigen HTML-Report für die Fälle, die `triage-mail` mit `route: "review"` markiert hat. Robert arbeitet ihn interaktiv ab.

## Inputs

**Aus dem Lauf:** Liste der Review-Items (je Item: Absender, Betreff, Body-Auszug, vorgeschlagene Kategorie, ggf. Teil-Kontaktdaten, `reason`).

**Aus Projektdateien:** `guide.md` (Empfehlungsregeln, Kategorien), `templates/review-report.html` (Gerüst).

## Ablauf
1. Für jedes Item die **Entscheidungsoptionen** als Checkboxen anlegen: die fünf Kategorien (`neuer_interessent`, `bestandskunde`, `organisation`, `absage`, `irrelevant`) plus „in CRM aufnehmen: ja/nein".
2. Je Item eine **Empfehlung** formulieren — max. 3 kurze Sätze, die ein **konkretes Signal aus der Mail** zitieren. Keine Fakten hinzufügen, die nicht in der Mail stehen.
3. Belegpflichtige Kontaktfelder, die `triage-mail` extrahiert hat, anzeigen — als editierbare Felder, vorbelegt nur mit wörtlich belegten Werten.
4. Report aus `templates/review-report.html` rendern. Der „Export"-Button schreibt die Auswahl als `decisions.json` (Download), das `export-crm` beim nächsten Lauf einliest.

## Empfehlungs-Format (Pflicht)
> **Empfehlung:** [eine klare Handlung]. **Beleg:** [wörtliches Signal aus der Mail]. [optional: 1 Satz Einordnung].

## Klärungs-Trigger
- Lässt sich aus der Mail keine belegbare Empfehlung ableiten → Empfehlung „Manuell prüfen" mit Hinweis, welches Signal fehlt. Nie eine Empfehlung erfinden.

## Gutes Beispiel (Empfehlung)
> **Empfehlung:** Als `neuer_interessent` aufnehmen. **Beleg:** „würde im Januar gern mit Gitarre anfangen". Kursart genannt, aber keine Telefonnummer — Feld leer lassen.

## Schlechtes Beispiel (NICHT so)
> **Empfehlung:** Vermutlich ein zahlungskräftiger Privatschüler aus gutem Umfeld, hohe Abschlusswahrscheinlichkeit.

Falsch: spekuliert über Fakten (Zahlungskraft, Umfeld, Abschluss), die nicht in der Mail stehen, und überschreitet implizit den Beleg-Grundsatz.
