#!/usr/bin/env python3
"""ADC lab check-in portal + admin CLI (Python stdlib only).

Students check in with the class passphrase and their registration email,
get the next free pod and see their ports, logins and the lab guide.

  adc-portal serve [--port 8080]          run the web portal (systemd does this)
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
import csv
import fcntl
import hashlib
import hmac
import html
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.parse
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
<div class='card'><h2>Your devices</h2><table>{''.join(rows)}</table></div>
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
    with locked():
        st = load_state()
        key = norm_email(a.target) if "@" in a.target else a.target
        pod = pod_of(st, key) if "@" in key else key
        if pod in st["pods"]:
            print(f"released {pod} ({st['pods'][pod]['email']})")
            del st["pods"][pod]
            _write(STATE, st)
        else:
            print("nothing to release")


def cmd_assign(cfg, a):
    email = norm_email(a.email)
    rec = next((r for r in load_roster(cfg) if r["email"] == email), {})
    first = a.first or rec.get("first", "")
    last = a.last or rec.get("last", "")
    print(f"{email} -> {assign(cfg, email, first, last, a.pod)}")


def cmd_disable(cfg, a, on=True):
    with locked():
        st = load_state()
        if on and a.pod not in st["disabled"]:
            st["disabled"].append(a.pod)
        if not on and a.pod in st["disabled"]:
            st["disabled"].remove(a.pod)
        _write(STATE, st)
    print(("disabled " if on else "enabled ") + a.pod)


def cmd_export(cfg, _a):
    st = load_state()
    w = csv.writer(sys.stdout)
    w.writerow(["pod", "email", "first", "last", "time"])
    for pid, x in sorted(st["pods"].items()):
        w.writerow([pid, x["email"], x["first"], x["last"], x["time"]])


def _pick(row, *names):
    low = {k.strip().lower(): v for k, v in row.items() if k}
    for n in names:
        if n in low and low[n]:
            return low[n].strip()
    return ""


def cmd_roster(cfg, a):
    with locked():
        roster = load_roster(cfg)
        if a.action == "import":
            with open(a.args[0], newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
            new = {}
            for r in rows:
                e = norm_email(_pick(r, "email", "email address"))
                if e:
                    new[e] = {"email": e, "first": _pick(r, "first name", "first_name", "firstname"),
                              "last": _pick(r, "last name", "last_name", "lastname")}
            merged = {r["email"]: r for r in roster}
            merged.update(new)
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
