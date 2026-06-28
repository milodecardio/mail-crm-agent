#!/usr/bin/env python3
"""run.py — Headless Orchestrator für den geplanten (cron-)Lauf.

Ablauf, vollautomatisch:
  1. fetch_mail.fetch_messages() holt neue Mails seit Lauf-Wasserzeichen (state.json).
  2. triage.triage_mail() klassifiziert + extrahiert je Mail (LLM, sonst Fallback).
  3. verify_verbatim.verify() bereinigt jedes Urteil deterministisch (Anti-Halluzination).
  4. Routing: route=export → auto_export.json ; route=review → review_queue.json.
  5. export.py schreibt die sicheren Treffer sofort nach kontakte.csv + kontakte.json.
  6. review-report.html (eigenständig) wird zusätzlich gerendert.

Review-Fälle warten in review_queue.json auf Robert (Flask-Dashboard app.py).
Beim Approve dort entsteht decisions.json und export.py läuft erneut (Merge).

Aufruf:  python3 scripts/run.py   (z. B. via cron, siehe MAINTENANCE.md)
"""
import json, os, subprocess, sys, datetime

sys.path.insert(0, os.path.dirname(__file__))
import fetch_mail            # noqa: E402
import triage as triage_mod  # noqa: E402
from verify_verbatim import verify  # noqa: E402

BASE = os.path.join(os.path.dirname(__file__), "..")
TEMPLATE = os.path.join(BASE, "templates", "review-report.html")


def _p(name):
    return os.path.join(BASE, name)


def _load(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def render_report(items):
    html = open(TEMPLATE, encoding="utf-8").read()
    html = html.replace("/*__REVIEW_ITEMS__*/ []", json.dumps(items, ensure_ascii=False))
    out = _p("review-report.html")
    open(out, "w", encoding="utf-8").write(html)
    return out


def run(maxn: int = 1000) -> dict:
    state = _load(_p("state.json"), {})
    since = state.get("last_received")

    mails = fetch_mail.fetch_messages(since=since, maxn=maxn)
    system = triage_mod.build_system_prompt()

    auto, review = [], []
    for mail in mails:
        raw = triage_mod.triage_mail(mail, system)
        source_text = " ".join(str(mail.get(k, "")) for k in ("subject", "body", "from_name", "from_email"))
        t = verify(raw, source_text)
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

    # Sichere Treffer sofort exportieren (Review-Fälle folgen nach Approve im Dashboard)
    subprocess.run([sys.executable, _p("scripts/export.py")], check=False)

    summary = {"fetched": len(mails), "auto": len(auto), "review": len(review),
               "ran_at": datetime.datetime.utcnow().isoformat() + "Z"}
    json.dump(summary, open(_p("last_run.json"), "w"), ensure_ascii=False, indent=2)
    return summary


if __name__ == "__main__":
    import preflight
    if not preflight.run_and_report("run", force="--force" in sys.argv, clean_transient="--clean" in sys.argv):
        sys.exit(1)
    try:
        s = run()
        print(f"✅ Lauf fertig: {s['fetched']} Mails · {s['auto']} auto-exportiert · {s['review']} im Review")
    except Exception as e:
        print(f"❌ Lauf fehlgeschlagen: {e}", file=sys.stderr)
        sys.exit(1)
