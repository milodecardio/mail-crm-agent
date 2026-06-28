#!/usr/bin/env python3
"""preflight.py — Pflicht-Check vor jedem Agent-Start.

Wird von app.py, run.py und process.py beim Start aufgerufen und prüft IMMER:

  1) Instanz-Sperre: Läuft bereits eine andere Instanz des Agenten?
     - PID-Lockfile (.agent.lock). Lebt der eingetragene Prozess → Abbruch.
     - Toter (stale) Lock wird automatisch entfernt und der Start fortgesetzt.
  2) Schrott-/Integritäts-Check der Arbeitsverzeichnisse:
     - Bekannte Status-Dateien, die kein gültiges JSON sind, werden in
       quarantine/ verschoben (statt den Lauf abstürzen zu lassen).
     - Veraltete Zwischendateien (mails.json, triage_results.json) werden gemeldet;
       mit clean_transient=True entfernt.

API:
    preflight(role, force=False, clean_transient=False) -> dict
        Wirft InstanceRunning, wenn eine lebende Instanz läuft (außer force=True).
        Legt bei Erfolg den Lock an (wird via atexit automatisch entfernt).
"""
import atexit, json, os, shutil, sys, time, datetime

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCK = os.path.join(BASE, ".agent.lock")
QUARANTINE = os.path.join(BASE, "quarantine")

STATE_FILES = ["state.json", "auto_export.json", "review_queue.json",
               "decisions.json", "kontakte.json", "last_run.json"]
TRANSIENT = ["mails.json", "triage_results.json"]


class InstanceRunning(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existiert, gehört aber anderem User
    except OSError:
        return False
    return True


def _read_lock():
    try:
        return json.load(open(LOCK, encoding="utf-8"))
    except Exception:
        return None


def _check_instance(force: bool) -> dict:
    info = _read_lock()
    if info and isinstance(info.get("pid"), int) and _pid_alive(info["pid"]):
        if info["pid"] == os.getpid():
            return {"state": "self"}
        if force:
            return {"state": "forced", "old": info}
        raise InstanceRunning(
            f"Es läuft bereits eine Instanz (PID {info['pid']}, Rolle '{info.get('role')}', "
            f"gestartet {info.get('started')}). Beende sie oder starte mit --force.")
    if info:  # vorhanden, aber Prozess tot → stale
        return {"state": "stale", "removed": info}
    return {"state": "none"}


def _acquire_lock(role: str):
    payload = {"pid": os.getpid(), "role": role,
               "started": datetime.datetime.now().isoformat(timespec="seconds")}
    json.dump(payload, open(LOCK, "w", encoding="utf-8"))

    def _release():
        cur = _read_lock()
        if cur and cur.get("pid") == os.getpid():
            try:
                os.remove(LOCK)
            except OSError:
                pass
    atexit.register(_release)


def _quarantine(path: str) -> str:
    os.makedirs(QUARANTINE, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = os.path.join(QUARANTINE, f"{ts}_{os.path.basename(path)}")
    shutil.move(path, dest)
    return dest


def _check_integrity() -> list:
    """Verschiebt unlesbare/korrupte JSON-Status-Dateien in Quarantäne."""
    quarantined = []
    for name in STATE_FILES:
        p = os.path.join(BASE, name)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            continue
        try:
            json.load(open(p, encoding="utf-8"))
        except Exception:
            quarantined.append({"file": name, "moved_to": os.path.relpath(_quarantine(p), BASE)})
    return quarantined


def _leftover_transient(clean: bool) -> list:
    found = []
    for name in TRANSIENT:
        p = os.path.join(BASE, name)
        if os.path.exists(p):
            entry = {"file": name, "removed": False}
            if clean:
                try:
                    os.remove(p); entry["removed"] = True
                except OSError:
                    pass
            found.append(entry)
    return found


def preflight(role: str = "agent", force: bool = False, clean_transient: bool = False) -> dict:
    inst = _check_instance(force)          # 1) Instanz-Sperre (kann InstanceRunning werfen)
    _acquire_lock(role)                    #    Lock setzen (atexit-Aufräumen)
    quarantined = _check_integrity()       # 2a) korruptes JSON → Quarantäne
    leftovers = _leftover_transient(clean_transient)  # 2b) Zwischendateien
    return {"instance": inst, "quarantined": quarantined, "leftovers": leftovers}


def run_and_report(role: str, force: bool = False, clean_transient: bool = False) -> bool:
    """Komfort-Wrapper für Entry-Points: prüft, druckt, signalisiert ok/abbruch."""
    try:
        rep = preflight(role, force=force, clean_transient=clean_transient)
    except InstanceRunning as e:
        print(f"⛔ {e}", file=sys.stderr)
        return False
    st = rep["instance"]["state"]
    if st == "stale":
        print("ℹ︎ Verwaister Lock einer abgestürzten Instanz entfernt.", file=sys.stderr)
    if st == "forced":
        print("⚠︎ --force: bestehende Instanz-Sperre überschrieben.", file=sys.stderr)
    for q in rep["quarantined"]:
        print(f"⚠︎ Korrupte Datei '{q['file']}' → Quarantäne ({q['moved_to']}).", file=sys.stderr)
    for lo in rep["leftovers"]:
        msg = "entfernt" if lo["removed"] else "vorhanden (mit --clean entfernen)"
        print(f"ℹ︎ Zwischendatei '{lo['file']}' {msg}.", file=sys.stderr)
    return True


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Preflight-Check manuell ausführen.")
    ap.add_argument("--role", default="manual")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    ok = run_and_report(a.role, force=a.force, clean_transient=a.clean)
    print("✅ Preflight ok." if ok else "Abbruch.", file=sys.stderr)
    sys.exit(0 if ok else 1)
