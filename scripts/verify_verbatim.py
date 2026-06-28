#!/usr/bin/env python3
"""verify_verbatim.py — Deterministischer Anti-Halluzinations-Check.

Stellt sicher, dass jedes belegpflichtige Kontaktfeld (name, phone, interest)
WÖRTLICH in der Quellmail (subject + body + from_name + from_email) vorkommt.
Felder, deren Wert nicht als Substring belegbar ist, werden geleert — der Agent
darf nichts ausgeben, was nicht in der Quelle steht.

Telefonnummern werden zusätzlich auf ein plausibles Format geprüft und beim
Vergleich von Trennzeichen (Leerzeichen, -, (), /) befreit, damit reine
Formatierung nicht als Abweichung zählt.

Aufruf:
    echo '<triage-json>' | python3 verify_verbatim.py --source source.json
oder programmatisch via verify(triage, source_text).
"""
import argparse, json, re, sys

VERIFIED_FIELDS = ("name", "phone", "interest")
SEP = re.compile(r"[\s\-()./]+")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s\-]?)?(?:\(?\d{2,5}\)?[\s\-]?)\d{2,}(?:[\s\-]?\d{2,}){0,4}")

# Schlüsselwörter, die unmittelbar VOR einer Zahlenkette stehen und sie als
# Nicht-Telefonnummer ausweisen (Bestell-/Kunden-/Rechnungsnummern etc.).
NON_PHONE_CONTEXT = (
    "bestellnummer", "bestell-nr", "bestellnr", "rechnungsnummer", "rechnungsnr",
    "kundennummer", "kundennr", "auftragsnummer", "auftragsnr", "vertragsnummer",
    "ticketnummer", "ticket", "iban", "steuernummer", "steuer-nr", "ust-id",
    "plz", "postleitzahl", "az", "aktenzeichen",
)


def _norm(s: str) -> str:
    return (s or "").casefold()


def _norm_phone(s: str) -> str:
    return SEP.sub("", s or "")


def is_plausible_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value or "")
    if not (6 <= len(digits) <= 15):
        return False
    return bool(PHONE_RE.search(value or ""))


def has_non_phone_context(value: str, source_text: str) -> bool:
    """True, wenn die Zahlenkette im Quelltext direkt hinter einem
    Nicht-Telefon-Schlüsselwort steht (z. B. 'Bestellnummer 8841-22')."""
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return False
    pattern = r"[\s\-()./]*".join(re.escape(d) for d in digits)
    m = re.search(pattern, source_text or "")
    if not m:
        return False
    pre = (source_text[max(0, m.start() - 30):m.start()]).casefold()
    return any(kw in pre for kw in NON_PHONE_CONTEXT)


def verify(triage: dict, source_text: str) -> dict:
    """Leert nicht belegbare Felder, gibt (bereinigtes triage, report) zurück."""
    src = _norm(source_text)
    src_phone = _norm_phone(source_text)
    contact = dict(triage.get("contact", {}))
    report = {"cleared": [], "kept": []}

    for field in VERIFIED_FIELDS:
        val = contact.get(field, "") or ""
        if not val:
            continue
        if field == "phone":
            ok = (is_plausible_phone(val)
                  and _norm_phone(val) in src_phone
                  and not has_non_phone_context(val, source_text))
        else:
            ok = _norm(val) in src
        if ok:
            report["kept"].append(field)
        else:
            contact[field] = ""
            report["cleared"].append(field)

    triage = dict(triage)
    triage["contact"] = contact
    # Confidence kappen, wenn etwas geleert wurde, und ggf. ins Review routen.
    if report["cleared"]:
        triage["confidence"] = min(triage.get("confidence", 0.0), 0.84)
        if triage.get("relevant") and triage.get("route") == "export":
            triage["route"] = "review"
    triage["_verify"] = report
    return triage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="JSON mit {subject, body, from_name, from_email}")
    args = ap.parse_args()
    triage = json.load(sys.stdin)
    with open(args.source, encoding="utf-8") as f:
        s = json.load(f)
    source_text = " ".join(str(s.get(k, "")) for k in ("subject", "body", "from_name", "from_email"))
    out = verify(triage, source_text)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
