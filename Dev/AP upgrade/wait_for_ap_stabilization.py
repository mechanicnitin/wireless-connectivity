#!/usr/bin/env python3
"""
Wait for AP stabilization - REDESIGNED for parallel checking.

This script works like manual post-validation checking:
1. Run post-validation for ALL sites at once (not sequentially)
2. Compare current state to pre-validation baseline
3. Show summary: ready sites, still upgrading, disconnected, offline_baseline
4. Only fail if an AP that WAS connected is permanently disconnected
5. Retry with configurable wait between polls

KEY DIFFERENCE FROM V1:
- V1: Polls each site sequentially, gets stuck if one is bad
- V2: Checks ALL sites at once, reports status, retries

Usage:
  python wait_for_ap_stabilization_v2.py -c site_list.csv --target-version 0.14.xxxx
"""

import sys
import os
import csv
import getopt
import time
import glob
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

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

    @staticmethod
    def success(msg: str):
        print(f"[SUCCESS] {msg}")


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
            scope = (row.get("scope") or "all").strip().lower()
            if site_name:
                rows.append({"site_name": site_name, "scope": scope})
    return rows


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


def parse_baseline_report() -> Dict[str, Dict[str, str]]:
    """
    Parse pre-validation report to get baseline AP states.
    
    Returns: Dict[site_name -> Dict[ap_name -> "baseline_state"]]
    Baseline states: "connected_ok", "connected_version_mismatch", "disconnected"
    """
    baseline = {}
    
    reports = sorted(glob.glob("upgrade_validation_pre_*.txt"), reverse=True)
    if not reports:
        console.warning("No pre-validation baseline report found.")
        return baseline
    
    report_path = reports[0]
    console.info(f"Reading baseline from: {report_path}")
    
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        current_site = None
        for line in lines:
            line_stripped = line.strip()
            
            # Match site header: "BASELINE_OK | Switch_POC | eligible=1 | target=0.14.xxxx | scope=connected"
            if " | " in line_stripped and "eligible=" in line_stripped:
                parts = line_stripped.split(" | ")
                if len(parts) >= 2:
                    current_site = parts[1].strip()
                    baseline.setdefault(current_site, {})
            
            # Match AP line: "  - cbnp48ge-01-jap02-45la [AP45] status=connected version=0.14.29967  OK"
            if line_stripped.startswith("  - ") and current_site:
                # Extract AP name (first token after "- ")
                ap_name = line_stripped[4:].split(" [")[0].strip()
                
                if "status=disconnected" in line_stripped:
                    baseline[current_site][ap_name] = "disconnected"
                elif "status=connected" in line_stripped:
                    if "OK" in line_stripped or "VERSION_MISMATCH" not in line_stripped:
                        baseline[current_site][ap_name] = "connected_ok"
                    else:
                        baseline[current_site][ap_name] = "connected_version_mismatch"
        
        console.info(f"Baseline parsed: {sum(len(v) for v in baseline.values())} APs tracked")
    
    except Exception as e:
        console.warning(f"Failed to parse baseline report: {e}")
    
    return baseline


def parse_current_validation_report(report_path: str) -> Dict[str, Dict[str, str]]:
    """
    Parse current validation report to get current AP states.
    
    Returns: Dict[site_name -> Dict[ap_name -> "current_state"]]
    """
    current = {}
    
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        current_site = None
        for line in lines:
            line_stripped = line.strip()
            
            # Match site header
            if " | " in line_stripped and "eligible=" in line_stripped:
                parts = line_stripped.split(" | ")
                if len(parts) >= 2:
                    current_site = parts[1].strip()
                    current.setdefault(current_site, {})
            
            # Match AP line
            if line_stripped.startswith("  - ") and current_site:
                ap_name = line_stripped[4:].split(" [")[0].strip()
                
                if "status=disconnected" in line_stripped:
                    current[current_site][ap_name] = "disconnected"
                elif "status=connected" in line_stripped:
                    if "OK" in line_stripped or "VERSION_MISMATCH" not in line_stripped:
                        current[current_site][ap_name] = "connected_ok"
                    else:
                        current[current_site][ap_name] = "connected_version_mismatch"
                elif "UPGRADE_IN_PROGRESS" in line_stripped:
                    current[current_site][ap_name] = "upgrading"
    
    except Exception as e:
        console.warning(f"Failed to parse current report: {e}")
    
    return current


def compare_baseline_to_current(
    baseline: Dict[str, Dict[str, str]],
    current: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    """
    Compare baseline to current state and return status summary.
    
    Returns:
    {
      "all_ready": bool,
      "sites": {
        site_name: {
          "status": "READY" | "UPGRADING" | "DISCONNECTED",
          "connected": int,
          "upgrading": int,
          "disconnected": int,
          "issues": [list of problems]
        }
      }
    }
    """
    summary = {
        "all_ready": True,
        "sites": {}
    }
    
    # Check each site in baseline
    for site_name, baseline_aps in baseline.items():
        if not baseline_aps:
            continue
        
        current_aps = current.get(site_name, {})
        
        connected = 0
        upgrading = 0
        disconnected = 0
        issues = []
        
        # For each AP that was in baseline
        for ap_name, baseline_state in baseline_aps.items():
            current_state = current_aps.get(ap_name, "unknown")
            
            if baseline_state == "disconnected":
                # This AP was offline pre-upgrade, skip it
                continue
            
            # This AP WAS connected before upgrade
            if baseline_state in ("connected_ok", "connected_version_mismatch"):
                if current_state == "connected_ok":
                    connected += 1
                elif current_state == "upgrading":
                    upgrading += 1
                    issues.append(f"{ap_name}: still upgrading")
                elif current_state == "connected_version_mismatch":
                    issues.append(f"{ap_name}: version mismatch")
                elif current_state == "disconnected":
                    disconnected += 1
                    issues.append(f"{ap_name}: DISCONNECTED (was connected pre-upgrade!)")
                else:
                    issues.append(f"{ap_name}: unknown state")
        
        total = len([b for b in baseline_aps.values() if b != "disconnected"])
        
        if total == 0:
            status = "SKIPPED"
        elif disconnected > 0:
            status = "DISCONNECTED"
            summary["all_ready"] = False
        elif upgrading > 0:
            status = "UPGRADING"
            summary["all_ready"] = False
        elif connected == total:
            status = "READY"
        else:
            status = "UNKNOWN"
            summary["all_ready"] = False
        
        summary["sites"][site_name] = {
            "status": status,
            "connected": connected,
            "upgrading": upgrading,
            "disconnected": disconnected,
            "total": total,
            "issues": issues[:3]  # Limit to 3 issues
        }
    
    return summary


def run_validation(client: MistClient, org_id: str, site_names_with_scope: Dict[str, str], 
                  target_version: str, allowed_models: List[str]) -> str:
    """
    Run validate_ap_status.py and return report path.
    """
    import subprocess
    
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"upgrade_validation_post_{stamp}.txt"
    
    # Create temp CSV for validation
    csv_path = f"temp_sites_{stamp}.csv"
    try:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("site_name,target_version,scope\n")
            for site_name, scope in site_names_with_scope.items():
                f.write(f"{site_name},{target_version},{scope}\n")
        
        # Run validation script
        result = subprocess.run(
            [
                sys.executable,
                "Dev/AP upgrade/validate_ap_status.py",
                "-c", csv_path,
                "--tag", "post",
                "--env", ".env"
            ],
            capture_output=True,
            text=True,
            timeout=300  # 5 min timeout
        )
        
        # Move report to expected location
        # The script creates upgrade_validation_post_<stamp>.txt
        import glob as glob_module
        reports = sorted(glob_module.glob("upgrade_validation_post_*.txt"), reverse=True)
        if reports:
            report_path = reports[0]
        
        return report_path
    
    except Exception as e:
        console.error(f"Failed to run validation: {e}")
        raise
    finally:
        # Clean up temp CSV
        if os.path.exists(csv_path):
            os.remove(csv_path)


def usage():
    print(
        """
Wait for AP Stabilization - V2 (Parallel Checking)

Required:
  -c, --csv=          CSV file (.csv) with site_name, scope columns
  --target-version=   Target firmware version (e.g., 0.14.xxxx)

Optional:
  -e, --env=          Env file path (default: ./.env)
  --poll-interval=    Seconds between polls (default: 120 = 2 min)
  --max-wait=         Maximum seconds to wait (default: 1800 = 30 min)

HOW IT WORKS:
1. Reads pre-validation baseline report
2. Runs post-validation for ALL sites at once
3. Compares current state to baseline
4. Reports: ready sites, upgrading, disconnected, offline_baseline
5. Only fails if AP that WAS connected is now disconnected
6. Retries with configurable wait

Example:
  python wait_for_ap_stabilization_v2.py -c site_list.csv --target-version 0.14.xxxx
  python wait_for_ap_stabilization_v2.py -c site_list.csv --target-version 0.14.xxxx --poll-interval 120 --max-wait 1800
"""
    )
    sys.exit(1)


if __name__ == "__main__":
    input_file: Optional[str] = None
    env_file = ".env"
    target_version: Optional[str] = None
    poll_interval = 120  # 2 minutes
    max_wait = 1800  # 30 minutes

    try:
        opts, _ = getopt.getopt(
            sys.argv[1:],
            "hc:e:",
            ["help", "csv=", "env=", "target-version=", "poll-interval=", "max-wait="],
        )
    except getopt.GetoptError as err:
        console.error(str(err))
        usage()

    for o, a in opts:
        if o in ("-h", "--help"):
            usage()
        elif o in ("-c", "--csv"):
            input_file = a
        elif o in ("-e", "--env"):
            env_file = a
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
        console.error("Both CSV file and --target-version are required")
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
        rows = read_csv_rows(input_file)
    except Exception as e:
        console.error(f"Failed to read input file: {e}")
        sys.exit(1)

    site_names_with_scope = {r["site_name"]: r.get("scope", "all") for r in rows}
    console.info(f"Loaded {len(site_names_with_scope)} site(s) from {input_file}")

    allowed_models = [(env.get("ALLOWED_MODELS") or "AP45").strip()]
    allowed_models = [m.strip() for m in allowed_models[0].split(",") if m.strip()]

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

    # Parse baseline
    baseline = parse_baseline_report()
    if not baseline:
        console.error("No baseline data found. Run pre-validation first!")
        sys.exit(1)

    console.info(f"Baseline: {sum(len(v) for v in baseline.values())} APs from pre-validation")

    # Polling loop
    console.info(f"Starting stabilization checks (interval={poll_interval}s, max_wait={max_wait}s)")
    console.info(f"Target version: {target_version}")

    start_time = time.time()
    poll_count = 0

    while time.time() - start_time < max_wait:
        poll_count += 1
        elapsed = int(time.time() - start_time)
        console.info(f"\n--- Poll #{poll_count} (elapsed: {elapsed}s) ---")

        try:
            # Run validation for ALL sites at once
            report_path = run_validation(client, org_id, site_names_with_scope, target_version, allowed_models)
            console.info(f"Post-validation report: {report_path}")

            # Parse and compare
            current = parse_current_validation_report(report_path)
            summary = compare_baseline_to_current(baseline, current)

            # Print summary
            ready_count = sum(1 for s in summary["sites"].values() if s["status"] == "READY")
            upgrading_count = sum(1 for s in summary["sites"].values() if s["status"] == "UPGRADING")
            disconnected_count = sum(1 for s in summary["sites"].values() if s["status"] == "DISCONNECTED")
            
            console.info(f"Status: {ready_count} READY, {upgrading_count} UPGRADING, {disconnected_count} DISCONNECTED")

            # Print problem sites
            for site_name, site_status in summary["sites"].items():
                if site_status["status"] != "READY":
                    console.warning(
                        f"  {site_name}: {site_status['status']} "
                        f"({site_status['connected']}/{site_status['total']} ok, "
                        f"{site_status['upgrading']} upgrading, "
                        f"{site_status['disconnected']} disconnected)"
                    )
                    for issue in site_status["issues"]:
                        console.warning(f"    - {issue}")

            if summary["all_ready"]:
                console.success("\n✓✓✓ All sites stabilized! ✓✓✓")
                sys.exit(0)

        except Exception as e:
            console.error(f"Error during validation: {e}")
            console.error("Continuing to next poll...")

        remaining = max_wait - (time.time() - start_time)
        if remaining > poll_interval:
            console.info(f"Waiting {poll_interval}s before next check ({int(remaining)}s remaining)...")
            time.sleep(poll_interval)
        else:
            break

    # Timeout reached
    console.error(f"\n✗✗✗ Timeout reached after {elapsed}s ✗✗✗")
    console.error("Not all APs have stabilized within the timeout period.")
    console.error("Run post-validation manually to see current status:")
    console.error(f"  python Dev/AP upgrade/validate_ap_status.py -c {input_file} --tag post --env .env")
    sys.exit(1)
