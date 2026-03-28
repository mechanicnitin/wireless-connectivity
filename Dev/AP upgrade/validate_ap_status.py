#!/usr/bin/env python3
"""
Validate AP firmware status per site (pre/post-check), driven by Excel/CSV.

PRE-VALIDATION: Captures baseline - only checks connectivity, NOT version.
  AP classification per device:
    READY_FOR_UPGRADE  - connected, version != target
    ALREADY_COMPLIANT  - connected, version == target
    DISCONNECTED       - offline

  Site-level:
    scope=all      → FLAGGED if ANY AP is disconnected
    scope=connected → disconnected APs are informational only; site is
                      BASELINE_OK when at least one AP is connected

POST-VALIDATION: Checks both connectivity AND target version.
  Site is FLAGGED if ANY AP (that is in scope) fails a check.
  scope=all      → disconnected APs count as failures
  scope=connected → only connected APs are evaluated

Inputs:
- Excel/CSV columns required: site_name, target_version, scope
- .env required:
    MIST_BASE_URL
    MIST_ORG_ID
    MIST_ACCESS_TOKEN

Exit codes:
- 0: All eligible sites OK (pre) or SUCCESS (post)
- 1: Any site flagged or all skipped

Usage:
  python validate_ap_status.py -c site_list.csv --tag pre
  python validate_ap_status.py -c site_list.csv --tag post
"""
#!/usr/bin/env python3

import sys
import os
import csv
import getopt
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from openpyxl import load_workbook

ENV_FILE_DEFAULT = ".env"

# ----------------------------
# ENV
# ----------------------------
def load_env_file(path: str) -> Dict[str, str]:
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        raise FileNotFoundError(f"Env file not found: {expanded}")

    env: Dict[str, str] = {}
    with open(expanded, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def normalize_scope(scope: str) -> str:
    s = (scope or "").strip().lower()
    if s in ("connected", "connected_only", "online"):
        return "connected"
    return "all"

def ok_label(tag: str) -> str:
    return "BASELINE_OK" if tag == "pre" else "SUCCESS"

# ----------------------------
# Mist Client
# ----------------------------
class MistClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        })

    def get(self, path: str):
        resp = self.session.get(f"{self.base_url}{path}", timeout=60)
        if not resp.ok:
            raise RuntimeError(f"GET failed: {resp.status_code} {resp.text}")
        return resp.json()

# ----------------------------
# Input Readers
# ----------------------------
def read_csv_rows(path: str) -> List[Dict[str, str]]:
    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "site_name": r["site_name"].strip(),
                "target_version": r["target_version"].strip(),
                "scope": normalize_scope(r["scope"]),
            })
    return rows

# ----------------------------
# Helpers
# ----------------------------
def device_name(d): return d.get("name", "unknown")
def device_model(d): return (d.get("model") or "").upper()
def device_status(d): return (d.get("status") or "").lower()
def device_version(d): return d.get("version") or "UNKNOWN"

def classify_ap_pre(d, target):
    if device_status(d) != "connected":
        return "DISCONNECTED"
    return "ALREADY_COMPLIANT" if device_version(d) == target else "READY_FOR_UPGRADE"

# ----------------------------
# Evaluation
# ----------------------------
def evaluate_site_pre(devices, scope, target):
    eligible, flagged = [], []

    for d in devices:
        cls = classify_ap_pre(d, target)
        if cls == "DISCONNECTED":
            flagged.append((d, ["STATUS=disconnected"]))
        else:
            eligible.append(d)

    if scope == "all" and flagged:
        return 0, [], flagged

    return len(eligible), eligible, flagged

def evaluate_site_post(devices, target, scope):
    eligible, flagged = [], []

    for d in devices:
        status = device_status(d)
        version = device_version(d)

        if scope == "connected" and status != "connected":
            continue

        issues = []
        if status != "connected":
            issues.append(f"STATUS={status}")
        elif version != target:
            issues.append(f"VERSION_MISMATCH({version})")

        if issues:
            flagged.append((d, issues))
        else:
            eligible.append(d)

    return len(eligible), eligible, flagged

# ----------------------------
# Main
# ----------------------------
def main():
    input_file = None
    tag = "pre"

    opts, _ = getopt.getopt(sys.argv[1:], "c:", ["tag="])
    for o, a in opts:
        if o == "-c":
            input_file = a
        elif o == "--tag":
            tag = a

    env = load_env_file(".env")
    client = MistClient(env["MIST_BASE_URL"], env["MIST_ACCESS_TOKEN"])
    org_id = env["MIST_ORG_ID"]

    rows = read_csv_rows(input_file)

    sites = client.get(f"/orgs/{org_id}/sites")
    site_map = {s["name"]: s["id"] for s in sites}

    ok = flagged = 0
    is_pre = tag == "pre"
    ok_word = ok_label(tag)

    for r in rows:
        name = r["site_name"]
        target = r["target_version"]
        scope = r["scope"]

        site_id = site_map.get(name)
        devices = client.get(f"/sites/{site_id}/stats/devices")

        if is_pre:
            eligible_count, eligible_devices, flagged_devices = evaluate_site_pre(devices, scope, target)
        else:
            eligible_count, eligible_devices, flagged_devices = evaluate_site_post(devices, target, scope)

        # ✅ FIXED LOGIC
        should_flag = False
        if is_pre:
            if scope == "all" and flagged_devices:
                should_flag = True
        else:
            if flagged_devices:
                should_flag = True

        if should_flag:
            print(f"FLAGGED | {name}")
            flagged += 1
        else:
            print(f"{ok_word} | {name}")
            ok += 1

    # ✅ EXIT FIX
    if flagged > 0:
        if tag == "post":
            sys.exit(1)
        else:
            sys.exit(0)

    sys.exit(0)

if __name__ == "__main__":
    main()
