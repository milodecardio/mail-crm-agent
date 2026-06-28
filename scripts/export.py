#!/usr/bin/env python3
"""export.py — Bestätigte Kontakte nach kontakte.csv + kontakte.json schreiben.

- Merge aus Auto-Export-Items (route=export) und decisions.json (Review-bestätigt).
- Dedup per E-Mail (case-insensitive). Fehlende Felder werden ergänzt, vorhandene
  wörtliche Werte NICHT überschrieben.
- Idempotent: erneuter Lauf über dieselben Daten ändert die Ausgabe nicht.
- Aktualisiert das Lauf-Wasserzeichen in state.json.
"""
import argparse, csv, json, os

COLUMNS = ["name", "email", "phone", "interest", "category", "note", "received", "confidence", "source"]


def load(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def to_contact(item, source):
    c = item.get("contact", item)
    return {
        "name": c.get("name", ""), "email": (c.get("email", "") or "").strip(),
        "phone": c.get("phone", ""), "interest": c.get("interest", ""),
        "category": item.get("category", ""), "note": c.get("note", item.get("note", "")),
        "received": item.get("received", ""), "confidence": item.get("confidence", ""),
        "source": item.get("source", source),
    }


def merge(existing, new):
    by_email = {(c.get("email") or "").casefold(): c for c in existing if c.get("email")}
    for c in new:
        key = (c.get("email") or "").casefold()
        if not key:
            existing.append(c); continue
        if key in by_email:
            cur = by_email[key]
            for f in COLUMNS:
                if not cur.get(f) and c.get(f):
                    cur[f] = c[f]
        else:
            by_email[key] = c; existing.append(c)
    return existing


def main():
    ap = argparse.ArgumentParser()
    base = os.path.join(os.path.dirname(__file__), "..")
    ap.add_argument("--auto", default=os.path.join(base, "auto_export.json"))
    ap.add_argument("--decisions", default=os.path.join(base, "decisions.json"))
    ap.add_argument("--csv", default=os.path.join(base, "kontakte.csv"))
    ap.add_argument("--json", default=os.path.join(base, "kontakte.json"))
    ap.add_argument("--state", default=os.path.join(base, "state.json"))
    args = ap.parse_args()

    new = []
    for it in load(args.auto, []):
        if it.get("route") == "export" and it.get("relevant"):
            new.append(to_contact(it, "auto"))
    for d in load(args.decisions, []):
        if d.get("include"):
            new.append(to_contact(d, "review"))

    contacts = merge(load(args.json, []), new)

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(contacts, f, ensure_ascii=False, indent=2)
    with open(args.csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS); w.writeheader()
        for c in contacts:
            w.writerow({k: c.get(k, "") for k in COLUMNS})

    received = [c.get("received") for c in contacts if c.get("received")]
    if received:
        state = load(args.state, {})
        state["last_received"] = max(received)
        json.dump(state, open(args.state, "w"), ensure_ascii=False, indent=2)

    print(f"{len(contacts)} Kontakte geschrieben → {os.path.basename(args.csv)}, {os.path.basename(args.json)}")


if __name__ == "__main__":
    main()
