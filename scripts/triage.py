#!/usr/bin/env python3
"""triage.py — LLM-Triage einer einzelnen Mail (für headless/automatische Läufe).

Baut den System-Prompt aus den Projektdateien (guide.md + skills/triage-mail.md +
templates/output-schema.md) — eine einzige Quelle der Wahrheit. Ruft die Anthropic-API
und gibt das Triage-JSON zurück. Der deterministische Verbatim-Check (verify_verbatim)
wird vom Orchestrator (run.py) NACH der Triage angewandt, unabhängig vom Modell.

Ohne API-Key (ANTHROPIC_API_KEY) greift ein konservativer Fallback:
keine Extraktion erfundener Felder, alles potenziell Relevante → Review, klare
Massen-/noreply-Mails → drop. So bleibt die Pipeline auch offline nutzbar.
"""
import json, os, re

BASE = os.path.join(os.path.dirname(__file__), "..")
MODEL = os.environ.get("TRIAGE_MODEL", "claude-sonnet-4-6")
THRESHOLD = float(os.environ.get("TRIAGE_THRESHOLD", "0.85"))

# Fallback-Heuristik (nur ohne API-Key) — gespiegelt aus guide.md
_KEYWORDS = ("vhs", "ukulele", "gitarre", "kurs", "unterricht", "anfänger", "schnupper")
_EXCLUDE = ("noreply", "no-reply", "donotreply", "newsletter", "mailer-daemon",
            "versand", "redaktion", "ticket@")


def _read(path):
    p = os.path.join(BASE, path)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def build_system_prompt() -> str:
    return (
        "Du bist die Triage-Komponente des mail-crm-agent. Bewerte GENAU EINE E-Mail.\n"
        "Gib AUSSCHLIESSLICH ein einzelnes JSON-Objekt nach dem Schema unten zurück — "
        "kein Text davor oder danach.\n\n"
        "REGELN (Auszug, verbindlich):\n"
        "- Kontaktfelder NUR wörtlich aus der Mail; nicht ableiten/ergänzen. Fehlt der Beleg → Feld \"\".\n"
        "- evidence: für jedes nicht-leere Feld name/phone/interest der wörtliche Quell-Snippet.\n"
        f"- route: \"export\" nur bei relevant=true UND confidence ≥ {THRESHOLD}; sonst \"review\"; irrelevant → \"drop\".\n"
        "- Kontext schlägt Keyword. Keine besonderen Datenkategorien (Gesundheit/Religion etc.).\n\n"
        "=== guide.md ===\n" + _read("guide.md") + "\n\n"
        "=== skill: triage-mail ===\n" + _read("skills/triage-mail.md") + "\n\n"
        "=== output-schema ===\n" + _read("templates/output-schema.md") + "\n"
    )


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("Kein JSON im Modell-Output gefunden")
    return json.loads(m.group(0))


def triage_via_api(mail: dict, system: str) -> dict:
    import anthropic  # nur wenn tatsächlich genutzt
    client = anthropic.Anthropic()  # liest ANTHROPIC_API_KEY
    user = json.dumps({
        "from_name": mail.get("from_name", ""), "from_email": mail.get("from_email", ""),
        "subject": mail.get("subject", ""), "body": mail.get("body", ""),
        "received": mail.get("received", ""),
    }, ensure_ascii=False)
    msg = client.messages.create(
        model=MODEL, max_tokens=700,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return _parse_json(msg.content[0].text)


def triage_fallback(mail: dict) -> dict:
    """Konservativ, ohne LLM: keine erfundenen Daten, Unklares → review."""
    blob = f"{mail.get('subject','')} {mail.get('body','')}".casefold()
    email = (mail.get("from_email", "") or "").casefold()
    name = mail.get("from_name", "") or ""
    is_noise = any(p in email for p in _EXCLUDE)
    hit = any(k in blob for k in _KEYWORDS)
    if is_noise and not hit:
        return {"relevant": False, "category": "irrelevant", "confidence": 0.9,
                "route": "drop", "contact": {"name": "", "email": "", "phone": "",
                "interest": "", "note": ""}, "evidence": {}, "reason": "Massen-/noreply-Mail (Fallback)."}
    # Nur Metadaten als belegbare Felder; Rest leer; immer in Review (kein LLM-Urteil).
    return {"relevant": bool(hit), "category": "neuer_interessent" if hit else "irrelevant",
            "confidence": 0.5 if hit else 0.3, "route": "review" if hit else "drop",
            "contact": {"name": name, "email": mail.get("from_email", ""), "phone": "",
                        "interest": "", "note": ""},
            "evidence": {"name": name} if name else {},
            "reason": "Fallback ohne LLM: bitte manuell prüfen." if hit else "Kein Kursbezug erkannt (Fallback)."}


def _claude_cli_path():
    """Pfad zur `claude`-CLI, falls installiert (für kostenfreie Triage über das Abo)."""
    import shutil
    return shutil.which("claude")


def available_mode() -> str:
    """Wählt den Triage-Modus: 'api' (API-Key) > 'cli' (Claude-Abo) > 'fallback'.
    Erzwingbar über env TRIAGE_MODE."""
    forced = os.environ.get("TRIAGE_MODE")
    if forced in ("api", "cli", "fallback"):
        return forced
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if _claude_cli_path():
        return "cli"
    return "fallback"


def _triage_via_cli(mails: list, timeout: int = None) -> list:
    """Triage über die lokale Claude-CLI (`claude -p`, nutzt dein Abo, keine API-Kosten).
    Schreibt mails.json, lässt Claude triage_results.json erzeugen, liest es zurück."""
    import subprocess
    json.dump(mails, open(os.path.join(BASE, "mails.json"), "w"), ensure_ascii=False, indent=2)
    prompt = (
        "Lies die Datei mails.json im aktuellen Ordner. Triagiere JEDE Mail strikt nach dem "
        "Skill triage-mail und den Regeln in CLAUDE.md/guide.md. Schreibe ein JSON-Array aller "
        "Triage-Urteile (Schema: templates/output-schema.md) nach triage_results.json — pro Mail "
        "zusätzlich das Feld \"ref\" = \"<from_email>|<received>\". Erfinde nichts; nicht belegbare "
        "Felder bleiben leer. Gib am Ende nur DONE aus."
    )
    out = os.path.join(BASE, "triage_results.json")
    if os.path.exists(out):
        os.remove(out)
    # Timeout grob an Mailmenge koppeln (min. 5 Min, ~3s/Mail), Obergrenze 30 Min.
    timeout = timeout or max(300, min(1800, 30 + 3 * len(mails)))
    # stdin schließen → headless, kein Warten auf Eingabe. acceptEdits erlaubt Schreiben ohne Rückfrage.
    for flags in (["--permission-mode", "acceptEdits"], ["--dangerously-skip-permissions"], []):
        try:
            subprocess.run([_claude_cli_path(), "-p", prompt, *flags],
                           cwd=BASE, timeout=timeout, check=True,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
            if os.path.exists(out):
                return json.load(open(out, encoding="utf-8"))
        except Exception:
            continue
    raise RuntimeError("Claude-CLI-Triage lieferte kein triage_results.json")


def triage_batch(mails: list, mode: str = None) -> tuple:
    """Triagiert eine Liste Mails im besten verfügbaren Modus.
    Gibt (results, mode_used) zurück. Fällt bei Fehlern sicher auf die Heuristik zurück."""
    mode = mode or available_mode()
    try:
        if mode == "cli":
            return _triage_via_cli(mails), "cli"
        if mode == "api":
            system = build_system_prompt()
            return [triage_via_api(m, system) for m in mails], "api"
    except Exception:
        pass  # auf Fallback ausweichen
    return [triage_fallback(m) for m in mails], "fallback"


def triage_mail(mail: dict, system: str = None) -> dict:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return triage_via_api(mail, system or build_system_prompt())
        except Exception as e:  # API-Fehler → konservativer Fallback statt Absturz
            r = triage_fallback(mail)
            r["reason"] = f"[API-Fehler, Fallback] {e}"
            r["route"] = "review" if r.get("relevant") else r["route"]
            return r
    return triage_fallback(mail)


if __name__ == "__main__":
    import sys
    data = json.load(sys.stdin)
    mails = data if isinstance(data, list) else [data]
    sysp = build_system_prompt()
    json.dump([triage_mail(m, sysp) for m in mails], sys.stdout, ensure_ascii=False, indent=2)
