#!/usr/bin/env python3
"""fetch_mail.py — Outlook-Posteingang via Microsoft Graph holen.

Übernommen aus dem bestehenden Projekt "CRM Agent":
- Scope Mail.Read, MSAL Public-Client Device-Flow mit Token-Cache.
- Holt Mails ab dem Lauf-Wasserzeichen (state.json) oder optionalem --since.

Gibt ein JSON-Array von {subject, body, from_name, from_email, received} auf stdout aus.
HTML-Bodies werden grob zu Klartext bereinigt (Markup entfernt).
"""
import argparse, json, os, re, sys, time
import requests
import msal

CLIENT_ID = os.environ.get("GRAPH_CLIENT_ID", "3ae26d06-295e-4c85-a368-d56d97c373e7")
TENANT_ID = os.environ.get("GRAPH_TENANT_ID", "0e4fb204-9f60-4bfe-b441-83bd03ad0e6b")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/Mail.Read"]
CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "token_cache.bin")
TAG_RE = re.compile(r"<[^>]+>")


def get_token() -> str:
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_FILE):
        cache.deserialize(open(CACHE_FILE).read())
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        print(flow.get("message", ""), file=sys.stderr)
        result = app.acquire_token_by_device_flow(flow)
    if cache.has_state_changed:
        open(CACHE_FILE, "w").write(cache.serialize())
    if "access_token" not in result:
        print("Login-Fehler:", result.get("error_description"), file=sys.stderr)
        sys.exit(1)
    return result["access_token"]


def html_to_text(s: str) -> str:
    return re.sub(r"\s+\n", "\n", TAG_RE.sub(" ", s or "")).strip()


GRAPH = "https://graph.microsoft.com/v1.0"


def _get(url, headers, tries=5):
    """GET mit Drosselungs-Behandlung (429 / 503 + Retry-After)."""
    for attempt in range(tries):
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code in (429, 503):
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            print(f"⏳ Drosselung ({r.status_code}) — warte {wait}s …", file=sys.stderr)
            time.sleep(wait)
            continue
        return r
    return r


def _excluded_folder_ids(headers, folders):
    """IDs der auszuschließenden well-known Ordner (z. B. junkemail, deleteditems)."""
    ids = set()
    for wk in folders:
        r = _get(f"{GRAPH}/me/mailFolders/{wk}", headers)
        if r.status_code == 200:
            ids.add(r.json().get("id"))
    return ids


def fetch_messages(since: str = None, start: str = None, end: str = None,
                   maxn: int = 5000, exclude_folders=("junkemail", "deleteditems")) -> list:
    """Holt Mails über ALLE Ordner, außer den in `exclude_folders` genannten.

    - Default `("junkemail", "deleteditems")`: Junk/Spam UND Papierkorb ausgeschlossen.
    - start/end: Datumsbereich 'YYYY-MM-DD' (inklusive).
    - since: ISO-Wasserzeichen für den inkrementellen Tagesbetrieb (nur ohne start).
    Wirft RuntimeError bei echten API-Fehlern.
    """
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    excluded = _excluded_folder_ids(headers, exclude_folders) if exclude_folders else set()

    filt = []
    if start:
        filt.append(f"receivedDateTime ge {start}T00:00:00Z")
    if end:
        filt.append(f"receivedDateTime le {end}T23:59:59Z")
    if since and not start:
        filt.append(f"receivedDateTime gt {since}")

    url = (f"{GRAPH}/me/messages?$top=50"
           "&$select=subject,from,body,receivedDateTime,parentFolderId"
           "&$orderby=receivedDateTime desc")
    if filt:
        url += "&$filter=" + " and ".join(filt)

    out, count = [], 0
    while url and count < maxn:
        r = _get(url, headers)
        if r.status_code != 200:
            raise RuntimeError(f"Graph-API-Fehler {r.status_code}: {r.text[:300]}")
        data = r.json()
        for m in data.get("value", []):
            if m.get("parentFolderId") in excluded:
                continue
            addr = m.get("from", {}).get("emailAddress", {})
            out.append({
                "subject": m.get("subject", ""),
                "body": html_to_text(m.get("body", {}).get("content", "")),
                "from_name": addr.get("name", ""),
                "from_email": addr.get("address", ""),
                "received": m.get("receivedDateTime", ""),
            })
            count += 1
            if count >= maxn:
                break
        url = data.get("@odata.nextLink")
    return out


def main():
    ap = argparse.ArgumentParser(description="Outlook-Mailabruf (alle Ordner außer Junk/Gelöscht).")
    ap.add_argument("--start", help="Startdatum YYYY-MM-DD (Backfill-/Testlauf)")
    ap.add_argument("--end", help="Enddatum YYYY-MM-DD")
    ap.add_argument("--since", help="ISO-Wasserzeichen; sonst aus state.json")
    ap.add_argument("--state", default=os.path.join(os.path.dirname(__file__), "..", "state.json"))
    ap.add_argument("--max", type=int, default=5000)
    ap.add_argument("--include-junk", action="store_true",
                    help="auch Junk/Spam einbeziehen (Default: ausgeschlossen)")
    ap.add_argument("--include-deleted", action="store_true",
                    help="auch den Papierkorb (Gelöscht) einbeziehen (Default: ausgeschlossen)")
    args = ap.parse_args()

    since = args.since
    if not since and not args.start and os.path.exists(args.state):
        since = json.load(open(args.state)).get("last_received")

    exclude = []
    if not args.include_junk:
        exclude.append("junkemail")
    if not args.include_deleted:
        exclude.append("deleteditems")

    out = fetch_messages(since=since, start=args.start, end=args.end, maxn=args.max,
                         exclude_folders=tuple(exclude))
    print(f"✉️  {len(out)} Mails geholt", file=sys.stderr)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
