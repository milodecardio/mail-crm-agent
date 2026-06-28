#!/usr/bin/env python3
"""process.py — Deterministische Nach-Triage-Pipeline (Engine-unabhängig).

Nimmt Mails + extern erzeugte Triage-Urteile entgegen (z. B. von Claude Code,
das die Triage über dein Abo macht — keine API-Kosten), und erledigt den Rest
verlässlich per Code:
  1. Verbatim-Check (verify_verbatim) — leert nicht belegbare Felder.
  2. Routing in Auto-Export (confidence ≥ Schwelle) vs. Review-Queue.
  3. review-report.html rendern.
  4. export.py → kontakte.csv + kontakte.json.

Eingaben (gleiche Reihenfolge / per "ref" zugeordnet):
  mails.json           : Liste von {subject, body, from_name, from_email, received}
  triage_results.json  : Liste von Triage-JSONs (Schema siehe templates/output-schema.md)

Zuordnung: per Index, oder — falls vorhanden — per Feld "ref" (z. B. from_email+received).

Aufruf:
  python3 scripts/process.py --mails mails.json --triage triage_results.json
"""
import argparse, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(__file__))
from verify_verbatim import verify  # noqa: E402

BASE = os.path.join(os.path.dirname(__file__), "..")
TEMPLATE = os.path.join(BASE, "templates", "review-report.html")


def _p(name):
    return os.path.join(BASE, name)


def _ref(mail):
    return f"{mail.get('from_email','')}|{mail.get('received','')}"


def render_report(items):
    html = open(TEMPLATE, encoding="utf-8").read()
    html = html.replace("/*__REVIEW_ITEMS__*/ []", json.dumps(items, ensure_ascii=False))
    open(_p("review-report.html"), "w", encoding="utf-8").write(html)


def process(mails, triage_results):
    # Zuordnung: per "ref", sonst per Index
    by_ref = {t.get("ref"): t for t in triage_results if t.get("ref")}
    auto, review = [], []
    for i, mail in enumerate(mails):
        t = by_ref.get(_ref(mail)) or (triage_results[i] if i < len(triage_results) else None)
        if not t:
            continue
        source_text = " ".join(str(mail.get(k, "")) for k in ("subject", "body", "from_name", "from_email"))
        t = verify(t, source_text)
        t["received"] = mail.get("received", "")
        if t.get("route") == "export" and t.get("relevant"):
            auto.append(t)
        elif t.get("route") == "review":
            review.append({
                "from_name": mail.get("from_name", ""), "from_email": mail.get("from_email", ""),
                "subject": mail.get("subject", ""), "received": mail.get("received", ""),
                "body_excerpt": (mail.get("body", "") or "")[:600],
                "category": t.get("category", ""), "contact": t.get("contact", {}),
                "recommendation": t.get("reason", ""), "suggest_include": t.get("relevant", False),
            })

    json.dump(auto, open(_p("auto_export.json"), "w"), ensure_ascii=False, indent=2)
    json.dump(review, open(_p("review_queue.json"), "w"), ensure_ascii=False, indent=2)
    if review:
        render_report(review)
    subprocess.run([sys.executable, _p("scripts/export.py")], check=False)
    return {"auto": len(auto), "review": len(review)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mails", default=_p("mails.json"))
    ap.add_argument("--triage", default=_p("triage_results.json"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    import preflight
    if not preflight.run_and_report("process", force=args.force):
        sys.exit(1)

    mails = json.load(open(args.mails, encoding="utf-8"))
    triage = json.load(open(args.triage, encoding="utf-8"))
    s = process(mails, triage)
    print(f"✅ {s['auto']} auto-exportiert · {s['review']} im Review")


if __name__ == "__main__":
    main()
