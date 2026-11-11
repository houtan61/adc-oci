#!/usr/bin/env python3
"""ADC lab check-in portal + admin CLI (Python stdlib only).

Students check in with the class passphrase and their registration email,
get the next free pod and see their ports, logins and the lab guide.

  adc-portal serve [--port 8080] [--admin-port 8005]
                                          run the portal (+ admin page if admin_password is set)
  adc-portal list                         who has which pod
  adc-portal release POD|EMAIL            free a pod
  adc-portal assign EMAIL POD [--first F --last L]
  adc-portal disable POD | enable POD     keep a pod out of the pool
  adc-portal export                       CSV of check-ins
  adc-portal roster import FILE.csv       registration export (keeps Email/First/Last only)
  adc-portal roster add EMAIL FIRST LAST | remove EMAIL | list | clear

No roster = open check-in (passphrase + name + email).
"""

import argparse
import base64
import csv
import email
import email.policy
import io
import fcntl
import hashlib
import hmac
import html
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(os.environ.get("ADC_PORTAL_DIR", "/opt/adc-portal"))
DATA_DIR = Path(os.environ.get("ADC_PORTAL_DATA", "/var/lib/adc-portal"))
CONFIG = APP_DIR / "config.json"
GUIDE_DIR = APP_DIR / "guide"
STATE = DATA_DIR / "state.json"
ROSTER = DATA_DIR / "roster.json"
SECRET = DATA_DIR / "secret"
LOCK = DATA_DIR / ".lock"
HEALTH = DATA_DIR / "health.json"
POWER_LOG = DATA_DIR / "power-on.log"
HEALTH_EVERY = 60
COOKIE = "adclab"


# ---------------------------------------------------------------- storage

def load_config():
    return json.loads(CONFIG.read_text())


@contextmanager
def locked():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load_state():
    st = _read(STATE, {})
    st.setdefault("pods", {})        # pod -> {email, first, last, time}
    st.setdefault("disabled", [])
    return st


def load_roster(cfg=None):
    """Roster file on the VM wins; first run seeds it from Terraform's config."""
    if ROSTER.exists():
        return _read(ROSTER, [])
    cfg = cfg or load_config()
    roster = [r for r in cfg.get("roster", []) if r.get("email")]
    if roster:
        _write(ROSTER, roster)
    return roster


def secret_key():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET.exists():
        SECRET.write_text(secrets.token_hex(32))
        os.chmod(SECRET, 0o600)
    return SECRET.read_text().strip().encode()


def norm_email(e):
    return (e or "").strip().lower()


def norm_phrase(p):
    return "".join((p or "").split()).lower()


def pod_of(st, email):
    for pod, a in st["pods"].items():
        if a.get("email") == email:
            return pod
    return None


def assign(cfg, email, first="", last="", pod=None):
    """Return the student's pod, giving them the next free one if needed."""
    with locked():
        st = load_state()
        current = pod_of(st, email)
        if pod is None and current:
            return current
        ids = [p["id"] for p in cfg["pods"]]
        if pod is None:
            free = [p for p in ids if p not in st["pods"] and p not in st["disabled"]]
            if not free:
                return None
            pod = free[0]
        elif pod not in ids:
            raise SystemExit(f"unknown pod {pod}")
        elif pod in st["pods"] and st["pods"][pod]["email"] != email:
            print(f"note: {pod} was {st['pods'][pod]['email']}; they no longer have a pod")
        if current and current != pod:
            del st["pods"][current]
        st["pods"][pod] = {"email": email, "first": first, "last": last,
                           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write(STATE, st)
        return pod


# ---------------------------------------------------------------- cookies

def sign(email):
    mac = hmac.new(secret_key(), email.encode(), hashlib.sha256).hexdigest()[:32]
    return urllib.parse.quote(f"{email}|{mac}")


def verify(value):
    try:
        email, mac = urllib.parse.unquote(value).rsplit("|", 1)
    except ValueError:
        return None
    good = hmac.new(secret_key(), email.encode(), hashlib.sha256).hexdigest()[:32]
    return email if hmac.compare_digest(mac, good) else None


# ---------------------------------------------------------------- pages

CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:#f4f5f7;color:#1d1d1f}
header{background:#da291c;color:#fff;padding:18px 28px;font-size:20px;font-weight:600}
main{max-width:820px;margin:28px auto;padding:0 20px}
.card{background:#fff;border-radius:10px;padding:24px 28px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:20px}
h1{font-size:22px;margin:0 0 6px} h2{font-size:17px;margin:0 0 12px}
label{display:block;font-size:14px;margin:14px 0 4px;color:#444}
input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #ccc;border-radius:6px;font-size:15px}
button,.btn{display:inline-block;margin-top:18px;background:#da291c;color:#fff;border:0;border-radius:6px;padding:10px 20px;font-size:15px;text-decoration:none;cursor:pointer}
table{border-collapse:collapse;width:100%;font-size:14px}
td,th{text-align:left;padding:8px 6px;border-bottom:1px solid #eee;vertical-align:top}
code{background:#f1f1f3;padding:2px 6px;border-radius:4px;font-size:13px}
.err{background:#fdecea;color:#a1271b;padding:10px 14px;border-radius:6px;margin-top:12px}
.pod{font-size:30px;font-weight:700;color:#da291c}
.muted{color:#777;font-size:13px}
"""


def page(cfg, body):
    title = html.escape(cfg.get("class_name", "Lab"))
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{CSS}</style></head>"
            f"<body><header>{title}</header><main>{body}</main></body></html>")


def checkin_form(cfg, roster, error="", email=""):
    names = "" if roster else (
        "<label>First name</label><input name='first' required>"
        "<label>Last name</label><input name='last' required>")
    hint = ("Use the email address you registered with." if roster
            else "Enter your name and email address.")
    err = f"<div class='err'>{html.escape(error)}</div>" if error else ""
    return page(cfg, f"""
<div class='card'><h1>Check in</h1><p class='muted'>{hint}</p>
<form method='post' action='/checkin'>
<label>Class passphrase</label><input name='passphrase' required autocomplete='off'>
{names}
<label>Email</label><input name='email' type='email' required value='{html.escape(email)}'>
<button type='submit'>Get my lab</button>
</form>{err}</div>""")


def pod_page(cfg, pod, who):
    host = cfg["public_host"]
    p = next(x for x in cfg["pods"] if x["id"] == pod)
    rows = []
    for s in cfg["services"]:
        port = p["port_start"] + s["suffix"]
        label = html.escape(s["label"])
        if s["proto"] == "https":
            url = f"https://{host}:{port}/"
            rows.append(f"<tr><td>{label}</td><td><a href='{url}' target='_blank'>{url}</a></td></tr>")
        elif s["proto"] == "ssh":
            user = s.get("user") or "admin"
            rows.append(f"<tr><td>{label}</td><td><code>ssh -p {port} {html.escape(user)}@{host}</code></td></tr>")
        else:
            rows.append(f"<tr><td>{label}</td><td><code>{host}:{port}</code></td></tr>")
    terms = []
    for t in cfg.get("web_terminals", []):
        url = f"https://{host}:{p['port_start'] + cfg.get('fs_gui_suffix', 0)}{t['path']}"
        terms.append(f"<tr><td>{html.escape(t['label'])}</td><td><a href='{html.escape(url)}' target='_blank'>open terminal</a></td></tr>")
    term_card = (f"<div class='card'><h2>Terminals (in your browser)</h2><table>{''.join(terms)}</table></div>"
                 if terms else "")
    console = ""
    g = cfg.get("fs_guest")
    if g:
        url = f"https://{host}:{p['port_start'] + cfg.get('fs_gui_suffix', 0)}/"
        console = (f"<div class='card'><h2>Lab console</h2>"
                   f"<p><a class='btn' style='margin-top:0' href='{url}' target='_blank'>Open the lab console</a></p>"
                   f"<p>Log in as <code>{html.escape(g['user'])}</code> / <code>{html.escape(g.get('password') or '')}</code>. "
                   f"It shows your topology and the SERIAL / SSH / VNC buttons for every device.</p>"
                   f"<p class='muted'>Its HTTP / HTTPS buttons don't work from here: use the device links below.</p></div>")
    creds = "".join(
        f"<tr><td>{html.escape(c['device'])}</td><td><code>{html.escape(c['user'])}</code></td>"
        f"<td><code>{html.escape(c['password'])}</code></td></tr>" for c in cfg.get("credentials", []))
    name = html.escape(" ".join(x for x in (who.get("first"), who.get("last")) if x) or who.get("email", ""))
    guide = ("<a class='btn' href='/guide/' target='_blank'>Open the lab guide</a>"
             if (GUIDE_DIR / "index.html").exists() else "")
    return page(cfg, f"""
<div class='card'><p class='muted'>Welcome, {name}</p>
<div>Your pod</div><div class='pod'>{pod}</div>
<p class='muted'>Browsers will warn about the self-signed certificates on the lab devices; accept them to continue.</p>
{guide}</div>
{console}
<div class='card'><h2>Device web GUIs</h2><table>{''.join(rows)}</table></div>
{term_card}
<div class='card'><h2>Logins</h2><table><tr><th>Device</th><th>User</th><th>Password</th></tr>{creds}</table></div>
<p class='muted'>Not you? <a href='/forget'>Check in again</a></p>""")


# ---------------------------------------------------------------- web server

class Handler(BaseHTTPRequestHandler):
    server_version = "adc-portal"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.headers.get("X-Forwarded-For", self.client_address[0]), fmt % args))

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, where, cookie=None):
        self.send_response(303)
        self.send_header("Location", where)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _me(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        c = cookies.SimpleCookie()
        try:
            c.load(raw)
        except cookies.CookieError:
            return None
        return verify(c[COOKIE].value) if COOKIE in c else None

    def do_GET(self):
        cfg = load_config()
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            st = load_state()
            return self._send(200, json.dumps({
                "pods": len(cfg["pods"]), "assigned": len(st["pods"]),
                "roster": len(load_roster(cfg)), "guide": (GUIDE_DIR / "index.html").exists()}),
                "application/json")
        if path == "/forget":
            return self._redirect("/", f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
        email = self._me()
        st = load_state()
        pod = pod_of(st, email) if email else None
        if path.startswith("/guide"):
            if not pod:
                return self._redirect("/")
            return self._static(path)
        if path != "/":
            return self._send(404, page(cfg, "<div class='card'>Not found. <a href='/'>Home</a></div>"))
        if pod:
            return self._send(200, pod_page(cfg, pod, st["pods"][pod]))
        return self._send(200, checkin_form(cfg, load_roster(cfg)))

    def _static(self, path):
        if path == "/guide":
            return self._redirect("/guide/")
        rel = urllib.parse.unquote(path[len("/guide/"):])
        target = (GUIDE_DIR / rel).resolve()
        root = GUIDE_DIR.resolve()
        if root not in target.parents and target != root:
            return self._send(403, "forbidden", "text/plain")
        if target.is_dir():
            if not path.endswith("/"):
                return self._redirect(path + "/")
            target = target / "index.html"
        if not target.is_file():
            return self._send(404, "not found", "text/plain")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def do_POST(self):
        cfg = load_config()
        if urllib.parse.urlparse(self.path).path != "/checkin":
            return self._send(404, "not found", "text/plain")
        length = min(int(self.headers.get("Content-Length") or 0), 10000)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace"))
        f = {k: (v[0] if v else "") for k, v in form.items()}
        email = norm_email(f.get("email"))
        roster = load_roster(cfg)
        if norm_phrase(f.get("passphrase")) != norm_phrase(cfg["passphrase"]):
            time.sleep(1)
            return self._send(200, checkin_form(cfg, roster, "That passphrase isn't right.", email))
        if not email or "@" not in email:
            return self._send(200, checkin_form(cfg, roster, "Please enter a valid email address.", email))
        if roster:
            rec = next((r for r in roster if r["email"] == email), None)
            if not rec:
                return self._send(200, checkin_form(
                    cfg, roster, "That email isn't on the class list. Please see the instructor.", email))
            first, last = rec.get("first", ""), rec.get("last", "")
        else:
            first, last = f.get("first", "").strip()[:60], f.get("last", "").strip()[:60]
            if not first or not last:
                return self._send(200, checkin_form(cfg, roster, "Please enter your name.", email))
        pod = assign(cfg, email, first, last)
        if not pod:
            return self._send(200, checkin_form(cfg, roster, "All pods are taken. Please see the instructor.", email))
        self._redirect("/", f"{COOKIE}={sign(email)}; Max-Age=1209600; Path=/; HttpOnly; SameSite=Lax")



# ---------------------------------------------------------------- shared admin actions (CLI + admin page)

def release(target):
    with locked():
        st = load_state()
        key = norm_email(target) if "@" in target else target.strip()
        pod = pod_of(st, key) if "@" in key else key
        if pod in st["pods"]:
            who = st["pods"][pod]["email"]
            del st["pods"][pod]
            _write(STATE, st)
            return f"released {pod} ({who})"
        return "nothing to release"


def set_disabled(pod, on):
    with locked():
        st = load_state()
        if on and pod not in st["disabled"]:
            st["disabled"].append(pod)
        if not on and pod in st["disabled"]:
            st["disabled"].remove(pod)
        _write(STATE, st)
    return ("disabled " if on else "enabled ") + pod


def _pick(row, *names):
    low = {k.strip().lower(): v for k, v in row.items() if k}
    for n in names:
        if n in low and low[n]:
            return low[n].strip()
    return ""


def roster_from_csv(text):
    """Keep only Email / First Name / Last Name from a registration export."""
    out = {}
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        e = norm_email(_pick(r, "email", "email address", "e-mail"))
        if e and "@" in e:
            out[e] = {"email": e, "first": _pick(r, "first name", "first_name", "firstname", "given name"),
                      "last": _pick(r, "last name", "last_name", "lastname", "surname", "family name")}
    return list(out.values())


def roster_update(cfg, new=None, replace=False, remove=None, clear=False):
    with locked():
        roster = [] if (replace or clear) else load_roster(cfg)
        merged = {r["email"]: r for r in roster}
        for r in new or []:
            merged[r["email"]] = r
        if remove:
            merged.pop(norm_email(remove), None)
        roster = list(merged.values())
        _write(ROSTER, roster)
        return roster


def stats(cfg):
    st = load_state()
    roster = load_roster(cfg)
    reg = {r["email"] for r in roster}
    taken = {a["email"] for a in st["pods"].values()}
    ids = [p["id"] for p in cfg["pods"]]
    free = [p for p in ids if p not in st["pods"] and p not in st["disabled"]]
    return {
        "pods": len(ids), "assigned": len(st["pods"]), "free": len(free), "disabled": len(st["disabled"]),
        "registered": len(roster), "checked_in": len(reg & taken), "walk_ins": len(taken - reg) if roster else 0,
        "waiting": sorted((r for r in roster if r["email"] not in taken), key=lambda r: (r["last"], r["first"])),
    }


def export_csv():
    st = load_state()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["pod", "email", "first", "last", "time"])
    for pid, x in sorted(st["pods"].items()):
        w.writerow([pid, x["email"], x["first"], x["last"], x["time"]])
    return buf.getvalue()



# ---------------------------------------------------------------- pod health + power-on

_health_lock = threading.Lock()
_power_proc = None


def _probe(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=3):
            return True
    except OSError:
        return False


def health_check(cfg):
    """TCP-probe every pod's forwarded ports (FS GUI + device GUIs) from the portal VM."""
    with _health_lock:
        svcs = cfg.get("all_services") or cfg["services"]
        with ThreadPoolExecutor(max_workers=64) as ex:
            jobs = {(p["id"], s["name"]): ex.submit(_probe, p["ip"], s["clone_port"])
                    for p in cfg["pods"] for s in svcs}
        res = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "services": [{"name": s["name"], "label": s["label"]} for s in svcs],
               "pods": {pid: {} for pid in [p["id"] for p in cfg["pods"]]}}
        for (pid, name), fut in jobs.items():
            res["pods"][pid][name] = fut.result()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _write(HEALTH, res)
        return res


def health_loop():
    while True:
        try:
            health_check(load_config())
        except Exception as e:  # never kill the portal over a probe
            sys.stderr.write(f"health check failed: {e}\n")
        time.sleep(HEALTH_EVERY)


def power_on(cfg, pods=None, action="install"):
    """Run pods_power_on.sh in the background; output goes to POWER_LOG."""
    if not cfg.get("fs_password"):
        return "set fs_admin_password in terraform.tfvars (or run pods-power-on on the VM with FS_PASS)"
    global _power_proc
    if _power_proc is not None and _power_proc.poll() is None:
        return "a power-on run is still going; check the log below"
    env = dict(os.environ, FS_PASS=cfg["fs_password"], ACTION=action)
    args = ["bash", str(APP_DIR / "pods_power_on.sh"), str(cfg.get("fabric_id", 1))] + (pods or [])
    with open(POWER_LOG, "w") as log:
        log.write(f"{time.strftime('%H:%M:%S')} runtime fabric {action} {cfg.get('fabric_id', 1)} "
                  f"on {' '.join(pods) if pods else 'all pods'}\n")
        log.flush()
        _power_proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    return f"{action} started on {' '.join(pods) if pods else 'all pods'}: FS queues it; the nested VMs take ~10 min"

# ---------------------------------------------------------------- admin page

class AdminHandler(Handler):
    server_version = "adc-portal-admin"

    def _auth(self):
        pw = load_config().get("admin_password") or ""
        h = self.headers.get("Authorization", "")
        if pw and h.startswith("Basic "):
            try:
                user, _, given = base64.b64decode(h[6:]).decode().partition(":")
            except Exception:
                given = ""
            if hmac.compare_digest(given.encode(), pw.encode()):
                return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="adc-portal admin"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._auth():
            return
        cfg = load_config()
        u = urllib.parse.urlparse(self.path)
        if u.path == "/export.csv":
            return self._send(200, export_csv(), "text/csv; charset=utf-8",
                              {"Content-Disposition": "attachment; filename=checkins.csv"})
        if u.path == "/api/stats":
            return self._send(200, json.dumps(stats(cfg)), "application/json")
        if u.path != "/":
            return self._send(404, "not found", "text/plain")
        msg = urllib.parse.parse_qs(u.query).get("msg", [""])[0]
        self._send(200, admin_page(cfg, msg))

    def do_POST(self):
        if not self._auth():
            return
        cfg = load_config()
        path = urllib.parse.urlparse(self.path).path
        length = min(int(self.headers.get("Content-Length") or 0), 5_000_000)
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            msg_ = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body,
                                            policy=email.policy.HTTP)
            f, files = {}, {}
            for part in msg_.iter_parts():
                name = part.get_param("name", header="content-disposition")
                data = part.get_payload(decode=True) or b""
                if part.get_filename():
                    files[name] = data
                else:
                    f[name] = data.decode(errors="replace")
        else:
            f = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode(errors="replace")).items()}
            files = {}
        try:
            out = admin_action(cfg, path, f, files)
        except SystemExit as e:
            out = str(e)
        except Exception as e:  # keep the page alive on bad input
            out = f"error: {e}"
        self._redirect("/?msg=" + urllib.parse.quote(out))


def admin_action(cfg, path, f, files):
    if path == "/roster/upload":
        data = files.get("file") or b""
        rows = roster_from_csv(data.decode("utf-8", errors="replace"))
        if not rows:
            return "no rows with an Email column found"
        roster = roster_update(cfg, rows, replace=f.get("mode") == "replace")
        return f"loaded {len(rows)} from the CSV, roster now {len(roster)} (only email + names kept)"
    if path == "/roster/add":
        e = norm_email(f.get("email"))
        if "@" not in e:
            return "need an email"
        roster_update(cfg, [{"email": e, "first": f.get("first", "").strip(), "last": f.get("last", "").strip()}])
        return f"added {e}"
    if path == "/roster/remove":
        roster_update(cfg, remove=f.get("email"))
        return f"removed {f.get('email')}"
    if path == "/roster/clear":
        roster_update(cfg, clear=True)
        return "roster cleared: check-in is open (passphrase + name + email)"
    if path == "/release":
        return release(f.get("target", ""))
    if path == "/assign":
        e = norm_email(f.get("email"))
        rec = next((r for r in load_roster(cfg) if r["email"] == e), {})
        pod = assign(cfg, e, f.get("first") or rec.get("first", ""), f.get("last") or rec.get("last", ""),
                     f.get("pod") or None)
        return f"{e} -> {pod}" if pod else "no free pod"
    if path == "/health":
        r = health_check(cfg)
        bad = sum(1 for v in r["pods"].values() for ok in v.values() if not ok)
        return f"health checked: {bad} port(s) down" if bad else "health checked: all ports answering"
    if path == "/power-on":
        pod = f.get("pod")
        return power_on(cfg, [pod] if pod else None, f.get("action") or "install")
    if path in ("/disable", "/enable"):
        return set_disabled(f.get("pod", ""), path == "/disable")
    return "unknown action"


ADMIN_CSS = CSS + """
main{max-width:1100px} .stats{display:flex;gap:14px;flex-wrap:wrap}
.stat{background:#fff;border-radius:10px;padding:14px 20px;box-shadow:0 1px 3px rgba(0,0,0,.08);min-width:120px}
.stat b{display:block;font-size:28px;color:#da291c} .stat span{font-size:13px;color:#666}
form.inline{display:inline;margin:0} form.inline button{margin:0;padding:4px 10px;font-size:12px}
.row{display:flex;gap:8px;align-items:end;flex-wrap:wrap} .row input{width:auto} .row button{margin-top:0}
.msg{background:#e8f4ea;color:#1e5b2a;padding:10px 14px;border-radius:6px;margin-bottom:16px}
.off{color:#999}
.ok{background:#e6f4ea;color:#1e7b34;text-align:center} .bad{background:#fdecea;color:#b3261e;text-align:center;font-weight:600}
pre{background:#1d1d1f;color:#e8e8e8;padding:12px;border-radius:6px;font-size:12px;overflow:auto;max-height:260px}
"""


def _btn(action, label, **fields):
    hidden = "".join(f"<input type='hidden' name='{k}' value='{html.escape(str(v))}'>" for k, v in fields.items())
    return f"<form class='inline' method='post' action='{action}'>{hidden}<button>{label}</button></form>"



def health_html(cfg):
    h = _read(HEALTH, None)
    pw = bool(cfg.get("fs_password"))
    buttons = _btn("/health", "Check now") + " " + (
        _btn("/power-on", "Power on all pods") if pw else
        "<span class='muted'>(power-on buttons need fs_admin_password in tfvars)</span>")
    if not h:
        return f"<div class='card'><h2>Pod health</h2><p class='muted'>first check running…</p>{buttons}</div>"
    head = "".join(f"<th>{html.escape(s['label'])}</th>" for s in h["services"])
    rows, bad_pods = [], 0
    for pid, res in h["pods"].items():
        cells = "".join(f"<td class='{'ok' if res.get(s['name']) else 'bad'}'>{'up' if res.get(s['name']) else 'DOWN'}</td>"
                        for s in h["services"])
        down = not all(res.values())
        bad_pods += down
        act = _btn("/power-on", "power on", pod=pid) if (pw and down) else ""
        rows.append(f"<tr><td><b>{pid}</b></td>{cells}<td>{act}</td></tr>")
    log = ""
    if POWER_LOG.exists():
        tail = POWER_LOG.read_text(errors="replace").splitlines()[-25:]
        log = f"<h2 style='margin-top:18px'>Last power-on run</h2><pre>{html.escape(chr(10).join(tail))}</pre>"
    summary = (f"<b style='color:#b3261e'>{bad_pods} pod(s) with ports down</b>" if bad_pods
               else "<b style='color:#1e7b34'>all pods answering</b>")
    return (f"<div class='card'><h2>Pod health</h2><p>{summary} <span class='muted'>· checked {h['time'][11:]}"
            f" (every {HEALTH_EVERY} s, TCP to each forwarded port from the portal)</span></p>"
            f"<table><tr><th>Pod</th>{head}<th></th></tr>{''.join(rows)}</table>"
            f"<p>{buttons}</p><p class='muted'>DOWN right after a build = nested VMs not started yet: power on, wait ~10 min.</p>{log}</div>")

def admin_page(cfg, msg=""):
    st = load_state()
    s = stats(cfg)
    host = cfg["public_host"]
    rows = []
    for p in cfg["pods"]:
        pid = p["id"]
        a = st["pods"].get(pid)
        dis = pid in st["disabled"]
        fs = f"https://{host}:{p['port_start'] + cfg.get('fs_gui_suffix', 0)}/"
        who = (f"{html.escape(a['first'])} {html.escape(a['last'])}</td><td>{html.escape(a['email'])}</td>"
               f"<td>{html.escape(a['time'][11:16])}") if a else "<span class='off'>free</span></td><td></td><td>"
        acts = (_btn("/release", "release", target=pid) if a else "") + (
            _btn("/enable", "enable", pod=pid) if dis else _btn("/disable", "disable", pod=pid))
        rows.append(f"<tr{' class=off' if dis else ''}><td><b>{pid}</b>{' (disabled)' if dis else ''}</td>"
                    f"<td>{who}</td><td><a href='{fs}' target='_blank'>FS</a></td><td>{acts}</td></tr>")
    waiting = "".join(f"<tr><td>{html.escape(r['first'])} {html.escape(r['last'])}</td><td>{html.escape(r['email'])}</td>"
                      f"<td>{_btn('/roster/remove', 'remove', email=r['email'])}</td></tr>" for r in s["waiting"])
    mode = "roster (registered emails only)" if s["registered"] else "open check-in (no roster)"
    note = f"<div class='msg'>{html.escape(msg)}</div>" if msg else ""
    health_card = health_html(cfg)
    title = html.escape(cfg.get("class_name", "Lab")) + " — admin"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>
<meta http-equiv='refresh' content='15'><style>{ADMIN_CSS}</style></head><body><header>{title}</header><main>
{note}
<div class='stats'>
<div class='stat'><b>{s['assigned']}/{s['pods']}</b><span>pods in use</span></div>
<div class='stat'><b>{s['free']}</b><span>pods free</span></div>
<div class='stat'><b>{s['checked_in']}/{s['registered']}</b><span>registered checked in</span></div>
<div class='stat'><b>{s['walk_ins']}</b><span>not on roster</span></div>
<div class='stat'><b>{s['disabled']}</b><span>pods disabled</span></div>
</div>
<p class='muted'>Check-in mode: {mode}. Student portal: <a href='http://{host}/' target='_blank'>http://{host}/</a> · refreshes every 15 s</p>

{health_card}

<div class='card'><h2>Pods</h2><table><tr><th>Pod</th><th>Name</th><th>Email</th><th>Time</th><th></th><th></th></tr>{''.join(rows)}</table>
<p><a href='/export.csv'>Download check-ins (CSV)</a></p></div>

<div class='card'><h2>Assign / move a student</h2>
<form method='post' action='/assign' class='row'>
<input name='email' placeholder='email' required><input name='pod' placeholder='pod (s05), empty = next free' size='24'>
<input name='first' placeholder='first (if not on roster)'><input name='last' placeholder='last'>
<button>Assign</button></form>
<p class='muted'>Their previous pod is freed. A reused pod keeps the previous student's device config.</p></div>

<div class='card'><h2>Roster</h2>
<form method='post' action='/roster/upload' enctype='multipart/form-data' class='row'>
<input type='file' name='file' accept='.csv,text/csv' required>
<label style='margin:0'><input type='radio' name='mode' value='merge' checked style='width:auto'> merge</label>
<label style='margin:0'><input type='radio' name='mode' value='replace' style='width:auto'> replace</label>
<button>Upload CSV</button></form>
<p class='muted'>Registration export with Email / First Name / Last Name columns; everything else in the file is dropped.</p>
<form method='post' action='/roster/add' class='row'>
<input name='email' placeholder='walk-in email' required><input name='first' placeholder='first'><input name='last' placeholder='last'>
<button>Add</button></form>
<h2 style='margin-top:22px'>Registered, not checked in yet ({len(s['waiting'])})</h2>
<table>{waiting or "<tr><td class='muted'>nobody</td></tr>"}</table>
{_btn('/roster/clear', 'Clear roster (open check-in)') if s['registered'] else ''}
</div>
</main></body></html>"""

# ---------------------------------------------------------------- CLI

def cmd_list(cfg, _a):
    st = load_state()
    for p in cfg["pods"]:
        pid = p["id"]
        a = st["pods"].get(pid)
        flag = " [disabled]" if pid in st["disabled"] else ""
        who = f"{a['first']} {a['last']} <{a['email']}>  {a['time']}" if a else "-"
        print(f"{pid}  {p['ip']:<14} ports {p['port_start']}-{p['port_start'] + cfg['port_block'] - 1}  {who}{flag}")


def cmd_release(cfg, a):
    print(release(a.target))


def cmd_assign(cfg, a):
    email = norm_email(a.email)
    rec = next((r for r in load_roster(cfg) if r["email"] == email), {})
    first = a.first or rec.get("first", "")
    last = a.last or rec.get("last", "")
    print(f"{email} -> {assign(cfg, email, first, last, a.pod)}")


def cmd_disable(cfg, a, on=True):
    print(set_disabled(a.pod, on))


def cmd_export(cfg, _a):
    sys.stdout.write(export_csv())


def cmd_roster(cfg, a):
    with locked():
        roster = load_roster(cfg)
        if a.action == "import":
            with open(a.args[0], encoding="utf-8-sig") as fh:
                new = roster_from_csv(fh.read())
            merged = {r["email"]: r for r in roster}
            merged.update({r["email"]: r for r in new})
            roster = list(merged.values())
            print(f"imported {len(new)}, roster now {len(roster)}")
        elif a.action == "add":
            if len(a.args) < 3:
                raise SystemExit("roster add EMAIL FIRST LAST")
            e = norm_email(a.args[0])
            roster = [r for r in roster if r["email"] != e] + [{"email": e, "first": a.args[1], "last": " ".join(a.args[2:])}]
            print(f"added {e}")
        elif a.action == "remove":
            e = norm_email(a.args[0])
            roster = [r for r in roster if r["email"] != e]
            print(f"removed {e}")
        elif a.action == "clear":
            roster = []
            print("roster cleared: open check-in")
        else:
            for r in sorted(roster, key=lambda r: r["email"]):
                print(f"{r['email']:<40} {r['first']} {r['last']}")
            print(f"{len(roster)} registered")
            return
        _write(ROSTER, roster)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8080)
    s.add_argument("--admin-port", type=int, default=8005)
    sub.add_parser("list")
    s = sub.add_parser("release"); s.add_argument("target")
    s = sub.add_parser("assign"); s.add_argument("email"); s.add_argument("pod")
    s.add_argument("--first", default=""); s.add_argument("--last", default="")
    s = sub.add_parser("disable"); s.add_argument("pod")
    s = sub.add_parser("enable"); s.add_argument("pod")
    sub.add_parser("export")
    s = sub.add_parser("roster"); s.add_argument("action", choices=["import", "add", "remove", "list", "clear"])
    s.add_argument("args", nargs="*")
    a = ap.parse_args()

    cfg = load_config()
    if a.cmd == "serve":
        secret_key()
        load_roster(cfg)
        print(f"adc-portal on :{a.port}, {len(cfg['pods'])} pods", flush=True)
        if cfg.get("admin_password"):
            adm = ThreadingHTTPServer(("0.0.0.0", a.admin_port), AdminHandler)
            threading.Thread(target=adm.serve_forever, daemon=True).start()
            threading.Thread(target=health_loop, daemon=True).start()
            print(f"admin page on :{a.admin_port}", flush=True)
        ThreadingHTTPServer(("0.0.0.0", a.port), Handler).serve_forever()
    elif a.cmd == "list":
        cmd_list(cfg, a)
    elif a.cmd == "release":
        cmd_release(cfg, a)
    elif a.cmd == "assign":
        cmd_assign(cfg, a)
    elif a.cmd == "disable":
        cmd_disable(cfg, a, True)
    elif a.cmd == "enable":
        cmd_disable(cfg, a, False)
    elif a.cmd == "export":
        cmd_export(cfg, a)
    elif a.cmd == "roster":
        cmd_roster(cfg, a)


if __name__ == "__main__":
    main()
