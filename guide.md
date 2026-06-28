# Guide — mail-crm-agent Domänenwissen

Nachschlagewissen für Klassifikation und Extraktion. Wird geladen, wenn ein Skill es braucht.

## Kurskontext (Robert)
- Unterrichtet Musikkurse, Schwerpunkt **Ukulele** und **Gitarre**.
- Anbieter teils **VHS** (Volkshochschule), teils Privatunterricht.
- Typische Anliegen: Kursanfrage, Anmeldung, Verfügbarkeit, Preise, Termine, Schnupperstunde, Lehrauftrag durch eine VHS.

## Kategorie-Definitionen
| Kategorie | Bedeutung | Typische Signale |
|---|---|---|
| `neuer_interessent` | Erstkontakt mit Interesse an Kurs/Unterricht | „Bieten Sie … an?", „würde gern lernen", „Verfügbarkeit", „Preise" |
| `bestandskunde` | Bezieht sich auf laufenden/früheren Kurs, ist bereits Teilnehmer | „in meinem Kurs", „nächste Stunde", „Fortsetzung" |
| `organisation` | VHS-/Kooperations-/Verwaltungskontakt mit persönlichem Bezug | „könnten Sie den Kurs übernehmen", VHS-Mitarbeiter:in schreibt persönlich |
| `absage` | Storniert, sagt ab, kein Interesse mehr | „muss leider absagen", „doch kein Interesse", „kündige" |
| `irrelevant` | Kein Kontakt extrahieren | siehe Exclude-Muster + Automatik-Mails |

## Exclude-Muster (Vorfilter, NICHT Letztentscheid)
Adress-/Inhaltsmuster, die fast immer auf Massen-/System-Mails deuten:
`noreply`, `no-reply`, `donotreply`, `newsletter`, `mailer-daemon`, `versand`, `redaktion`, `ticket@`, `tips`, `news`.

**Wichtig:** Diese Muster lösen *Prüfung* aus, nicht automatischen Ausschluss. Schreibt ein realer Mensch persönlich von einer `@vhs…`-Adresse, ist die Mail relevant (`organisation` oder `neuer_interessent`). Eine reine Automatik-Mail von privater Adresse ist irrelevant. Entscheidend ist der Kontext.

## Confidence-Schwellen
- `confidence ≥ 0.85` → Auto-Export (CSV + JSON).
- `confidence < 0.85` → Review-Report (Robert entscheidet).
- Reine Betreff-ohne-Body-Mails: Entscheidung nur auf Betreff → `confidence` deutlich senken (i. d. R. < 0.85 → Review).

## Verbatim-Extraktionsregeln
- Jedes Feld muss wörtlich im Quelltext (Body, Betreff) oder in den Metadaten (Absendername, Absenderadresse) vorkommen.
- **name:** bevorzugt aus Signatur/Body; sonst Anzeigename. Niemals die E-Mail-Adresse als Name verwenden. Keine Namen aus dem lokalen Teil der Adresse „rekonstruieren".
- **email:** Absenderadresse, oder eine im Body genannte eindeutige Wunsch-Adresse.
- **interest:** genanntes Instrument/Kursart, wörtlich (z. B. „Ukulele", „Gitarre für Anfänger").
- **note:** max. 1 Satz, sachliche Zusammenfassung — darf paraphrasieren, aber keine Fakten hinzufügen, die nicht in der Mail stehen.
- Fehlt ein Beleg → Feld leer (`""`). Nie ableiten.

## Telefon-Format-Regeln
- Akzeptiert: internationale (`+49 …`) und nationale Formate, mit Leerzeichen/Bindestrichen/Klammern, mind. 6 Ziffern.
- Die Nummer muss wörtlich im Text stehen; Normalisierung darf nur Trennzeichen vereinheitlichen, keine Ziffern ändern/ergänzen.
- Verwirf Zahlenketten, die erkennbar keine Telefonnummer sind: Bestell-/Kunden-/Rechnungsnummern, Datumsangaben, IBANs, Postleitzahl-allein.
- Kein eindeutiger Treffer → leeres Feld.

## Empfehlungsregeln (Review-Report)
- Jede Empfehlung nennt das konkrete Signal aus der Mail, auf dem sie beruht.
- Maximal 3 kurze, prägnante Sätze.
- Keine Annahmen über Fakten, die nicht in der Mail stehen.
- Format: Empfehlung + Begründung mit Mail-Beleg (Zitat oder Paraphrase des Signals).

## Datenschutz-Grenzen
- Erfassen: Name, E-Mail, Telefon, Interesse/Kurs, kurze Notiz zum Anliegen.
- Niemals erfassen/ausgeben: besondere Datenkategorien (Gesundheit, Religion, politische Meinung, sexuelle Orientierung, ethnische Herkunft), auch wenn in der Mail erwähnt.
- Daten dienen ausschließlich Roberts Kurs-Kontaktpflege.

## Quelle der technischen Anbindung
Aus dem bestehenden Code übernommen (Stand: Projekt „CRM Agent"):
- Microsoft Graph, Scope `Mail.Read`, MSAL Public-Client **Device-Flow** mit Token-Cache (`token_cache.bin`).
- Abruf: `GET /me/mailFolders/inbox/messages?$select=subject,from,body&$filter=receivedDateTime ge … le …`.

[Review: 2026-12] — Kurskontext, Kursangebot und Exclude-Muster bei Saisonwechsel prüfen.
