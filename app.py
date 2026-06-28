from flask import Flask, render_template_string, request, jsonify, Response
import threading, requests, msal, os, csv, io, re, subprocess, signal, time, json, sys, uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===============================================================================
# Mail CRM Agent – generiert nach MASTER PROMPT V6 (Zero-Loss, Graceful Stop,
# Fortsetzungs-Mechanismus, persistente Kontakte/Logs, dynamische Typen)
# ===============================================================================

app = Flask(__name__)
PORT = 5050
VERSION = "v1.3"
BUILD = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(__file__)))

ACCOUNTS = [
    "rb@robert-beckert.de",
    "gitarre@robert-beckert.de",
    "tenor@robert-beckert.de",
]

def cache_file_for(email):
    return "token_cache_" + email.replace("@", "_").replace(".", "_") + ".bin"

CONTACTS_FILE = "contacts_data.json"
LOGS_FILE = "logs_data.json"
PROGRESS_FILE = "progress_state.json"
EXCLUDED_FILE  = "excluded_emails.json"
PIPELINE_FILE  = "pipeline_data.json"
LEHRPLAN_FILE  = "lehrplan_data.json"

PIPELINE_COLUMNS = [
    {"id": "neu",         "title": "🆕 Neu",           "color": "#3b82f6"},
    {"id": "kontaktiert", "title": "📨 Kontaktiert",    "color": "#f59e0b"},
    {"id": "angebot",     "title": "📋 Angebot",        "color": "#8b5cf6"},
    {"id": "gebucht",     "title": "✅ Gebucht",        "color": "#22c55e"},
    {"id": "nein",        "title": "❌ Kein Interesse", "color": "#94a3b8"},
]

LEHRPLAN_COLUMNS = [
    {"id": "ideen",         "title": "💡 Ideen",         "color": "#94a3b8"},
    {"id": "geplant",       "title": "📅 Geplant",        "color": "#3b82f6"},
    {"id": "vorbereitung",  "title": "🔧 Vorbereitung",   "color": "#f59e0b"},
    {"id": "durchgefuehrt", "title": "✅ Durchgeführt",   "color": "#22c55e"},
    {"id": "archiv",        "title": "📦 Archiv",         "color": "#64748b"},
]

board_state = {"pipeline": [], "lehrplan": []}

excluded_emails = set()


def load_boards():
    for key, fname in [("pipeline", PIPELINE_FILE), ("lehrplan", LEHRPLAN_FILE)]:
        if os.path.exists(fname):
            try:
                with open(fname, "r", encoding="utf-8") as f:
                    board_state[key] = json.load(f)
            except Exception:
                board_state[key] = []


def save_board(board_name):
    fname = PIPELINE_FILE if board_name == "pipeline" else LEHRPLAN_FILE
    _atomic_write_json(fname, board_state[board_name])


def load_excluded():
    global excluded_emails
    if os.path.exists(EXCLUDED_FILE):
        try:
            with open(EXCLUDED_FILE, "r", encoding="utf-8") as f:
                excluded_emails = set(json.load(f))
        except Exception:
            excluded_emails = set()


def save_excluded():
    _atomic_write_json(EXCLUDED_FILE, list(excluded_emails))

CLIENT_ID = "3ae26d06-295e-4c85-a368-d56d97c373e7"
TENANT_ID = "0e4fb204-9f60-4bfe-b441-83bd03ad0e6b"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/Mail.Read.Shared"]

# ✅ KEYWORDS / LABELS / EXCLUDE-PATTERNS (Teil B.11/12)
KEYWORDS = ["vhs", "Ukulele", "Gitarre", "Kurs"]
KEYWORD_LABELS = {"vhs": "VHS", "Ukulele": "Ukulele", "Gitarre": "Gitarre", "Kurs": "Kurs"}
EXCLUDE_PATTERNS = [
    "newsletter", "no-reply", "noreply", "ticket", "versand", "tips", "news", "redaktion", "@vhs",
    "mailer-daemon", "postmaster", "donotreply",
    "automated", "automatisch",
    "bestaetigung", "notification", "benachrichtigung",
    "kalender", "calendar", "bounce",
    "unsubscribe", "abmelden",
    "marketing", "promo",
    "info@"
]

# ✅ MAX_MAILS bezieht sich auf relevant_count (Teil B.15)
MAX_MAILS = 1000

# ✅ STATE (Teil A.5)
state = {
    "running": False,
    "paused": False,
    "stop_requested": False,
    "ever_started": False,
    "awaiting_login": False,
    "current_account": "",
    "page": 0,
    "mail_count": 0,
    "relevant_count": 0,
    "excluded_count": 0,
    "contacts": [],
    "logs": [],
    "next_link": None,
    "last_received_datetime": None
}

settings = {
    "start_date": "",
    "end_date": "",
    "selected_accounts": []
}

agent_lock = threading.Lock()


# ===============================================================================
# PERSISTENZ-HELFER (Teil C.17, Teil D.17b, Teil E.23)
# ===============================================================================

def _atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_contacts():
    _atomic_write_json(CONTACTS_FILE, state["contacts"])


def export_csv_snapshot():
    filename = f"kontakte_{settings['start_date']}_{settings['end_date']}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Email", "Name", "Typ", "Telefon", "Treffer", "Kontext"])
        for c in sorted(state["contacts"], key=lambda c: c.get("hits", 0), reverse=True):
            writer.writerow([c["email"], c.get("name", ""), c["type"], c.get("phone", ""), c.get("hits", 0), c.get("context", "")])
    add_log(f"💾 CSV gespeichert: {filename}")


def save_logs():
    _atomic_write_json(LOGS_FILE, state["logs"])


def add_log(message):
    state["logs"].append(message)
    save_logs()


def save_progress(next_link, last_received_datetime, incomplete=True):
    data = {
        "incomplete": incomplete,
        "next_link": next_link,
        "page": state["page"],
        "mail_count": state["mail_count"],
        "relevant_count": state["relevant_count"],
        "excluded_count": state["excluded_count"],
        "last_received_datetime": last_received_datetime,
        "start_date": settings["start_date"],
        "end_date": settings["end_date"]
    }
    _atomic_write_json(PROGRESS_FILE, data)


def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return None
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def delete_progress():
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


def log_build_start():
    history_file = "build_history.json"
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass
    history.append({
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "build": BUILD
    })
    _atomic_write_json(history_file, history)


def load_initial_data():
    # Alten Token-Cache migrieren (token_cache.bin → neues Format)
    old_cache = "token_cache.bin"
    new_cache = cache_file_for("rb@robert-beckert.de")
    if os.path.exists(old_cache) and not os.path.exists(new_cache):
        os.rename(old_cache, new_cache)

    load_excluded()
    load_boards()

    if os.path.exists(CONTACTS_FILE):
        try:
            with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
                state["contacts"] = json.load(f)
        except Exception:
            state["contacts"] = []

    if os.path.exists(LOGS_FILE):
        try:
            with open(LOGS_FILE, "r", encoding="utf-8") as f:
                state["logs"] = json.load(f)
        except Exception:
            state["logs"] = []


# ===============================================================================
# SYSTEM-HELFER (Teil A.8)
# ===============================================================================

def free_port(port):
    try:
        result = subprocess.check_output(f"lsof -t -i:{port}", shell=True)
        for pid in result.decode().split():
            os.kill(int(pid), signal.SIGKILL)
    except Exception:
        pass


# ===============================================================================
# TOKEN-HANDLING (Teil A.7)
# ===============================================================================

def get_token():
    # Einmaliger Login als rb@ — Token gilt für alle geteilten Postfächer
    cache_path = cache_file_for("rb@robert-beckert.de")
    cache = msal.SerializableTokenCache()
    if os.path.exists(cache_path):
        cache.deserialize(open(cache_path).read())

    app_msal = msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )

    accounts = app_msal.get_accounts()
    result = None

    if accounts:
        result = app_msal.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app_msal.initiate_device_flow(scopes=SCOPES)
        add_log("🔐 Login: rb@robert-beckert.de")
        add_log(flow["verification_uri"])
        add_log("Code: " + flow["user_code"])
        state["awaiting_login"] = True
        result = app_msal.acquire_token_by_device_flow(flow)
        state["awaiting_login"] = False

        if cache.has_state_changed:
            open(cache_path, "w").write(cache.serialize())

    if not result:
        return None

    return result.get("access_token")


# ===============================================================================
# KEYWORD- / FILTER- / TELEFON-LOGIK (Teil B.11/12/13/14/16)
# ===============================================================================

def get_matched_keywords(text):
    if not text:
        return set()
    text_lower = text.lower()
    return {kw for kw in KEYWORDS if kw.lower() in text_lower}


def is_relevant(text):
    return bool(get_matched_keywords(text))


def is_valid_email(email):
    if not email:
        return False
    email_lower = email.lower()
    if any(pattern.lower() in email_lower for pattern in EXCLUDE_PATTERNS):
        return False
    if email_lower in {e.lower() for e in excluded_emails}:
        return False
    return True


def extract_context(body, matched_keywords):
    """Extrahiert relevante Sätze aus dem Mail-Body als Kontext-Snippet."""
    if not body:
        return ""
    # HTML-Tags entfernen
    text = re.sub(r'<[^>]+>', ' ', body)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = re.split(r'(?<=[.!?\n])\s+', text)
    relevant = []
    for s in sentences:
        s = s.strip()
        if len(s) < 15:
            continue
        if any(kw.lower() in s.lower() for kw in matched_keywords):
            relevant.append(s)
        if len(relevant) >= 3:
            break
    result = ' … '.join(relevant) if relevant else text[:200]
    return result[:450]


# ✅ Telefonnummern-Regex – international tolerant (Teil B.16)
PHONE_REGEX = re.compile(r"\+?\(?\d{1,4}\)?(?:[\s\-./]?\(?\d{1,5}\)?){1,6}")


def extract_phone(text):
    if not text:
        return ""
    for match in PHONE_REGEX.finditer(text):
        candidate = match.group(0).strip()
        digits = re.sub(r"\D", "", candidate)
        if len(digits) >= 6:
            return candidate
    return ""


def compute_type(matched_keywords):
    labels = [KEYWORD_LABELS[kw] for kw in KEYWORDS if kw in matched_keywords]
    if not labels:
        return "Interessent"
    return "/".join(labels) + "-Interessent"


def upsert_contact(email, matched_keywords, phone, name="", context=""):
    for contact in state["contacts"]:
        if contact["email"] == email:
            contact["hits"] = contact.get("hits", 0) + 1

            existing_matched = set(contact.get("matched_keywords", []))
            new_matched = existing_matched | matched_keywords
            if new_matched != existing_matched:
                contact["matched_keywords"] = list(new_matched)
                contact["type"] = compute_type(new_matched)

            if phone:
                existing_phone = contact.get("phone", "") or ""
                existing_numbers = [p.strip() for p in existing_phone.split(",") if p.strip()]
                if phone not in existing_numbers:
                    existing_numbers.append(phone)
                    contact["phone"] = ", ".join(existing_numbers)

            # Name nur setzen wenn noch nicht gesetzt oder neuer Name länger
            if name and len(name) > len(contact.get("name", "")):
                contact["name"] = name

            # Kontext nur beim ersten Treffer speichern
            if context and not contact.get("context"):
                contact["context"] = context

            save_contacts()
            return

    new_contact = {
        "email": email,
        "name": name,
        "type": compute_type(matched_keywords),
        "phone": phone if phone else "",
        "hits": 1,
        "matched_keywords": list(matched_keywords),
        "context": context
    }
    state["contacts"].append(new_contact)
    save_contacts()


# ===============================================================================
# GRAPH-API URL-BUILDER (Teil A.6, Teil D.20)
# ===============================================================================

def build_initial_url(start_date, end_date, account_email):
    return (
        f"https://graph.microsoft.com/v1.0/users/{account_email}/mailFolders/inbox/messages"
        f"?$top=50&$select=subject,from,body,receivedDateTime"
        f"&$filter=receivedDateTime ge {start_date}T00:00:00Z and receivedDateTime le {end_date}T23:59:59Z"
    )


def build_fallback_url(last_received_datetime, end_date, account_email):
    return (
        f"https://graph.microsoft.com/v1.0/users/{account_email}/mailFolders/inbox/messages"
        f"?$top=50&$select=subject,from,body,receivedDateTime"
        f"&$filter=receivedDateTime gt {last_received_datetime} and receivedDateTime le {end_date}T23:59:59Z"
    )


# ===============================================================================
# AGENT – POSTFACH-SCAN (ein Konto)
# ===============================================================================

def _scan_inbox(token, account_email, resume_from_progress=False, resume_url=None, fallback_last_received=None):
    """Scannt das Postfach von account_email. Gibt True zurück bei regulärem Ende, False bei Stop."""
    headers = {"Authorization": f"Bearer {token}"}
    last_received_datetime = fallback_last_received if resume_from_progress else None

    if resume_from_progress and resume_url:
        url = resume_url
        first_request_is_resume = True
    elif resume_from_progress:
        url = build_fallback_url(last_received_datetime, settings["end_date"], account_email)
        add_log(f"⚠️ Fortsetzung über gespeicherten Link fehlgeschlagen – Fallback über Datum {last_received_datetime}")
        first_request_is_resume = False
    else:
        url = build_initial_url(settings["start_date"], settings["end_date"], account_email)
        first_request_is_resume = False

    state["next_link"] = url
    state["last_received_datetime"] = last_received_datetime

    while url:
        if state["stop_requested"]:
            return False

        if state["paused"]:
            time.sleep(1)
            continue

        try:
            r = requests.get(url, headers=headers, timeout=30)
        except Exception as e:
            add_log(f"❌ API Fehler: {e}")
            break

        if r.status_code != 200:
            if first_request_is_resume:
                add_log(f"⚠️ Fortsetzung über gespeicherten Link fehlgeschlagen – Fallback über Datum {last_received_datetime}")
                url = build_fallback_url(last_received_datetime, settings["end_date"], account_email)
                state["next_link"] = url
                first_request_is_resume = False
                continue
            else:
                add_log(f"❌ API Fehler: {r.status_code}")
                break

        first_request_is_resume = False
        data = r.json()
        messages = data.get("value", [])
        state["page"] += 1

        for msg in messages:
            state["mail_count"] += 1
            subject = msg.get("subject", "") or ""
            body = (msg.get("body", {}) or {}).get("content", "") or ""
            received = msg.get("receivedDateTime")
            if received:
                last_received_datetime = received
                state["last_received_datetime"] = last_received_datetime
            matched = get_matched_keywords(subject) | get_matched_keywords(body)
            if matched:
                state["relevant_count"] += 1
                from_field = (msg.get("from", {}) or {}).get("emailAddress", {}) or {}
                sender = from_field.get("address", "")
                sender_name = from_field.get("name", "")
                if is_valid_email(sender):
                    phone = extract_phone(body)
                    context = extract_context(body, matched)
                    upsert_contact(sender, matched, phone, name=sender_name, context=context)
                else:
                    state["excluded_count"] += 1
                    reason = "gespeichert" if sender.lower() in {e.lower() for e in excluded_emails} else "Muster"
                    add_log(f"⛔ Ausgeschlossen ({reason}): {sender}")

        next_link = data.get("@odata.nextLink")
        state["next_link"] = next_link
        add_log(f"📡 Seite {state['page']} ({len(messages)} Mails)")
        save_progress(next_link, state["last_received_datetime"], incomplete=True)

        if state["relevant_count"] >= MAX_MAILS:
            add_log(f"⏹ Limit von {MAX_MAILS} relevanten Treffern erreicht – Verarbeitung gestoppt (fortsetzbar)")
            return False

        url = next_link

    return True


# ===============================================================================
# AGENT – HAUPTSCHLEIFE (Teil A/B/C/D)
# ===============================================================================

def run_agent(resume_from_progress=False, resume_url=None, fallback_last_received=None):
    if state["running"]:
        return

    if not settings["start_date"] or not settings["end_date"]:
        return

    if not resume_from_progress:
        state["page"] = 0
        state["mail_count"] = 0
        state["relevant_count"] = 0
        state["excluded_count"] = 0
        state["contacts"].clear()
        save_contacts()
        state["logs"].clear()
        save_logs()

    state["running"] = True
    state["paused"] = False
    state["stop_requested"] = False

    token = get_token()
    if not token:
        state["running"] = False
        return

    accounts_to_scan = [a for a in (settings.get("selected_accounts") or ACCOUNTS) if a]

    first = True
    for account_email in accounts_to_scan:
        if state["stop_requested"]:
            break

        state["current_account"] = account_email
        add_log(f"📬 Postfach: {account_email}")

        use_resume = resume_from_progress and first
        completed = _scan_inbox(
            token,
            account_email,
            resume_from_progress=use_resume,
            resume_url=resume_url if use_resume else None,
            fallback_last_received=fallback_last_received if use_resume else None
        )
        first = False

        if not completed:
            break

    if state["stop_requested"]:
        save_progress(state.get("next_link"), state.get("last_received_datetime"), incomplete=True)
        state["running"] = False
        state["current_account"] = ""
        return

    delete_progress()
    state["running"] = False
    state["current_account"] = ""
    add_log("✅ Fertig")
    add_log(
        f"📊 Zusammenfassung: Zeitraum {settings['start_date']} – {settings['end_date']} | "
        f"Seiten: {state['page']} | Geprüfte Emails: {state['mail_count']} | "
        f"Relevante Treffer: {state['relevant_count']} | "
        f"Eindeutige Kontakte: {len(state['contacts'])} | "
        f"Ausgeschlossen: {state['excluded_count']}"
    )
    export_csv_snapshot()


def start_agent_thread(**kwargs):
    threading.Thread(target=run_agent, kwargs=kwargs, daemon=True).start()


# ===============================================================================
# ROUTES
# ===============================================================================

@app.route("/")
def home():
    return render_template_string(HTML, version=VERSION, build=BUILD)


@app.route("/start")
def start():
    if not settings["start_date"] or not settings["end_date"]:
        return "error"
    if state["running"]:
        return "error"
    delete_progress()
    state["ever_started"] = True
    start_agent_thread(resume_from_progress=False)
    return "ok"


@app.route("/pause")
def pause():
    state["paused"] = True
    return "ok"


@app.route("/resume")
def resume():
    state["paused"] = False
    return "ok"


@app.route("/close")
def close_application():
    state["stop_requested"] = True
    save_contacts()
    save_logs()
    if state["running"]:
        save_progress(state.get("next_link"), state.get("last_received_datetime"), incomplete=True)
    response = jsonify({"status": "closed"})
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return response


@app.route("/settings", methods=["POST"])
def set_settings():
    settings["start_date"] = request.form["start_date"]
    settings["end_date"] = request.form["end_date"]
    raw = request.form.get("accounts", "")
    settings["selected_accounts"] = [a for a in raw.split(",") if a]
    return "ok"


@app.route("/status")
def status():
    return jsonify({
        "running": state["running"],
        "paused": state["paused"],
        "page": state["page"],
        "mails": state["mail_count"],
        "contacts": len(state["contacts"]),
        "relevant_count": state["relevant_count"],
        "max_mails": MAX_MAILS,
        "ever_started": state["ever_started"],
        "awaiting_login": state["awaiting_login"],
        "current_account": state["current_account"]
    })


@app.route("/contacts")
def contacts():
    sorted_contacts = sorted(state["contacts"], key=lambda c: c.get("hits", 0), reverse=True)
    return jsonify(sorted_contacts)


@app.route("/logs")
def logs():
    return "\n".join(state["logs"])


@app.route("/download")
def download():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Email", "Name", "Typ", "Telefon", "Treffer", "Kontext"])
    sorted_contacts = sorted(state["contacts"], key=lambda c: c.get("hits", 0), reverse=True)
    for c in sorted_contacts:
        writer.writerow([c["email"], c.get("name", ""), c["type"], c.get("phone", ""), c.get("hits", 0), c.get("context", "")])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=kontakte.csv"}
    )


@app.route("/progress_status")
def progress_status():
    progress = load_progress()
    if progress and progress.get("incomplete"):
        return jsonify({
            "has_progress": True,
            "page": progress.get("page", 0),
            "mail_count": progress.get("mail_count", 0),
            "relevant_count": progress.get("relevant_count", 0),
            "start_date": progress.get("start_date", ""),
            "end_date": progress.get("end_date", "")
        })
    return jsonify({"has_progress": False})


@app.route("/continue_run")
def continue_run():
    progress = load_progress()
    if not progress or not progress.get("incomplete"):
        return jsonify({"status": "error", "message": "Kein Fortsetzungspunkt vorhanden"})
    if state["running"]:
        return jsonify({"status": "error", "message": "Agent läuft bereits"})

    settings["start_date"] = progress.get("start_date", "")
    settings["end_date"] = progress.get("end_date", "")
    state["page"] = progress.get("page", 0)
    state["mail_count"] = progress.get("mail_count", 0)
    state["relevant_count"] = progress.get("relevant_count", 0)
    state["excluded_count"] = progress.get("excluded_count", 0)
    state["stop_requested"] = False
    state["paused"] = False

    resume_url = progress.get("next_link")
    last_received = progress.get("last_received_datetime")

    state["ever_started"] = True
    start_agent_thread(
        resume_from_progress=True,
        resume_url=resume_url,
        fallback_last_received=last_received
    )
    return jsonify({"status": "resuming"})


@app.route("/discard_progress")
def discard_progress():
    delete_progress()
    return jsonify({"status": "discarded"})


@app.route("/check_update")
def check_update():
    try:
        subprocess.run(["git", "fetch", "origin"], cwd=BASE_DIR, capture_output=True, timeout=10)
        log = subprocess.run(
            ["git", "log", "HEAD..origin/main", "--oneline"],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=5
        )
        commits = [l.strip() for l in log.stdout.strip().splitlines() if l.strip()]
        return jsonify({"has_update": bool(commits), "commits": commits})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/do_update")
def do_update():
    def _run():
        add_log("🔄 Update gestartet …")
        try:
            r1 = subprocess.run(["git", "pull"], cwd=BASE_DIR, capture_output=True, text=True, timeout=30)
            add_log(f"📥 git pull: {r1.stdout.strip() or r1.stderr.strip()}")
            pip = os.path.join(BASE_DIR, "venv", "bin", "pip")
            r2 = subprocess.run([pip, "install", "-r", "requirements.txt", "-q"],
                                cwd=BASE_DIR, capture_output=True, text=True, timeout=60)
            add_log("📦 pip install: fertig")
            save_contacts()
            save_logs()
            add_log("♻️ Neustart …")
            time.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            add_log(f"❌ Update-Fehler: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "updating"})


@app.route("/set_exclusions", methods=["POST"])
def set_exclusions():
    """Bulk-Validierung: approved = gewollte E-Mails. Alle anderen werden dauerhaft ausgeschlossen."""
    data = request.get_json() or {}
    approved_set = {e.lower() for e in data.get("approved", [])}
    all_emails = {c["email"].lower() for c in state["contacts"]}
    newly_excluded = all_emails - approved_set
    excluded_emails.update(newly_excluded)
    save_excluded()
    # Nicht gewollte Kontakte aus der aktuellen Liste entfernen
    before = len(state["contacts"])
    state["contacts"] = [c for c in state["contacts"] if c["email"].lower() not in newly_excluded]
    save_contacts()
    add_log(f"✅ Validierung: {len(approved_set)} behalten, {len(newly_excluded)} dauerhaft ausgeschlossen")
    return jsonify({"status": "ok", "approved": len(approved_set), "excluded": len(newly_excluded), "removed": before - len(state["contacts"])})


@app.route("/validate_contact", methods=["POST"])
def validate_contact():
    """Einzelnen Kontakt als gewollt oder ungewollt markieren."""
    data = request.get_json() or {}
    email = data.get("email", "")
    approved = data.get("approved", True)
    if not email:
        return jsonify({"status": "error", "message": "E-Mail fehlt"})
    if not approved:
        excluded_emails.add(email.lower())
        save_excluded()
        state["contacts"] = [c for c in state["contacts"] if c["email"].lower() != email.lower()]
        save_contacts()
        add_log(f"⛔ Dauerhaft ausgeschlossen (manuell): {email}")
    else:
        add_log(f"✅ Bestätigt (manuell): {email}")
    return jsonify({"status": "ok"})


@app.route("/board/<board_name>")
def get_board(board_name):
    if board_name not in board_state:
        return jsonify({"error": "Unknown board"}), 404
    cols = PIPELINE_COLUMNS if board_name == "pipeline" else LEHRPLAN_COLUMNS
    return jsonify({"columns": cols, "cards": board_state[board_name]})


@app.route("/board/<board_name>/add", methods=["POST"])
def add_card(board_name):
    if board_name not in board_state:
        return jsonify({"error": "Unknown board"}), 404
    data = request.get_json() or {}
    col = data.get("column", "")
    pos = len([c for c in board_state[board_name] if c.get("column") == col])
    card = {
        "id": uuid.uuid4().hex[:8],
        "column": col,
        "position": pos,
        "priority": data.get("priority", "mittel"),
        "notes": data.get("notes", ""),
    }
    if board_name == "pipeline":
        card.update({"email": data.get("email", ""), "name": data.get("name", ""), "type": data.get("type", "")})
    else:
        card.update({"title": data.get("title", ""), "thema": data.get("thema", ""), "datum": data.get("datum", "")})
    board_state[board_name].append(card)
    save_board(board_name)
    return jsonify(card)


@app.route("/board/<board_name>/move", methods=["POST"])
def move_card(board_name):
    data = request.get_json() or {}
    card_id, new_col, new_pos = data.get("id"), data.get("column"), data.get("position", 9999)
    cards = board_state[board_name]
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return jsonify({"error": "not found"}), 404
    card["column"] = new_col
    col_cards = sorted([c for c in cards if c["column"] == new_col and c["id"] != card_id], key=lambda c: c.get("position", 0))
    col_cards.insert(min(int(new_pos), len(col_cards)), card)
    for i, c in enumerate(col_cards):
        c["position"] = i
    save_board(board_name)
    return jsonify({"status": "ok"})


@app.route("/board/<board_name>/card/<card_id>", methods=["PUT"])
def update_card(board_name, card_id):
    data = request.get_json() or {}
    card = next((c for c in board_state[board_name] if c["id"] == card_id), None)
    if not card:
        return jsonify({"error": "not found"}), 404
    for k, v in data.items():
        if k != "id":
            card[k] = v
    save_board(board_name)
    return jsonify(card)


@app.route("/board/<board_name>/card/<card_id>", methods=["DELETE"])
def delete_card(board_name, card_id):
    board_state[board_name] = [c for c in board_state[board_name] if c["id"] != card_id]
    save_board(board_name)
    return jsonify({"status": "ok"})


@app.route("/board/pipeline/import_contacts", methods=["POST"])
def import_contacts_to_pipeline():
    data = request.get_json() or {}
    emails = set(data.get("emails", []))
    existing = {c["email"] for c in board_state["pipeline"]}
    added = 0
    for contact in state["contacts"]:
        if contact["email"] in emails and contact["email"] not in existing:
            pos = len([c for c in board_state["pipeline"] if c.get("column") == "neu"])
            board_state["pipeline"].append({
                "id": uuid.uuid4().hex[:8],
                "email": contact["email"],
                "name": contact.get("name", ""),
                "type": contact.get("type", ""),
                "column": "neu",
                "position": pos,
                "priority": "mittel",
                "notes": "",
            })
            added += 1
    save_board("pipeline")
    add_log(f"📋 {added} Kontakt(e) in Lead-Pipeline importiert")
    return jsonify({"status": "ok", "added": added})


@app.route("/ai_review/<path:email>")
def ai_review(email):
    """Claude-KI analysiert den gespeicherten Kontext eines Kontakts."""
    contact = next((c for c in state["contacts"] if c["email"] == email), None)
    if not contact:
        return jsonify({"result": "Kontakt nicht gefunden"})
    context = contact.get("context", "")
    if not context:
        return jsonify({"result": "Kein Kontext gespeichert (nächster Scan füllt dieses Feld)"})
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"result": "⚠️ ANTHROPIC_API_KEY nicht gesetzt – bitte in start_app.command eintragen"})
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": (
                    "Analysiere diesen Mail-Ausschnitt in 1–2 Sätzen auf Deutsch: "
                    "Ist das ein echter Interessent für Musikunterricht (Gitarre, Ukulele, VHS-Kurs)? "
                    "Falls ja, was ist das Anliegen?\n\nAusschnitt: " + context[:600]
                )
            }]
        )
        result = msg.content[0].text
        add_log(f"🤖 KI [{email[:28]}…]: {result[:70]}…")
        return jsonify({"result": result})
    except Exception as e:
        err = str(e)[:120]
        return jsonify({"result": f"Fehler: {err}"})


# ===============================================================================
# UI (HTML + JS)
# ===============================================================================

HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CRM Cockpit</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:          #f1f5f9;
    --header-bg:   #0f172a;
    --sidebar-bg:  #1e293b;
    --card-bg:     #ffffff;
    --text:        #1e293b;
    --text-muted:  #64748b;
    --text-inv:    #f8fafc;
    --accent:      #3b82f6;
    --accent-h:    #2563eb;
    --green:       #22c55e;
    --green-bg:    #f0fdf4;
    --red:         #ef4444;
    --amber:       #f59e0b;
    --border:      #e2e8f0;
    --shadow:      0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.05);
    --shadow-md:   0 4px 6px -1px rgba(0,0,0,.08), 0 2px 4px -1px rgba(0,0,0,.05);
    --r:           10px;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* ── HEADER ── */
  header {
    background: var(--header-bg);
    color: var(--text-inv);
    padding: 0 28px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
    box-shadow: 0 2px 8px rgba(0,0,0,.3);
  }
  header .brand { display: flex; align-items: center; gap: 10px; }
  header h1 { font-size: 17px; font-weight: 600; letter-spacing: -.2px; }
  header .meta { font-size: 11px; color: #94a3b8; }

  /* ── LAYOUT ── */
  .layout {
    display: grid;
    grid-template-columns: 240px 1fr;
    grid-template-rows: 1fr auto;
    gap: 20px;
    padding: 20px;
    flex: 1;
    min-height: 0;
  }

  /* ── SIDEBAR ── */
  .sidebar {
    grid-row: 1 / 3;
    background: var(--sidebar-bg);
    border-radius: var(--r);
    padding: 20px 16px;
    display: flex;
    flex-direction: column;
    gap: 20px;
    color: var(--text-inv);
    box-shadow: var(--shadow-md);
  }
  .sidebar-section h3 {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #64748b;
    margin-bottom: 10px;
  }
  .sidebar label {
    display: block;
    font-size: 11px;
    color: #94a3b8;
    margin-bottom: 4px;
  }
  .sidebar input[type="date"] {
    width: 100%;
    padding: 7px 10px;
    border-radius: 6px;
    border: 1px solid #334155;
    background: #0f172a;
    color: var(--text-inv);
    font-size: 13px;
    margin-bottom: 10px;
    outline: none;
  }
  .sidebar input[type="date"]:focus { border-color: var(--accent); }
  .sidebar hr { border: none; border-top: 1px solid #334155; }

  .checkbox-label {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    font-size: 12px;
    color: #cbd5e1;
    cursor: pointer;
  }
  .checkbox-label input { accent-color: var(--accent); width: 14px; height: 14px; }

  /* ── BUTTONS ── */
  .btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    width: 100%;
    padding: 8px 12px;
    border-radius: 7px;
    border: none;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: background .15s, transform .1s;
    margin-bottom: 6px;
  }
  .btn:active { transform: scale(.97); }
  .btn-primary  { background: var(--accent);  color: #fff; }
  .btn-primary:hover  { background: var(--accent-h); }
  .btn-ghost   { background: #1e293b; color: #94a3b8; border: 1px solid #334155; }
  .btn-ghost:hover   { background: #334155; color: #cbd5e1; }
  .btn-danger  { background: #7f1d1d; color: #fca5a5; }
  .btn-danger:hover  { background: #991b1b; }
  .btn-save    { background: #065f46; color: #a7f3d0; margin-top: 4px; }
  .btn-save:hover    { background: #047857; }
  .btn-csv     { background: #1e3a5f; color: #93c5fd; border: 1px solid #1d4ed8; }
  .btn-csv:hover     { background: #1d3461; }

  /* ── MAIN AREA ── */
  .main { display: flex; flex-direction: column; gap: 16px; min-width: 0; }

  /* ── CARDS ── */
  .card {
    background: var(--card-bg);
    border-radius: var(--r);
    box-shadow: var(--shadow);
    border: 1px solid var(--border);
    overflow: hidden;
  }
  .card-header {
    padding: 14px 18px 10px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .card-header h2 { font-size: 13px; font-weight: 600; color: var(--text); }
  .card-body { padding: 16px 18px; }

  /* ── STATUS CARD ── */
  .status-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 14px;
  }
  .stat-box {
    background: var(--bg);
    border-radius: 8px;
    padding: 10px 14px;
    border: 1px solid var(--border);
  }
  .stat-box .val { font-size: 22px; font-weight: 700; color: var(--text); }
  .stat-box .lbl { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  .status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge-running { background: var(--green-bg); color: #15803d; }
  .badge-stopped { background: #f8fafc; color: var(--text-muted); border: 1px solid var(--border); }
  .badge-login   { background: #fffbeb; color: #92400e; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .badge-running .dot { animation: pulse 1.4s infinite; }

  .progress-bar-wrap {
    background: var(--bg);
    border-radius: 99px;
    height: 8px;
    overflow: hidden;
    flex: 1;
  }
  .progress-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--green));
    border-radius: 99px;
    width: 0%;
    transition: width .5s ease;
  }
  .progress-row {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12px;
    color: var(--text-muted);
  }
  .progress-val { font-weight: 600; color: var(--text); min-width: 60px; text-align: right; }
  .account-tag {
    background: #eff6ff;
    color: var(--accent);
    border: 1px solid #bfdbfe;
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
  }

  /* ── CONTACTS TABLE ── */
  .contacts-count {
    background: var(--accent);
    color: #fff;
    border-radius: 99px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 600;
  }
  .contacts-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead th {
    text-align: left;
    padding: 8px 12px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .05em;
    text-transform: uppercase;
    color: var(--text-muted);
    background: var(--bg);
    border-bottom: 1px solid var(--border);
  }
  tbody tr { border-bottom: 1px solid var(--border); transition: background .1s; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: #f8fafc; }
  tbody td { padding: 9px 12px; color: var(--text); }
  .hits-badge {
    display: inline-block;
    background: var(--accent);
    color: #fff;
    border-radius: 99px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 700;
    min-width: 28px;
    text-align: center;
  }
  .type-tag {
    background: #f0f9ff;
    color: #0369a1;
    border: 1px solid #bae6fd;
    border-radius: 5px;
    padding: 1px 7px;
    font-size: 11px;
    white-space: nowrap;
  }
  .empty-state {
    text-align: center;
    padding: 32px;
    color: var(--text-muted);
    font-size: 13px;
  }

  /* ── UPDATE WIDGET ── */
  .update-wrap { display: flex; align-items: center; gap: 10px; }
  .btn-update {
    padding: 5px 12px;
    border-radius: 6px;
    border: 1px solid #334155;
    background: #1e293b;
    color: #94a3b8;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: background .15s, color .15s;
    white-space: nowrap;
  }
  .btn-update:hover { background: #334155; color: #e2e8f0; }
  .btn-update:disabled { opacity: .5; cursor: default; }
  .update-status { font-size: 12px; white-space: nowrap; }
  .update-status.ok     { color: #4ade80; }
  .update-status.avail  { color: #fbbf24; }
  .update-status.err    { color: #f87171; }
  .btn-get-update {
    padding: 5px 12px;
    border-radius: 6px;
    border: none;
    background: #f59e0b;
    color: #1c1917;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    animation: pulse 1.6s infinite;
  }
  .btn-get-update:hover { background: #d97706; }

  /* ── BULK ACTION BAR ── */
  .bulk-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 18px;
    background: #eff6ff;
    border-bottom: 1px solid #bfdbfe;
    flex-wrap: wrap;
  }
  .bulk-bar .lbl { font-size: 12px; color: var(--text-muted); margin-right: 4px; }
  .btn-sm {
    padding: 4px 10px;
    border-radius: 5px;
    border: 1px solid var(--border);
    background: #fff;
    font-size: 12px;
    cursor: pointer;
    font-weight: 500;
    transition: background .12s;
  }
  .btn-sm:hover { background: var(--bg); }
  .btn-sm-primary {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent-h);
  }
  .btn-sm-primary:hover { background: var(--accent-h); }
  .btn-sm-danger {
    background: #fee2e2;
    color: #991b1b;
    border-color: #fca5a5;
  }
  .excluded-count {
    margin-left: auto;
    font-size: 11px;
    color: var(--text-muted);
    background: #f1f5f9;
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 2px 8px;
  }

  /* ── CONTACT ROW ACTIONS ── */
  .row-actions { display: flex; gap: 4px; }
  .btn-approve { background: #dcfce7; color: #15803d; border: 1px solid #86efac; border-radius: 5px; padding: 3px 7px; font-size: 12px; cursor: pointer; }
  .btn-approve:hover { background: #bbf7d0; }
  .btn-reject  { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; border-radius: 5px; padding: 3px 7px; font-size: 12px; cursor: pointer; }
  .btn-reject:hover  { background: #fecaca; }

  /* ── KONTEXT / KI SPALTE ── */
  .context-cell { max-width: 260px; }
  .context-snippet { font-size: 11px; color: var(--text-muted); display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 200px; }
  .btn-ai {
    margin-top: 3px;
    background: #faf5ff;
    color: #7c3aed;
    border: 1px solid #ddd6fe;
    border-radius: 5px;
    padding: 2px 7px;
    font-size: 11px;
    cursor: pointer;
    white-space: nowrap;
  }
  .btn-ai:hover { background: #ede9fe; }
  .btn-ai.loading { opacity: .6; pointer-events: none; }
  .ai-result {
    display: block;
    margin-top: 4px;
    font-size: 11px;
    color: #4c1d95;
    background: #faf5ff;
    border: 1px solid #ddd6fe;
    border-radius: 5px;
    padding: 4px 6px;
    white-space: normal;
    line-height: 1.4;
  }
  .ai-result:empty { display: none; }

  /* ── LOGS ── */
  .logs-grid-row { grid-column: 1 / -1; }
  .logs-pre {
    font-family: "SF Mono", "Fira Code", monospace;
    font-size: 11.5px;
    line-height: 1.6;
    color: #334155;
    background: #f8fafc;
    padding: 12px 16px;
    max-height: 220px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }

  /* ── TAB BAR ── */
  .tab-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 20px;
    background: var(--header-bg);
    border-bottom: 2px solid #1e293b;
    flex-shrink: 0;
  }
  .tab {
    padding: 10px 16px;
    border: none;
    background: transparent;
    color: #64748b;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    transition: color .15s, border-color .15s;
    white-space: nowrap;
  }
  .tab:hover { color: #94a3b8; }
  .tab.active { color: #f8fafc; border-bottom-color: var(--accent); }

  /* ── TOOL PANELS ── */
  .tool-panel { display: none; flex: 1; flex-direction: column; min-height: 0; overflow: hidden; }
  .tool-panel.active { display: flex; }

  /* ── KANBAN ── */
  .kanban-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    background: var(--card-bg);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    gap: 10px;
  }
  .kanban-board {
    display: flex;
    gap: 14px;
    padding: 16px 20px;
    overflow-x: auto;
    flex: 1;
    align-items: flex-start;
    background: var(--bg);
  }
  .kanban-col {
    flex-shrink: 0;
    width: 248px;
    display: flex;
    flex-direction: column;
    border-radius: var(--r);
    background: #f1f5f9;
    border: 2px solid transparent;
    transition: border-color .15s;
    max-height: calc(100vh - 180px);
  }
  .kanban-col.drag-over { border-color: var(--accent); background: #eff6ff; }
  .kanban-col-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px 8px;
    border-bottom: 2px solid var(--border);
    flex-shrink: 0;
  }
  .kanban-col-title {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text);
  }
  .kanban-col-count {
    background: #e2e8f0;
    color: var(--text-muted);
    border-radius: 99px;
    padding: 1px 7px;
    font-size: 11px;
    font-weight: 600;
  }
  .kanban-col-actions { display: flex; gap: 4px; }
  .col-btn {
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    padding: 2px 5px;
    border-radius: 4px;
    font-size: 12px;
  }
  .col-btn:hover { background: var(--border); color: var(--text); }
  .kanban-col-body {
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    overflow-y: auto;
    flex: 1;
    min-height: 60px;
  }
  .kanban-empty {
    text-align: center;
    color: var(--text-muted);
    font-size: 12px;
    padding: 20px 8px;
    border: 2px dashed var(--border);
    border-radius: 8px;
  }
  .kanban-card {
    background: var(--card-bg);
    border-radius: 8px;
    padding: 10px 12px;
    box-shadow: var(--shadow);
    border: 1px solid var(--border);
    cursor: grab;
    transition: box-shadow .15s, opacity .15s, transform .1s;
    user-select: none;
  }
  .kanban-card:hover { box-shadow: var(--shadow-md); }
  .kanban-card:active { cursor: grabbing; }
  .kanban-card.dragging { opacity: .4; transform: rotate(1deg); }
  .kanban-card.drag-target-above { border-top: 2px solid var(--accent); }
  .card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 6px; margin-bottom: 6px; }
  .card-title { font-size: 13px; font-weight: 600; color: var(--text); line-height: 1.3; }
  .card-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
  .card-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .card-tag {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 4px;
    background: #eff6ff;
    color: #1d4ed8;
    border: 1px solid #bfdbfe;
    white-space: nowrap;
  }
  .card-tag.thema-git  { background: #fef9c3; color: #92400e; border-color: #fde68a; }
  .card-tag.thema-uku  { background: #f0fdf4; color: #166534; border-color: #bbf7d0; }
  .card-tag.thema-vhs  { background: #faf5ff; color: #6b21a8; border-color: #e9d5ff; }
  .card-tag.thema-priv { background: #fff7ed; color: #9a3412; border-color: #fed7aa; }
  .priority-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 3px;
  }
  .prio-hoch   { background: #ef4444; }
  .prio-mittel { background: #f59e0b; }
  .prio-niedrig{ background: #94a3b8; }
  .card-notes { font-size: 11px; color: var(--text-muted); margin-top: 5px; font-style: italic; white-space: pre-wrap; }
  .card-actions { display: flex; gap: 4px; margin-top: 7px; }
  .card-action-btn {
    font-size: 11px; padding: 2px 7px; border-radius: 4px;
    border: 1px solid var(--border); background: var(--bg);
    cursor: pointer; color: var(--text-muted);
  }
  .card-action-btn:hover { background: var(--border); color: var(--text); }
  .col-stripe {
    width: 3px; height: 16px; border-radius: 2px; flex-shrink: 0;
  }

  /* ── CARD EDIT MODAL ── */
  .card-modal-body { display: flex; flex-direction: column; gap: 12px; }
  .field-row { display: flex; flex-direction: column; gap: 4px; }
  .field-row label { font-size: 12px; color: var(--text-muted); font-weight: 500; }
  .field-row input, .field-row textarea, .field-row select {
    padding: 7px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 13px;
    font-family: inherit;
    background: var(--bg);
    color: var(--text);
    outline: none;
    resize: vertical;
  }
  .field-row input:focus, .field-row textarea:focus, .field-row select:focus { border-color: var(--accent); }
  .field-row input[readonly] { background: #f1f5f9; color: var(--text-muted); }

  /* ── MODAL ── */
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(15,23,42,.6);
    z-index: 100;
    backdrop-filter: blur(2px);
    align-items: center;
    justify-content: center;
  }
  .modal-overlay.open { display: flex; }
  .modal {
    background: #fff;
    border-radius: 14px;
    padding: 28px;
    max-width: 440px;
    width: 90%;
    box-shadow: 0 20px 40px rgba(0,0,0,.2);
  }
  .modal h3 { font-size: 15px; font-weight: 600; margin-bottom: 10px; }
  .modal p  { font-size: 13px; color: var(--text-muted); margin-bottom: 20px; line-height: 1.5; }
  .modal-actions { display: flex; gap: 10px; }
  .modal-actions button {
    flex: 1; padding: 9px; border-radius: 7px; border: none;
    font-size: 13px; font-weight: 500; cursor: pointer;
  }
  .modal-btn-resume  { background: var(--accent); color: #fff; }
  .modal-btn-discard { background: var(--bg); color: var(--text); border: 1px solid var(--border) !important; }
</style>
</head>
<body>

<header>
  <div class="brand">
    <span style="font-size:20px">📬</span>
    <div>
      <h1>Mail CRM Agent</h1>
    </div>
  </div>
  <div class="update-wrap">
    <div class="meta">{{ version }} &nbsp;·&nbsp; Build {{ build }}</div>
    <button class="btn-update" id="btn-check-update" onclick="checkUpdate()">🔄 Updates prüfen</button>
    <span class="update-status" id="update-status"></span>
    <button class="btn-get-update" id="btn-get-update" style="display:none" onclick="doUpdate()">🚀 Go, get it now!</button>
  </div>
</header>

<!-- TAB BAR -->
<nav class="tab-bar">
  <button class="tab active" data-tool="mail"     onclick="switchTab('mail')">📧 Mail Scanner</button>
  <button class="tab"        data-tool="pipeline" onclick="switchTab('pipeline')">📋 Lead-Pipeline</button>
  <button class="tab"        data-tool="lehrplan" onclick="switchTab('lehrplan')">📚 Lehr-Planung</button>
</nav>

<!-- ═══ TOOL: MAIL SCANNER ═══ -->
<div class="tool-panel active" id="tool-mail">
<div class="layout">

  <!-- SIDEBAR -->
  <aside class="sidebar">
    <div class="sidebar-section">
      <h3>Zeitraum</h3>
      <label>Von</label>
      <input type="date" id="start" autocomplete="off">
      <label>Bis</label>
      <input type="date" id="end" autocomplete="off">
    </div>

    <hr>

    <div class="sidebar-section">
      <h3>Postfächer</h3>
      <label class="checkbox-label">
        <input type="checkbox" name="account" value="rb@robert-beckert.de" checked>
        rb@robert-beckert.de
      </label>
      <label class="checkbox-label">
        <input type="checkbox" name="account" value="gitarre@robert-beckert.de" checked>
        gitarre@robert-beckert.de
      </label>
      <label class="checkbox-label">
        <input type="checkbox" name="account" value="tenor@robert-beckert.de" checked>
        tenor@robert-beckert.de
      </label>
    </div>

    <button class="btn btn-save" onclick="saveSettings()">💾 Speichern</button>

    <hr>

    <div class="sidebar-section">
      <h3>Aktionen</h3>
      <button class="btn btn-primary" onclick="startAgent()">🚀 Start</button>
      <button class="btn btn-ghost"   onclick="fetch('/pause')">⏸ Pause</button>
      <button class="btn btn-ghost"   onclick="fetch('/resume')">▶ Resume</button>
      <button class="btn btn-ghost"   onclick="fetch('/pause')">🛑 Stop</button>
      <a href="/download" download="kontakte.csv" style="text-decoration:none">
        <button class="btn btn-csv">📥 CSV exportieren</button>
      </a>
      <button class="btn btn-danger"  onclick="closeApplication()" style="margin-top:8px">❌ Beenden</button>
    </div>
  </aside>

  <!-- MAIN -->
  <main class="main">

    <!-- STATUS CARD -->
    <div class="card">
      <div class="card-header">
        <h2>📊 Status</h2>
        <div id="status-badge" class="status-badge badge-stopped">
          <span class="dot"></span><span id="status-text">Nicht gestartet</span>
        </div>
      </div>
      <div class="card-body">
        <div class="status-grid">
          <div class="stat-box">
            <div class="val" id="cnt-mails">0</div>
            <div class="lbl">Geprüfte Mails</div>
          </div>
          <div class="stat-box">
            <div class="val" id="cnt-relevant">0</div>
            <div class="lbl">Relevante Treffer</div>
          </div>
          <div class="stat-box">
            <div class="val" id="cnt-contacts">0</div>
            <div class="lbl">Kontakte</div>
          </div>
          <div class="stat-box">
            <div class="val" id="cnt-pages">0</div>
            <div class="lbl">Seiten</div>
          </div>
        </div>

        <div style="display:flex; flex-direction:column; gap:8px;">
          <div class="progress-row">
            <span style="min-width:110px">Relevante Treffer</span>
            <div class="progress-bar-wrap"><div class="progress-bar-fill" id="bar-relevant"></div></div>
            <span class="progress-val"><span id="bar-relevant-val">0</span> / <span id="bar-max">1000</span></span>
          </div>
          <div class="progress-row" id="account-row" style="display:none">
            <span style="min-width:110px">Aktuelles Postfach</span>
            <span class="account-tag" id="current-account-tag"></span>
          </div>
        </div>
      </div>
    </div>

    <!-- CONTACTS CARD -->
    <div class="card">
      <div class="card-header">
        <h2>📇 Kontakte</h2>
        <div style="display:flex;align-items:center;gap:10px">
          <span id="excluded-count" class="excluded-count" style="display:none"></span>
          <span class="contacts-count" id="contacts-count">0</span>
        </div>
      </div>
      <div class="bulk-bar">
        <span class="lbl">Auswahl:</span>
        <button class="btn-sm" onclick="selectAll()">Alle</button>
        <button class="btn-sm" onclick="selectNone()">Keine</button>
        <button class="btn-sm btn-sm-primary" onclick="confirmSelection()" title="Markierte = gewollt. Nicht markierte werden dauerhaft ausgeschlossen.">✅ Auswahl bestätigen &amp; Ungewollte ausschließen</button>
      </div>
      <div class="contacts-wrap">
        <table>
          <thead>
            <tr>
              <th style="width:34px"><input type="checkbox" id="cb-all" title="Alle aus/abwählen" onchange="toggleAll(this.checked)"></th>
              <th>E-Mail</th>
              <th>Name</th>
              <th>Typ</th>
              <th>Telefon</th>
              <th>Treffer</th>
              <th>Kontext / KI-Prüfung</th>
              <th>Prüfen</th>
            </tr>
          </thead>
          <tbody id="table">
            <tr><td colspan="8" class="empty-state">Noch keine Kontakte — starte einen Scan.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </main>

  <!-- LOGS CARD (full width) -->
  <div class="card logs-grid-row">
    <div class="card-header"><h2>📜 Logs</h2></div>
    <pre class="logs-pre" id="logs"></pre>
  </div>

</div>
</div><!-- /tool-mail -->

<!-- ═══ TOOL: LEAD-PIPELINE ═══ -->
<div class="tool-panel" id="tool-pipeline">
  <div class="kanban-toolbar">
    <span style="font-size:14px;font-weight:600;color:var(--text)">📋 Lead-Pipeline</span>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn-sm btn-sm-primary" onclick="importContactsToPipeline()">📧 Kontakte importieren</button>
      <button class="btn-sm" onclick="openCardModal('pipeline','neu')">+ Karte</button>
    </div>
  </div>
  <div class="kanban-board" id="kanban-pipeline"></div>
</div>

<!-- ═══ TOOL: LEHR-PLANUNG ═══ -->
<div class="tool-panel" id="tool-lehrplan">
  <div class="kanban-toolbar">
    <span style="font-size:14px;font-weight:600;color:var(--text)">📚 Lehr-Planung</span>
    <div style="display:flex;gap:8px;align-items:center">
      <select class="btn-sm" id="lehrplan-thema-filter" onchange="applyLehrplanFilter()">
        <option value="">Alle Themen</option>
        <option value="Gitarre">🎸 Gitarre</option>
        <option value="Ukulele">🪗 Ukulele</option>
        <option value="VHS">🏫 VHS</option>
        <option value="Privat">🏠 Privat</option>
        <option value="Sonstiges">📌 Sonstiges</option>
      </select>
      <button class="btn-sm" onclick="openCardModal('lehrplan','ideen')">+ Einheit</button>
    </div>
  </div>
  <div class="kanban-board" id="kanban-lehrplan"></div>
</div>

<!-- MODAL: unterbrochener Lauf -->
<div class="modal-overlay" id="progressModal">
  <div class="modal">
    <h3>⏯ Unterbrochener Lauf gefunden</h3>
    <p id="progressInfo"></p>
    <div class="modal-actions">
      <button class="modal-btn-resume"  onclick="continueRun()">▶️ Fortsetzen</button>
      <button class="modal-btn-discard" onclick="discardProgress()">🆕 Neu starten</button>
    </div>
  </div>
</div>

<!-- MODAL: Karte bearbeiten / hinzufügen -->
<div class="modal-overlay" id="cardModal">
  <div class="modal" style="max-width:480px">
    <h3 id="cardModalTitle">Karte bearbeiten</h3>
    <div class="card-modal-body" style="margin:16px 0">
      <input type="hidden" id="cm-board">
      <input type="hidden" id="cm-card-id">
      <input type="hidden" id="cm-col">
      <!-- Pipeline-Felder -->
      <div id="cm-pipeline-fields">
        <div class="field-row"><label>E-Mail</label><input id="cm-email" type="email" readonly></div>
        <div class="field-row"><label>Name</label><input id="cm-name" type="text"></div>
        <div class="field-row"><label>Typ</label><input id="cm-type" type="text" readonly></div>
      </div>
      <!-- Lehrplan-Felder -->
      <div id="cm-lehrplan-fields" style="display:none">
        <div class="field-row"><label>Titel</label><input id="cm-title" type="text" placeholder="Thema / Einheitstitel"></div>
        <div class="field-row"><label>Themenbereich</label>
          <select id="cm-thema">
            <option value="Gitarre">🎸 Gitarre</option>
            <option value="Ukulele">🪗 Ukulele</option>
            <option value="VHS">🏫 VHS</option>
            <option value="Privat">🏠 Privat</option>
            <option value="Sonstiges">📌 Sonstiges</option>
          </select>
        </div>
        <div class="field-row"><label>Datum</label><input id="cm-datum" type="date"></div>
      </div>
      <!-- Gemeinsame Felder -->
      <div class="field-row"><label>Priorität</label>
        <select id="cm-priority">
          <option value="hoch">🔴 Hoch</option>
          <option value="mittel" selected>🟡 Mittel</option>
          <option value="niedrig">⚪ Niedrig</option>
        </select>
      </div>
      <div class="field-row"><label>Notizen</label><textarea id="cm-notes" rows="3" placeholder="Optionale Notizen …"></textarea></div>
    </div>
    <div class="modal-actions">
      <button class="modal-btn-resume"  onclick="saveCard()">💾 Speichern</button>
      <button class="modal-btn-discard" onclick="closeCardModal()">Abbrechen</button>
    </div>
  </div>
</div>

<script>
function startAgent(){
  if(!document.getElementById("start").value || !document.getElementById("end").value){
    alert("Bitte zuerst einen Zeitraum auswählen.");
    return;
  }
  const accounts = [...document.querySelectorAll('input[name="account"]:checked')].map(cb=>cb.value).join(',');
  fetch('/settings',{
    method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:`start_date=${document.getElementById("start").value}&end_date=${document.getElementById("end").value}&accounts=${accounts}`
  }).then(()=>fetch('/start'));
}

function closeApplication(){
  fetch('/close').then(()=>stopLoopAndShowClosedMessage()).catch(()=>stopLoopAndShowClosedMessage());
}

function stopLoopAndShowClosedMessage(){
  if(loopInterval){ clearInterval(loopInterval); loopInterval=null; }
  document.getElementById("status-text").innerText = "Beendet";
  document.getElementById("status-badge").className = "status-badge badge-stopped";
}

function saveSettings(){
  const accounts = [...document.querySelectorAll('input[name="account"]:checked')].map(cb=>cb.value).join(',');
  fetch('/settings',{
    method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:`start_date=${document.getElementById("start").value}&end_date=${document.getElementById("end").value}&accounts=${accounts}`
  });
}

function continueRun(){
  fetch('/continue_run').then(r=>r.json()).then(()=>{
    document.getElementById("progressModal").classList.remove("open");
  });
}

function discardProgress(){
  fetch('/discard_progress').then(r=>r.json()).then(()=>{
    document.getElementById("progressModal").classList.remove("open");
  });
}

function checkProgress(){
  fetch('/progress_status').then(r=>r.json()).then(p=>{
    if(p.has_progress){
      document.getElementById("progressInfo").innerText =
        `Zeitraum ${p.start_date} – ${p.end_date}, Seite ${p.page}, ${p.mail_count} Mails verarbeitet, ${p.relevant_count} relevante Treffer.`;
      document.getElementById("progressModal").classList.add("open");
    }
  }).catch(()=>{});
}

function loop(){
  fetch('/status').then(r=>r.json()).then(s=>{
    const badge = document.getElementById("status-badge");
    const txt   = document.getElementById("status-text");
    if(s.awaiting_login){
      badge.className="status-badge badge-login";
      txt.innerText="Warte auf Login …";
    } else if(s.running && !s.paused){
      badge.className="status-badge badge-running";
      txt.innerText="Läuft";
    } else if(s.running && s.paused){
      badge.className="status-badge badge-login";
      txt.innerText="Pausiert";
    } else if(s.ever_started){
      badge.className="status-badge badge-stopped";
      txt.innerText="Abgeschlossen";
    } else {
      badge.className="status-badge badge-stopped";
      txt.innerText="Bereit";
    }

    document.getElementById("cnt-mails").innerText     = s.mails;
    document.getElementById("cnt-relevant").innerText  = s.relevant_count;
    document.getElementById("cnt-contacts").innerText  = s.contacts;
    document.getElementById("cnt-pages").innerText     = s.page;
    document.getElementById("bar-relevant-val").innerText = s.relevant_count;
    document.getElementById("bar-max").innerText       = s.max_mails;
    document.getElementById("bar-relevant").style.width = Math.min(100,(s.relevant_count/s.max_mails)*100)+"%";

    const accRow = document.getElementById("account-row");
    if(s.current_account){
      accRow.style.display="flex";
      document.getElementById("current-account-tag").innerText = s.current_account;
    } else {
      accRow.style.display="none";
    }
  }).catch(()=>{});

  fetch('/contacts').then(r=>r.json()).then(data=>{
    document.getElementById("contacts-count").innerText = data.length;
    if(!data.length){
      document.getElementById("table").innerHTML =
        '<tr><td colspan="8" class="empty-state">Noch keine Kontakte — starte einen Scan.</td></tr>';
      return;
    }
    let html="";
    data.forEach(c=>{
      const emailId = c.email.replace(/[@.+]/g,'_');
      const snippet = (c.context||'').substring(0,90) + ((c.context||'').length>90?'…':'');
      const nameDisp = c.name || '<span style="color:var(--text-muted)">—</span>';
      html+=`<tr id="row-${emailId}">
        <td style="text-align:center"><input type="checkbox" class="contact-cb" value="${c.email}" checked></td>
        <td style="font-size:12px">${c.email}</td>
        <td style="font-size:12px">${nameDisp}</td>
        <td><span class="type-tag">${c.type}</span></td>
        <td style="font-size:12px">${c.phone||'—'}</td>
        <td><span class="hits-badge">${c.hits}</span></td>
        <td class="context-cell">
          <span class="context-snippet" title="${(c.context||'').replace(/"/g,'&quot;')}">${snippet||'<span style="color:var(--text-muted)">Kein Kontext</span>'}</span>
          <button class="btn-ai" id="ai-btn-${emailId}" onclick="runAI('${c.email}','${emailId}')">🤖 KI-Prüfung</button>
          <span class="ai-result" id="ai-res-${emailId}"></span>
        </td>
        <td>
          <div class="row-actions">
            <button class="btn-approve" title="Behalten" onclick="validateContact('${c.email}','${emailId}',true)">✅</button>
            <button class="btn-reject"  title="Dauerhaft ausschließen" onclick="validateContact('${c.email}','${emailId}',false)">⛔</button>
          </div>
        </td>
      </tr>`;
    });
    document.getElementById("table").innerHTML=html;
  }).catch(()=>{});

  fetch('/logs').then(r=>r.text()).then(t=>{
    const el=document.getElementById("logs");
    el.innerText=t;
    el.scrollTop=el.scrollHeight;
  }).catch(()=>{});
}

// ── TABS ──────────────────────────────────────────────
function switchTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.dataset.tool===name));
  document.querySelectorAll('.tool-panel').forEach(p=>p.classList.toggle('active', p.id==='tool-'+name));
  if(name==='pipeline') loadBoard('pipeline');
  if(name==='lehrplan') loadBoard('lehrplan');
}

// ── KANBAN DATA ────────────────────────────────────────
const boardCache={};
let lehrplanFilter='';

function loadBoard(board){
  fetch('/board/'+board).then(r=>r.json()).then(data=>{
    boardCache[board]=data;
    renderBoard(board, data);
  });
}

function applyLehrplanFilter(){
  lehrplanFilter=document.getElementById('lehrplan-thema-filter').value;
  if(boardCache['lehrplan']) renderBoard('lehrplan', boardCache['lehrplan']);
}

function renderBoard(board, data){
  const el=document.getElementById('kanban-'+board);
  if(!el) return;
  let html='';
  data.columns.forEach(col=>{
    let cards=data.cards.filter(c=>c.column===col.id);
    if(board==='lehrplan'&&lehrplanFilter) cards=cards.filter(c=>c.thema===lehrplanFilter);
    cards.sort((a,b)=>(a.position||0)-(b.position||0));
    html+=`<div class="kanban-col" id="col-${board}-${col.id}"
      ondragover="onColDragOver(event)"
      ondragleave="onColDragLeave(event)"
      ondrop="onColDrop(event,'${col.id}','${board}')">
      <div class="kanban-col-header">
        <div class="kanban-col-title">
          <span class="col-stripe" style="background:${col.color}"></span>
          ${col.title}
          <span class="kanban-col-count">${cards.length}</span>
        </div>
        <div class="kanban-col-actions">
          <button class="col-btn" title="Nach Priorität sortieren" onclick="sortColByPriority('${board}','${col.id}')">⇅</button>
          <button class="col-btn" title="Karte hinzufügen" onclick="openCardModal('${board}','${col.id}')">+</button>
        </div>
      </div>
      <div class="kanban-col-body" id="colbody-${board}-${col.id}">
        ${cards.length?cards.map(c=>renderCardHTML(c,board,col.id)).join('')
          :'<div class="kanban-empty">Keine Karten</div>'}
      </div>
    </div>`;
  });
  el.innerHTML=html;
}

function renderCardHTML(c, board, colId){
  const prioClass={'hoch':'prio-hoch','mittel':'prio-mittel','niedrig':'prio-niedrig'}[c.priority]||'prio-mittel';
  let title='', sub='', tags='';
  if(board==='pipeline'){
    title=c.name||c.email||'(kein Name)';
    sub=c.name?`<div class="card-sub">${c.email}</div>`:'';
    if(c.type) tags+=`<span class="card-tag">${c.type}</span>`;
  } else {
    title=c.title||'(kein Titel)';
    if(c.datum) sub=`<div class="card-sub">📅 ${c.datum}</div>`;
    const themaClass={'Gitarre':'thema-git','Ukulele':'thema-uku','VHS':'thema-vhs','Privat':'thema-priv'}[c.thema]||'';
    if(c.thema) tags+=`<span class="card-tag ${themaClass}">${c.thema}</span>`;
  }
  const notes=c.notes?`<div class="card-notes">${c.notes.substring(0,80)}${c.notes.length>80?'…':''}</div>`:'';
  return `<div class="kanban-card" id="card-${c.id}" draggable="true"
    ondragstart="onCardDragStart(event,'${c.id}','${board}')"
    ondragend="onCardDragEnd(event)"
    ondragover="onCardDragOver(event,'${c.id}')"
    ondragleave="onCardDragLeave(event,'${c.id}')"
    ondrop="onCardDrop(event,'${c.id}','${colId}','${board}')">
    <div class="card-top">
      <div><div class="card-title">${title}</div>${sub}</div>
      <span class="priority-dot ${prioClass}" title="Priorität: ${c.priority}"></span>
    </div>
    ${tags?`<div class="card-tags">${tags}</div>`:''}
    ${notes}
    <div class="card-actions">
      <button class="card-action-btn" onclick="openCardModal('${board}','${colId}','${c.id}')">✏️ Bearbeiten</button>
      <button class="card-action-btn btn-sm-danger" onclick="deleteCard('${board}','${c.id}')">🗑</button>
    </div>
  </div>`;
}

// ── DRAG & DROP ────────────────────────────────────────
let _dragCardId=null, _dragBoard=null;

function onCardDragStart(e,cardId,board){
  _dragCardId=cardId; _dragBoard=board;
  e.dataTransfer.effectAllowed='move';
  setTimeout(()=>document.getElementById('card-'+cardId)?.classList.add('dragging'),0);
}
function onCardDragEnd(e){
  document.querySelectorAll('.kanban-card').forEach(c=>c.classList.remove('dragging','drag-target-above'));
}
function onColDragOver(e){ e.preventDefault(); e.currentTarget.classList.add('drag-over'); }
function onColDragLeave(e){ e.currentTarget.classList.remove('drag-over'); }
function onColDrop(e,colId,board){
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  if(!_dragCardId) return;
  moveCard(board,_dragCardId,colId,9999);
  _dragCardId=null;
}
function onCardDragOver(e,cardId){
  e.preventDefault(); e.stopPropagation();
  document.querySelectorAll('.kanban-card').forEach(c=>c.classList.remove('drag-target-above'));
  if(cardId!==_dragCardId) document.getElementById('card-'+cardId)?.classList.add('drag-target-above');
}
function onCardDragLeave(e,cardId){
  document.getElementById('card-'+cardId)?.classList.remove('drag-target-above');
}
function onCardDrop(e,targetCardId,colId,board){
  e.preventDefault(); e.stopPropagation();
  document.querySelectorAll('.kanban-card').forEach(c=>c.classList.remove('drag-target-above'));
  if(!_dragCardId||_dragCardId===targetCardId) return;
  const data=boardCache[board];
  const target=data?.cards.find(c=>c.id===targetCardId);
  moveCard(board,_dragCardId,colId,target?target.position:9999);
  _dragCardId=null;
}

function moveCard(board,cardId,colId,pos){
  fetch(`/board/${board}/move`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:cardId,column:colId,position:pos})
  }).then(()=>loadBoard(board));
}

// ── SORT ──────────────────────────────────────────────
const prioOrder={hoch:0,mittel:1,niedrig:2};
function sortColByPriority(board,colId){
  const data=boardCache[board]; if(!data) return;
  const col=data.cards.filter(c=>c.column===colId).sort((a,b)=>(prioOrder[a.priority]||1)-(prioOrder[b.priority]||1));
  const promises=col.map((c,i)=>fetch(`/board/${board}/move`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({id:c.id,column:colId,position:i})}));
  Promise.all(promises).then(()=>loadBoard(board));
}

// ── CARD MODAL ─────────────────────────────────────────
function openCardModal(board, colId, cardId){
  const data=boardCache[board];
  const card=cardId&&data?data.cards.find(c=>c.id===cardId):null;
  document.getElementById('cm-board').value=board;
  document.getElementById('cm-card-id').value=cardId||'';
  document.getElementById('cm-col').value=colId;
  document.getElementById('cardModalTitle').innerText=card?'Karte bearbeiten':'Neue Karte';
  document.getElementById('cm-pipeline-fields').style.display=board==='pipeline'?'':'none';
  document.getElementById('cm-lehrplan-fields').style.display=board==='lehrplan'?'':'none';
  if(board==='pipeline'){
    document.getElementById('cm-email').value=card?.email||'';
    document.getElementById('cm-name').value=card?.name||'';
    document.getElementById('cm-type').value=card?.type||'';
  } else {
    document.getElementById('cm-title').value=card?.title||'';
    document.getElementById('cm-thema').value=card?.thema||'Gitarre';
    document.getElementById('cm-datum').value=card?.datum||'';
  }
  document.getElementById('cm-priority').value=card?.priority||'mittel';
  document.getElementById('cm-notes').value=card?.notes||'';
  document.getElementById('cardModal').classList.add('open');
}
function closeCardModal(){ document.getElementById('cardModal').classList.remove('open'); }

function saveCard(){
  const board=document.getElementById('cm-board').value;
  const cardId=document.getElementById('cm-card-id').value;
  const col=document.getElementById('cm-col').value;
  const payload={
    column:col,
    priority:document.getElementById('cm-priority').value,
    notes:document.getElementById('cm-notes').value,
  };
  if(board==='pipeline'){
    payload.name=document.getElementById('cm-name').value;
    payload.email=document.getElementById('cm-email').value;
    payload.type=document.getElementById('cm-type').value;
  } else {
    payload.title=document.getElementById('cm-title').value;
    payload.thema=document.getElementById('cm-thema').value;
    payload.datum=document.getElementById('cm-datum').value;
  }
  const url=cardId?`/board/${board}/card/${cardId}`:`/board/${board}/add`;
  const method=cardId?'PUT':'POST';
  fetch(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(()=>{ closeCardModal(); loadBoard(board); });
}

function deleteCard(board,cardId){
  if(!confirm('Karte löschen?')) return;
  fetch(`/board/${board}/card/${cardId}`,{method:'DELETE'}).then(()=>loadBoard(board));
}

function importContactsToPipeline(){
  const contacts=[...document.querySelectorAll('.contact-cb:checked')].map(cb=>cb.value);
  if(!contacts.length){ alert('Keine Kontakte ausgewählt (Mail Scanner Tab → Checkboxen setzen).'); return; }
  fetch('/board/pipeline/import_contacts',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({emails:contacts})
  }).then(r=>r.json()).then(d=>{ loadBoard('pipeline'); alert(`✅ ${d.added} Kontakt(e) importiert`); });
}

function checkUpdate(){
  const btn=document.getElementById('btn-check-update');
  const status=document.getElementById('update-status');
  const getBtn=document.getElementById('btn-get-update');
  btn.disabled=true;
  btn.innerText='⏳ Prüfe …';
  status.className='update-status';
  status.innerText='';
  getBtn.style.display='none';
  fetch('/check_update').then(r=>r.json()).then(d=>{
    btn.disabled=false;
    btn.innerText='🔄 Updates prüfen';
    if(d.error){
      status.className='update-status err';
      status.innerText='❌ '+d.error;
    } else if(d.has_update){
      status.className='update-status avail';
      status.innerText='🆕 New version available ('+d.commits.length+' Commit'+(d.commits.length>1?'s':'')+')';
      getBtn.style.display='';
    } else {
      status.className='update-status ok';
      status.innerText='✅ No newer version available';
    }
  }).catch(()=>{
    btn.disabled=false;
    btn.innerText='🔄 Updates prüfen';
    status.className='update-status err';
    status.innerText='❌ Verbindungsfehler';
  });
}

function doUpdate(){
  const getBtn=document.getElementById('btn-get-update');
  const status=document.getElementById('update-status');
  if(!confirm('Update jetzt herunterladen und Agent neu starten?'))return;
  getBtn.style.display='none';
  status.className='update-status avail';
  status.innerText='⏳ Update läuft … Agent startet gleich neu.';
  fetch('/do_update').then(()=>{
    status.innerText='⏳ Neustart … Seite wird automatisch neu geladen.';
    waitForRestart();
  }).catch(()=>{
    status.className='update-status err';
    status.innerText='❌ Fehler beim Update';
  });
}

function waitForRestart(){
  setTimeout(()=>{
    fetch('/status').then(()=>location.reload()).catch(()=>waitForRestart());
  }, 2000);
}

function selectAll(){
  document.querySelectorAll('.contact-cb').forEach(cb=>cb.checked=true);
  document.getElementById('cb-all').checked=true;
}
function selectNone(){
  document.querySelectorAll('.contact-cb').forEach(cb=>cb.checked=false);
  document.getElementById('cb-all').checked=false;
}
function toggleAll(checked){
  document.querySelectorAll('.contact-cb').forEach(cb=>cb.checked=checked);
}

function confirmSelection(){
  const approved=[...document.querySelectorAll('.contact-cb:checked')].map(cb=>cb.value);
  const total=document.querySelectorAll('.contact-cb').length;
  const willExclude=total-approved.length;
  if(!confirm(`Auswahl bestätigen?\n✅ ${approved.length} Kontakte werden behalten.\n⛔ ${willExclude} Kontakte werden dauerhaft ausgeschlossen und erscheinen in zukünftigen Scans nicht mehr.`))return;
  fetch('/set_exclusions',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({approved})
  }).then(r=>r.json()).then(d=>{
    const el=document.getElementById('excluded-count');
    el.style.display='';
    el.innerText=`⛔ ${d.excluded} ausgeschlossen`;
  }).catch(()=>alert('Fehler beim Speichern der Auswahl'));
}

function validateContact(email, emailId, approved){
  fetch('/validate_contact',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email,approved})
  }).then(r=>r.json()).then(()=>{
    if(!approved){
      const row=document.getElementById('row-'+emailId);
      if(row) row.remove();
    } else {
      const row=document.getElementById('row-'+emailId);
      if(row) row.style.background='#f0fdf4';
    }
  });
}

function runAI(email, emailId){
  const btn=document.getElementById('ai-btn-'+emailId);
  const res=document.getElementById('ai-res-'+emailId);
  if(btn) btn.classList.add('loading');
  if(res) res.innerText='Analysiere …';
  fetch('/ai_review/'+encodeURIComponent(email))
    .then(r=>r.json())
    .then(d=>{
      if(res) res.innerText=d.result||d.error||'Keine Antwort';
      if(btn) btn.classList.remove('loading');
    }).catch(()=>{
      if(res) res.innerText='Fehler';
      if(btn) btn.classList.remove('loading');
    });
}

let loopInterval=setInterval(loop,1500);
loop();

window.onload=()=>{
  document.getElementById("start").value="";
  document.getElementById("end").value="";
  checkProgress();
};
</script>
</body>
</html>
"""


if __name__ == "__main__":
    load_initial_data()
    log_build_start()
    free_port(PORT)
    app.run(host="0.0.0.0", port=PORT)
