#!/usr/bin/env python3
"""Strict A/B benchmark via a ComfyUI panel's HTTP proxy (no SSH required).

Usage:
    python proxy_ab_bench.py <panel_url> <base.json> <acc.json> <out.json> [order]

Runs 3 warmups of A, then A->B->A->B (or any order string). Records wall time,
prompt_id, status and peak VRAM sampled from GET /api/comfy/proxy/system_stats
(devices[i].vram_total - vram_free) once per second.

The two workflow files must already share the same prompt / seed / resolution /
frame count. This measures the WHOLE workflow package, not a single switch.
"""
import json
import sys
import threading
import time
import uuid

import requests

requests.packages.urllib3.disable_warnings()
POLL = 1.0
CLIENT_ID = str(uuid.uuid4())


def get(session, url, timeout=20):
    r = session.get(url, timeout=timeout, verify=False)
    r.raise_for_status()
    return r.json()


def sample_vram(session, stats_url, stop, peaks):
    while not stop.is_set():
        try:
            d = get(session, stats_url, timeout=8)
            for dev in d.get("devices", []):
                used = dev.get("vram_total", 0) - dev.get("vram_free", 0)
                peaks["mib"] = max(peaks.get("mib", 0), round(used / 1048576))
        except Exception:
            pass
        stop.wait(POLL)


def run_one(session, base, wf, stats_url, label, round_no, timeout=1200):
    peaks = {"mib": 0}
    stop = threading.Event()
    th = threading.Thread(target=sample_vram, args=(session, stats_url, stop, peaks), daemon=True)
    th.start()
    t0 = time.time()
    r = session.post(base + "/api/comfy/proxy/prompt",
                     json={"prompt": wf, "client_id": CLIENT_ID}, timeout=30)
    r.raise_for_status()
    resp = r.json()
    if resp.get("error"):
        stop.set()
        return {"label": label, "round": round_no, "status": "submit_error", "error": resp["error"]}
    pid = resp["prompt_id"]
    status = "timeout"
    while time.time() - t0 < timeout:
        hist = get(session, base + "/api/comfy/proxy/history?prompt_id=" + pid)
        if pid in hist:
            st = hist[pid].get("status", {}).get("status_str", "")
            if st in ("success", "error"):
                status = st
                break
        time.sleep(POLL)
    wall = time.time() - t0
    stop.set()
    th.join(timeout=3)
    return {"label": label, "round": round_no, "prompt_id": pid, "status": status,
            "wall_s": round(wall, 2), "peak_vram_mib": peaks["mib"]}


def main():
    if len(sys.argv) < 5:
        print("usage: proxy_ab_bench.py <panel_url> <a.json> <b.json> <out.json> [order]",
              file=sys.stderr)
        return 2
    base = sys.argv[1].rstrip("/")
    a = json.load(open(sys.argv[2], encoding="utf-8"))
    b = json.load(open(sys.argv[3], encoding="utf-8"))
    order = sys.argv[5] if len(sys.argv) > 5 else "ABAB"
    stats = base + "/api/comfy/proxy/system_stats"
    s = requests.Session()
    results = []
    for i in range(3):
        rec = run_one(s, base, a, stats, "warmup-A", i)
        print("warmup", i, rec["status"], rec.get("wall_s"), flush=True)
        results.append(rec)
    for i, label in enumerate(order):
        rec = run_one(s, base, {"A": a, "B": b}[label], stats, label, i)
        print("measured", i, label, rec["status"], rec.get("wall_s"),
              "peak", rec.get("peak_vram_mib"), flush=True)
        results.append(rec)
    json.dump(results, open(sys.argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("wrote", sys.argv[4], "runs:", len(results), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
