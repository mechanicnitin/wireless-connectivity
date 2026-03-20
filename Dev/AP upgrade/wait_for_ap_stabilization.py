#!/usr/bin/env python3
"""
Wait for AP stabilization after upgrade trigger.

This script polls AP status and waits for:
1. All APs are connected (or in target scope)
2. All APs are on target firmware version
3. No APs are in upgrade state

Polling strategy:
- Poll every ~2 minutes (configurable)
- Max timeout ~30 minutes (configurable)
- Fail fast if any critical error occurs
- Return proper exit codes for CI/CD integration

Usage:
  python wait_for_ap_stabilization.py -c site_list.csv --target-version 0.14.xxxx
  python wait_for_ap_stabilization.py -x site_list.xlsx --target-version 0.14.xxxx
"""

import sys
import os
import csv
import getopt
import time
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from openpyxl import load_workbook


class console:
    @staticmethod
    def info(msg: str):
        print(f"[INFO] {msg}")

    @staticmethod
    def warning(msg: str):
        print(f"[WARN] {msg}", file=sys.stderr)

    @staticmethod
    def error(msg: str):
        print(f"[ERROR] {msg}", file=sys.stderr)


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


def read_csv_rows(path: str) -> List[Dict[str, str]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV is empty")
        
        for row in reader:
            site_name = (row.get("site_name") or "").strip()
            if site_name:
                rows.append({"site_name": site_name})
    return rows


def read_excel_rows(path: str, sheet_name: Optional[str] = None) -> List[Dict[str, str]]:
    wb = load_workbook(filename=path, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    
    if not rows:
        raise ValueError("Excel sheet is empty")

    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    idx = {h.lower(): i for i, h in enumerate(headers) if h}

    if "site_name" not in idx:
        raise ValueError(f"Missing 'site_name' column. Found: {headers}")

    out = []
    for r in rows[1:]:
        if r is None:
            continue
        site_name = r[idx["site_name"]]
        if site_name and str(site_name).strip():
            out.append({"site_name": str(site_name).strip()})
    return out


class MistClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        proxies: Optional[Dict[str, str]] = None,
        no_proxy: Optional[str] = None,
        max_retries: int = 5,
        timeout: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout

        self.session = requests.Session()
        self.session.trust_env = False

        self.session.headers.update(
            {
                "Authorization": f"Token {token}",
                "Accept": "application/json",
            }
        )

        if proxies:
            self.session.proxies.update(proxies)

        if no_proxy:
            os.environ["NO_PROXY"] = no_proxy
            os.environ["no_proxy"] = no_proxy

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self._url(path)
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)

                if resp.status_code in (429, 500, 502, 503, 504):
                    if attempt < self.max_retries:
                        time.sleep(min(30, 2 ** attempt))
                        continue

                if not resp.ok:
                    raise RuntimeError(f"GET {path} failed ({resp.status_code}): {resp.text}")

                return resp.json() if resp.text.strip() else None

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                if attempt < self.max_retries:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                raise
            except Exception as e:
                raise

        raise RuntimeError(f"GET {path} failed unexpectedly")


def build_site_map(client: MistClient, org_id: str) -> Dict[str, str]:
    sites = client.get(f"/orgs/{org_id}/sites", params={"limit": 1000, "page": 1})
    if not isinstance(sites, list):
        raise RuntimeError("Unexpected response when listing sites")
    return {str(s["name"]).strip().lower(): s["id"] for s in sites if s.get("name") and s.get("id")}


def get_site_devices(client: MistClient, site_id: str) -> List[Dict[str, Any]]:
    data = client.get(f"/sites/{site_id}/stats/devices")
    return data if isinstance(data, list) else []


def is_upgrade_in_progress(state: Optional[str]) -> bool:
    if not state:
        return False
    s = str(state).lower()
    return any(x in s for x in ("download", "upgrading", "install", "reboot"))


def evaluate_site_readiness(
    devices: List[Dict[str, Any]],
    target_version: str,
    allowed_models: List[str],
) -> Tuple[bool, int, int, int, str]:
    """
    Returns: (is_ready, connected, correct_version, upgrading, issue_summary)
    is_ready: True if all AP conditions met
    """
    ap_models = {m.strip().upper() for m in allowed_models}
    
    # Filter to allowed models
    eligible_aps = [d for d in devices if (d.get("model") or "").strip().upper() in ap_models]
    
    if not eligible_aps:
        return False, 0, 0, 0, "No eligible APs found"

    connected = 0
    correct_version = 0
    upgrading = 0
    issues = []

    for ap in eligible_aps:
        is_connected = (ap.get("status") or "").strip().lower() == "connected"
        version = (ap.get("version") or ap.get("firmware_version") or "UNKNOWN").strip()
        upgrade_state = ap.get("firmware_status") or ap.get("upgrade_status") or None
        is_upgrading = is_upgrade_in_progress(upgrade_state)

        if is_connected:
            connected += 1
        else:
            issues.append(f"{ap.get('name', 'unknown')}: disconnected")

        if version == target_version:
            correct_version += 1
        else:
            issues.append(f"{ap.get('name', 'unknown')}: version {version} (expected {target_version})")

        if is_upgrading:
            upgrading += 1
            issues.append(f"{ap.get('name', 'unknown')}: upgrading ({upgrade_state})")

    total = len(eligible_aps)
    is_ready = (connected == total) and (correct_version == total) and (upgrading == 0)
    
    issue_summary = "; ".join(issues[:5])  # Limit to first 5 issues
    if len(issues) > 5:
        issue_summary += f"; +{len(issues)-5} more"

    return is_ready, connected, correct_version, upgrading, issue_summary


def usage():
    print(
        """
Wait for AP Stabilization After Upgrade

Required:
  -x, --excel=        Excel file (.xlsx) with site_name column
  -c, --csv=          CSV file (.csv) with site_name column
  --target-version=   Target firmware version (e.g., 0.14.xxxx)

Optional:
  -e, --env=          Env file path (default: ./.env)
  --poll-interval=    Seconds between polls (default: 120)
  --max-wait=         Maximum seconds to wait (default: 1800 = 30 min)

Example:
  python wait_for_ap_stabilization.py -c site_list.csv --target-version 0.14.xxxx
  python wait_for_ap_stabilization.py -x site_list.xlsx --target-version 0.14.xxxx --max-wait 1800
"""
    )
    sys.exit(1)


if __name__ == "__main__":
    input_file: Optional[str] = None
    input_format = "xlsx"
    env_file = ".env"
    target_version: Optional[str] = None
    sheet_name: Optional[str] = None
    poll_interval = 120  # 2 minutes
    max_wait = 1800  # 30 minutes

    try:
        opts, _ = getopt.getopt(
            sys.argv[1:],
            "hx:c:e:",
            ["help", "excel=", "csv=", "env=", "sheet=", "target-version=", "poll-interval=", "max-wait="],
        )
    except getopt.GetoptError as err:
        console.error(str(err))
        usage()

    for o, a in opts:
        if o in ("-h", "--help"):
            usage()
        elif o in ("-x", "--excel"):
            input_file = a
            input_format = "xlsx"
        elif o in ("-c", "--csv"):
            input_file = a
            input_format = "csv"
        elif o in ("-e", "--env"):
            env_file = a
        elif o == "--sheet":
            sheet_name = a
        elif o == "--target-version":
            target_version = a
        elif o == "--poll-interval":
            try:
                poll_interval = int(a)
            except ValueError:
                console.error("--poll-interval must be an integer")
                sys.exit(1)
        elif o == "--max-wait":
            try:
                max_wait = int(a)
            except ValueError:
                console.error("--max-wait must be an integer")
                sys.exit(1)

    if not input_file or not target_version:
        console.error("Both --input file and --target-version are required")
        usage()

    # Load environment
    try:
        env = load_env_file(env_file)
    except Exception as e:
        console.error(f"Failed to load env: {e}")
        sys.exit(1)

    base_url = env.get("MIST_BASE_URL")
    org_id = env.get("MIST_ORG_ID")
    token = env.get("MIST_ACCESS_TOKEN")

    if not base_url or not org_id or not token:
        console.error("Missing MIST_BASE_URL, MIST_ORG_ID, or MIST_ACCESS_TOKEN in .env")
        sys.exit(1)

    # Read input file
    try:
        if input_format == "csv":
            rows = read_csv_rows(input_file)
        else:
            rows = read_excel_rows(input_file, sheet_name)
    except Exception as e:
        console.error(f"Failed to read input file: {e}")
        sys.exit(1)

    site_names = [r["site_name"] for r in rows]
    console.info(f"Loaded {len(site_names)} site(s) from {input_file}")

    # Setup Mist client
    proxy = env.get("ALL_PROXY") or env.get("HTTPS_PROXY") or env.get("HTTP_PROXY")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    no_proxy = env.get("NO_PROXY")

    client = MistClient(
        base_url=base_url,
        token=token,
        proxies=proxies,
        no_proxy=no_proxy,
    )

    # Build site map
    try:
        site_map = build_site_map(client, org_id)
    except Exception as e:
        console.error(f"Failed to load sites: {e}")
        sys.exit(1)

    # Resolve site IDs
    site_ids: Dict[str, str] = {}
    for name in site_names:
        site_id = site_map.get(name.lower())
        if not site_id:
            console.warning(f"Site not found: {name}")
        else:
            site_ids[name] = site_id

    if not site_ids:
        console.error("No valid sites found")
        sys.exit(1)

    allowed_models = [(env.get("ALLOWED_MODELS") or "AP45").strip()]
    allowed_models = [m.strip() for m in allowed_models[0].split(",") if m.strip()]

    # Polling loop
    console.info(f"Starting stabilization poll (interval={poll_interval}s, max_wait={max_wait}s)")
    console.info(f"Target version: {target_version}")
    console.info(f"Watching {len(site_ids)} site(s)")

    start_time = time.time()
    poll_count = 0
    all_ready = False

    while time.time() - start_time < max_wait:
        poll_count += 1
        elapsed = int(time.time() - start_time)
        console.info(f"\n--- Poll #{poll_count} (elapsed: {elapsed}s) ---")

        all_ready = True
        failed_sites = []

        for site_name, site_id in site_ids.items():
            try:
                devices = get_site_devices(client, site_id)
                is_ready, connected, correct_version, upgrading, issue_summary = evaluate_site_readiness(
                    devices, target_version, allowed_models
                )

                total_aps = len([d for d in devices if (d.get("model") or "").strip().upper() in {m.upper() for m in allowed_models}])

                if is_ready:
                    console.info(f"✓ {site_name}: READY ({total_aps} APs, all connected, all v{target_version}, 0 upgrading)")
                else:
                    all_ready = False
                    console.warning(
                        f"✗ {site_name}: NOT READY ({connected}/{total_aps} connected, "
                        f"{correct_version}/{total_aps} on target, {upgrading}/{total_aps} upgrading)"
                    )
                    if issue_summary:
                        console.warning(f"  Issues: {issue_summary}")

            except Exception as e:
                all_ready = False
                console.error(f"✗ {site_name}: ERROR - {e}")
                failed_sites.append(site_name)

        if all_ready:
            console.info("\n✓✓✓ All sites stabilized! ✓✓✓")
            sys.exit(0)

        if failed_sites:
            console.error(f"\nSites with API errors: {', '.join(failed_sites)}")
            console.error("Continuing polls despite errors...")

        remaining = max_wait - (time.time() - start_time)
        if remaining > poll_interval:
            console.info(f"Waiting {poll_interval}s before next poll ({int(remaining)}s remaining)...")
            time.sleep(poll_interval)
        else:
            break

    # Timeout reached
    console.error(f"\n✗✗✗ Timeout reached after {elapsed}s ✗✗✗")
    console.error("Not all APs have stabilized. Check logs and Mist dashboard for details.")
    sys.exit(1)
