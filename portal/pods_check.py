#!/usr/bin/env python3
"""Check every pod's ports from the portal VM (TCP connect to pod IP : clone port).

  pods-check            all pods, all services
  pods-check s01 s05    some pods

A closed port usually means the nested VMs aren't running yet (pods-power-on),
or the FS port redirection is missing in the class image.
"""
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

cfg = json.load(open("/opt/adc-portal/config.json"))
want = set(sys.argv[1:])
pods = [p for p in cfg["pods"] if not want or p["id"] in want]
services = cfg["all_services"]


def probe(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=3):
            return True
    except OSError:
        return False


jobs = {}
with ThreadPoolExecutor(max_workers=64) as ex:
    for p in pods:
        for s in services:
            jobs[(p["id"], s["name"])] = ex.submit(probe, p["ip"], s["clone_port"])

names = [s["name"] for s in services]
w = max(len(n) for n in names)
print("pod  " + "  ".join(n.ljust(w) for n in names))
bad = 0
for p in pods:
    cells = []
    for n in names:
        ok = jobs[(p["id"], n)].result()
        bad += not ok
        cells.append(("ok" if ok else "DOWN").ljust(w))
    print(f"{p['id']}  " + "  ".join(cells))
print(f"\n{bad} port(s) down" if bad else "\nall ports answering")
sys.exit(1 if bad else 0)
