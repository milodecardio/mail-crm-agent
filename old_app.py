#!/usr/bin/env python3
"""app.py — Flask-Dashboard für mail-crm-agent (Hybrid).

- Zeigt den letzten Lauf, startet bei Bedarf einen neuen (fetch + Triage + Routing).
- Stellt die Review-Queue interaktiv dar: je Fall Kategorie-Auswahl, In-CRM-Checkbox,
  editierbare (nur belegte) Kontaktfelder und eine belegte Empfehlung.
- "Übernehmen" schreibt decisions.json und exportiert nach kontakte.csv + kontakte.json.

Port: bindet an 127.0.0.1 (nur lokal). Ist der gewünschte Port belegt, wird automatisch
der nächste freie genommen und der Browser auf genau diesem geöffnet — kein Portproblem mehr.
"""
import datetime, json, os, socket, sys, threading, webbrowser
from flask import Flask, request, jsonify, render_template_string, send_file, redirect, url_for

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "scripts"))
import fetch_mail            # noqa: E402
import triage as triage_mod  # noqa: E402
import process as process_mod  # noqa: E402

app = Flask(__name__)
DESIRED_PORT = int(os.environ.get("PORT", "5050"))
_state = {"running": False, "log": "", "mode": ""}


def _p(name):
    return os.path.join(BASE, name)


def _load(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def find_free_port(start: int, host: str = "127.0.0.1", tries: int = 50) -> int:
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex((host, p)) != 0:  # nichts lauscht hier → frei
                return p
    raise RuntimeError(f"Kein freier Port im Bereich {start}–{start+tries}")


def _do_run(start: str, end: str):
    _state["running"] = True
    _state["mode"] = triage_mod.available_mode()
    try:
        mails = fetch_mail.fetch_messages(start=start or None, end=end or None)
        _state["log"] = f"{len(mails)} Mails geholt, triagiere ({_state['mode']}) …"
        results, mode = triage_mod.triage_batch(mails)
        _state["mode"] = mode
        s = process_mod.process(mails, results)
        _state["log"] = f"{len(mails)} Mails · {s['auto']} auto · {s['review']} Review · Modus: {mode}"
    except Exception as e:
        _state["log"] = f"Fehler: {e}"
    finally:
        _state["running"] = False


@app.route("/")
def home():
    review = _load(_p("review_queue.json"), [])
    last = _load(_p("last_run.json"), {})
    contacts = _load(_p("kontakte.json"), [])
    today = datetime.date.today().isoformat()
    return render_template_string(HTML, review=review, last=last,
                                  n_contacts=len(contacts), running=_state["running"],
                                  log=_state["log"], mode=triage_mod.available_mode(),
                                  start_default="2024-01-01", end_default=today)


@app.route("/run", methods=["POST"])
def trigger():
    if not _state["running"]:
        start = request.form.get("start", "")
        end = request.form.get("end", "")
        threading.Thread(target=_do_run, args=(start, end), daemon=True).start()
    return redirect(url_for("home"))


@app.route("/status")
def status():
    return jsonify(running=_state["running"], log=_state["log"])


@app.route("/approve", methods=["POST"])
def approve():
    decisions = request.get_json(force=True) or []
    json.dump(decisions, open(_p("decisions.json"), "w"), ensure_ascii=False, indent=2)
    # bestätigte Fälle aus der Queue entfernen
    approved = {(d.get("email"), d.get("received")) for d in decisions}
    queue = [q for q in _load(_p("review_queue.json"), [])
             if (q.get("from_email"), q.get("received")) not in approved]
    json.dump(queue, open(_p("review_queue.json"), "w"), ensure_ascii=False, indent=2)
    import subprocess
    subprocess.run([sys.executable, _p("scripts/export.py")], check=False)
    return jsonify(ok=True, exported=len([d for d in decisions if d.get("include")]))


@app.route("/download/<kind>")
def download(kind):
    path = _p("kontakte.csv" if kind == "csv" else "kontakte.json")
    if not os.path.exists(path):
        return "Noch keine Kontakte exportiert.", 404
    return send_file(path, as_attachment=True)


HTML = """
<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<title>mail-crm-agent</title><style>
 body{font-family:-apple-system,system-ui,sans-serif;max-width:880px;margin:24px auto;padding:0 16px;color:#1a1a1a}
 h1{font-size:22px} .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}
 button,a.btn{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer;text-decoration:none}
 a.btn.sec{background:#475569}
 .pill{background:#eef2ff;border-radius:999px;padding:4px 10px;font-size:13px}
 .item{border:1px solid #ddd;border-radius:10px;padding:14px;margin:14px 0}
 .meta{color:#555;font-size:13px} .subject{font-weight:600;margin:4px 0}
 .body{background:#f7f7f7;border-radius:6px;padding:8px;font-size:13px;white-space:pre-wrap;max-height:140px;overflow:auto}
 .rec{background:#eef5ff;border-left:3px solid #3b82f6;padding:8px;margin:8px 0;font-size:14px}
 .opts label{display:inline-block;margin:4px 12px 4px 0;font-size:14px}
 .fields input{font-size:13px;padding:4px 6px;margin:2px 0;width:100%;box-sizing:border-box}
 .hint{color:#666;font-size:13px}
</style></head><body>
<h1>📬 mail-crm-agent</h1>
<form method="post" action="/run">
<div class="bar">
 <label>Von <input type="date" name="start" value="{{ start_default }}"></label>
 <label>Bis <input type="date" name="end" value="{{ end_default }}"></label>
 <button {{ "disabled" if running }}>▶ Run — Mails prüfen</button>
 <a class="btn sec" href="/download/csv">📥 CSV</a>
 <a class="btn sec" href="/download/json">📥 JSON</a>
 <span class="pill">Kontakte gesamt: {{ n_contacts }}</span>
 {% if mode == 'cli' %}<span class="pill" style="background:#e8f5e9">Triage: Claude-Abo (kostenfrei)</span>
 {% elif mode == 'api' %}<span class="pill" style="background:#e8f5e9">Triage: API-Key</span>
 {% else %}<span class="pill" style="background:#fff4e5">Triage: Fallback (Heuristik) — alles in Review</span>{% endif %}
</div>
</form>
<div class="hint" id="st">Letzter Lauf: {{ last.get('ran_at','—') }} · {{ log or '—' }}</div>

<h3>📋 Review ({{ review|length }})</h3>
{% if not review %}<p class="hint">Keine offenen Fälle. 🎉</p>{% endif %}
<div id="items"></div>
{% if review %}<button onclick="save()">✅ Auswahl übernehmen &amp; exportieren</button>{% endif %}

<script>
const ITEMS = {{ review|tojson }};
const CATS = ["neuer_interessent","bestandskunde","organisation","absage","irrelevant"];
function esc(s){return (s||"").replace(/</g,"&lt;");}
function render(){
 const root=document.getElementById("items");
 ITEMS.forEach((it,i)=>{
  const d=document.createElement("div");d.className="item";
  const cats=CATS.map(c=>`<label><input type="radio" name="cat_${i}" value="${c}" ${c===it.category?"checked":""}> ${c}</label>`).join("");
  d.innerHTML=`<div class="meta">${esc(it.from_name)} &lt;${esc(it.from_email)}&gt; · ${it.received||""}</div>
   <div class="subject">${esc(it.subject)||"(kein Betreff)"}</div>
   <div class="body">${esc(it.body_excerpt)}</div>
   <div class="rec"><b>Empfehlung:</b> ${esc(it.recommendation)||"Manuell prüfen."}</div>
   <div class="opts">${cats}</div>
   <label><input type="checkbox" id="crm_${i}" ${it.suggest_include?"checked":""}> In CRM aufnehmen</label>
   <div class="fields">
     <input id="name_${i}" placeholder="Name" value="${esc(it.contact?.name)}">
     <input id="email_${i}" placeholder="E-Mail" value="${esc(it.contact?.email||it.from_email)}">
     <input id="phone_${i}" placeholder="Telefon" value="${esc(it.contact?.phone)}">
     <input id="interest_${i}" placeholder="Interesse" value="${esc(it.contact?.interest)}">
   </div>`;
  root.appendChild(d);
 });
}
function save(){
 const out=ITEMS.map((it,i)=>({
   email:document.getElementById("email_"+i).value||it.from_email, received:it.received,
   include:document.getElementById("crm_"+i).checked,
   category:(document.querySelector(`input[name=cat_${i}]:checked`)||{}).value||it.category,
   contact:{name:document.getElementById("name_"+i).value,email:document.getElementById("email_"+i).value,
            phone:document.getElementById("phone_"+i).value,interest:document.getElementById("interest_"+i).value},
   source:"review"}));
 fetch("/approve",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(out)})
  .then(r=>r.json()).then(r=>{alert(r.exported+" Kontakt(e) exportiert.");location.reload();});
}
render();
// Live-Status während eines Laufs
setInterval(()=>fetch("/status").then(r=>r.json()).then(s=>{
 document.getElementById("st").innerText="Status: "+(s.running?"🟢 läuft …":"⚪ bereit")+" · "+(s.log||"—");
 if(s.running)setTimeout(()=>location.reload(),2500);
}),2000);
</script>
</body></html>
"""

PORTFILE = os.path.join(BASE, ".port")

if __name__ == "__main__":
    import preflight
    force = "--force" in sys.argv
    if not preflight.run_and_report("dashboard", force=force):
        # Läuft vermutlich schon → bestehendes Dashboard öffnen statt zweiter Instanz.
        if os.path.exists(PORTFILE):
            p = open(PORTFILE).read().strip()
            print(f"ℹ︎ Dashboard läuft bereits → http://127.0.0.1:{p}")
            webbrowser.open(f"http://127.0.0.1:{p}")
            sys.exit(0)
        sys.exit(1)
    port = find_free_port(DESIRED_PORT)
    open(PORTFILE, "w").write(str(port))
    import atexit
    atexit.register(lambda: os.path.exists(PORTFILE) and os.remove(PORTFILE))
    url = f"http://127.0.0.1:{port}"
    if port != DESIRED_PORT:
        print(f"ℹ︎ Port {DESIRED_PORT} belegt → nutze {port}")
    print(f"🌐 {url}")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port)
