#!/usr/bin/env python3
"""
Wait for AP stabilization - VERSION-AWARE (Parallel Checking with API Delay Handling).

This script works like manual post-validation checking:
1. Run post-validation for ALL sites at once
2. Compare current state to pre-validation baseline
3. VALIDATE VERSIONS - parse target vs current version
4. Show summary: ready sites, still upgrading, disconnected, version mismatches
5. Only fail if an AP that WAS connected is permanently disconnected
6. Retry with configurable wait between polls

KEY DIFFERENCES:
- Validates VERSION (parses target vs current)
- Handles API delay (versions take time to register on Mist)
- Only marks "READY" when: connected AND current_version == target_version AND not upgrading
- Parses BOTH BASELINE_OK and FLAGGED baseline reports
- Uses version=(target) (current=actual) format

Usage:
  python wait_for_ap_stabilization.py -c site_list.csv --target-version 0.14.29967
"""

import sys
import os
import csv
import getopt
import time
import glob
import re
import subprocess
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


def parse_baseline_report() -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    Parse pre-validation report to get baseline AP states.
    
    Handles BOTH BASELINE_OK and FLAGGED lines.
    Parses NEW format: version=<target> (current=<actual>)
    
    Returns: Dict[site_name -> Dict[ap_name -> {"status": "...", "target_version": "...", "current_version": "..."}]]
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
            
            # Match site header: "BASELINE_OK | Switch_POC | ..." or "FLAGGED | Switch_POC | ..."
            if " | " in line_stripped and ("eligible=" in line_stripped or "mismatched=" in line_stripped):
                parts = line_stripped.split(" | ")
                if len(parts) >= 2:
                    current_site = parts[1].strip()
                    baseline.setdefault(current_site, {})
                    console.info(f"  Baseline site: {current_site}")
            
            # Match AP line: "  - cbnp48ge-01-jap02-45la [AP45] status=connected version=0.14.29967 (current=0.14.29543)  OK"
            if line_stripped.startswith("  - ") and current_site:
                # Extract AP name
                ap_name = line_stripped[4:].split(" [")[0].strip()
                
                # Extract status
                status = "unknown"
                if "status=disconnected" in line_stripped:
                    status = "disconnected"
                elif "status=connected" in line_stripped:
                    status = "connected"
                
                # Extract target version and current version
                # Format: version=<target> (current=<actual>)
                version_match = re.search(r'version=(\S+)\s+\(current=(\S+)\)', line_stripped)
                if version_match:
                    target_ver = version_match.group(1)
                    current_ver = version_match.group(2)
                else:
                    # Fallback for old format
                    version_match = re.search(r'version=(\S+)', line_stripped)
                    target_ver = version_match.group(1) if version_match else "UNKNOWN"
                    current_ver = "UNKNOWN"
                
                baseline[current_site][ap_name] = {
                    "status": status,
                    "target_version": target_ver,
                    "current_version": current_ver
                }
                console.info(f"    AP: {ap_name} -> status={status}, target={target_ver}, current={current_ver}")
        
        total_aps = sum(len(v) for v in baseline.values())
        console.info(f"Baseline parsed: {total_aps} APs tracked across {len(baseline)} sites")
    
    except Exception as e:
        console.warning(f"Failed to parse baseline report: {e}")
    
    return baseline


def parse_current_validation_report(report_path: str) -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    Parse current validation report to get current AP states.
    
    Parses NEW format: version=<target> (current=<actual>)
    
    Returns: Dict[site_name -> Dict[ap_name -> {"status": "...", "target_version": "...", "current_version": "...", "issues": [...]}]]
    """
    current = {}
    
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        current_site = None
        for line in lines:
            line_stripped = line.strip()
            
            # Match site header
            if " | " in line_stripped and ("eligible=" in line_stripped or "mismatched=" in line_stripped):
                parts = line_stripped.split(" | ")
                if len(parts) >= 2:
                    current_site = parts[1].strip()
                    current.setdefault(current_site, {})
            
            # Match AP line
            if line_stripped.startswith("  - ") and current_site:
                ap_name = line_stripped[4:].split(" [")[0].strip()
                
                # Extract status
                status = "unknown"
                if "status=disconnected" in line_stripped:
                    status = "disconnected"
                elif "status=connected" in line_stripped:
                    status = "connected"
                
                # Extract target version and current version
                version_match = re.search(r'version=(\S+)\s+\(current=(\S+)\)', line_stripped)
                if version_match:
                    target_ver = version_match.group(1)
                    current_ver = version_match.group(2)
                else:
                    # Fallback for old format
                    version_match = re.search(r'version=(\S+)', line_stripped)
                    target_ver = version_match.group(1) if version_match else "UNKNOWN"
                    current_ver = "UNKNOWN"
                
                # Extract issues
                issues = []
                if "UPGRADE_IN_PROGRESS" in line_stripped:
                    issues.append("upgrading")
                if "VERSION_MISMATCH" in line_stripped:
                    issues.append("version_mismatch")
                if "STATUS=" in line_stripped:
                    issues.append("status_issue")
                
                current[current_site][ap_name] = {
                    "status": status,
                    "target_version": target_ver,
                    "current_version": current_ver,
                    "issues": issues
                }
    
    except Exception as e:
        console.warning(f"Failed to parse current report: {e}")
    
    return current


def compare_baseline_to_current(
    baseline: Dict[str, Dict[str, Dict[str, str]]],
    current: Dict[str, Dict[str, Dict[str, str]]],
    target_version: str,
) -> Dict[str, Any]:
    """
    Compare baseline to current state and return status summary.
    
    KEY LOGIC:
    - AP must be CONNECTED (or was disconnected pre-upgrade, then skip)
    - AP's CURRENT version must match TARGET_VERSION
    - AP must NOT be upgrading
    
    Returns:
    {
      "all_ready": bool,
      "sites": {
        site_name: {
          "status": "READY" | "UPGRADING" | "VERSION_MISMATCH" | "DISCONNECTED",
          "connected": int,
          "correct_version": int,
          "upgrading": int,
          "disconnected": int,
          "version_mismatch": int,
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
        correct_version = 0
        upgrading = 0
        disconnected = 0
        version_mismatch = 0
        issues = []
        
        # For each AP that was in baseline
        for ap_name, baseline_state in baseline_aps.items():
            baseline_status = baseline_state["status"]
            baseline_target = baseline_state["target_version"]
            baseline_current = baseline_state["current_version"]
            
            current_state = current_aps.get(ap_name, {})
            current_status = current_state.get("status", "unknown")
            current_target = current_state.get("target_version", "UNKNOWN")
            current_current = current_state.get("current_version", "UNKNOWN")
            current_issues = current_state.get("issues", [])
            
            # Skip APs that were disconnected pre-upgrade
            if baseline_status == "disconnected":
                console.info(f"    {site_name}/{ap_name}: was disconnected pre-upgrade (skip)")
                continue
            
            # This AP WAS connected before upgrade
            if baseline_status == "connected":
                # Check 1: Is it still connected?
                if current_status == "disconnected":
                    disconnected += 1
                    issues.append(f"{ap_name}: DISCONNECTED (was connected pre-upgrade!)")
                    summary["all_ready"] = False
                elif current_status == "connected":
                    connected += 1
                else:
                    issues.append(f"{ap_name}: unknown status {current_status}")
                    summary["all_ready"] = False
                
                # Check 2: Is current version == target version?
                # (target_version is what we're upgrading TO)
                if current_current == target_version:
                    correct_version += 1
                else:
                    version_mismatch += 1
                    issues.append(f"{ap_name}: version {current_current} (target: {target_version})")
                    summary["all_ready"] = False
                
                # Check 3: Is it upgrading?
                if "upgrading" in current_issues:
                    upgrading += 1
                    issues.append(f"{ap_name}: UPGRADING (may take time)")
                    summary["all_ready"] = False
        
        total = len([b for b in baseline_aps.values() if b["status"] == "connected"])
        
        if total == 0:
            status = "SKIPPED"
        elif disconnected > 0:
            status = "DISCONNECTED"
        elif version_mismatch > 0:
            status = "VERSION_MISMATCH"
        elif upgrading > 0:
            status = "UPGRADING"
        elif connected == total and correct_version == total:
            status = "READY"
        else:
            status = "UNKNOWN"
        
        summary["sites"][site_name] = {
            "status": status,
            "connected": connected,
            "correct_version": correct_version,
            "upgrading": upgrading,
            "disconnected": disconnected,
            "version_mismatch": version_mismatch,
            "total": total,
            "issues": issues[:3]
        }
    
    return summary


def run_validation(site_names_with_scope: Dict[str, str], target_version: str) -> str:
    """
    Run validate_ap_status.py and return report path.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
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
            timeout=300
        )
        
        # Find most recent post validation report
        reports = sorted(glob.glob("upgrade_validation_post_*.txt"), reverse=True)
        if reports:
            report_path = reports[0]
            console.info(f"Generated report: {report_path}")
            return report_path
        else:
            raise RuntimeError("No post-validation report generated")
    
    except Exception as e:
        console.error(f"Failed to run validation: {e}")
        raise
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)


def usage():
    print(
        """
Wait for AP Stabilization - VERSION-AWARE (Parallel Checking with API Delay Handling)

Required:
  -c, --csv=          CSV file (.csv) with site_name, scope columns
  --target-version=   Target firmware version (e.g., 0.14.29967)

Optional:
  -e, --env=          Env file path (default: ./.env)
  --poll-interval=    Seconds between polls (default: 120 = 2 min)
  --max-wait=         Maximum seconds to wait (default: 1800 = 30 min)

VALIDATION CRITERIA (ALL must pass):
1. AP must be CONNECTED (or was disconnected pre-upgrade)
2. AP's CURRENT version must == TARGET_VERSION
3. AP must NOT be upgrading

HOW IT WORKS:
1. Reads pre-validation baseline report
2. Runs post-validation for ALL sites at once (parallel)
3. Compares current state to baseline
4. Parses version=<target> (current=<actual>) format
5. Reports: ready sites, upgrading, disconnected, version mismatches
6. Only fails if AP that WAS connected is now disconnected
7. Retries with configurable wait (2 min default) to handle API delays

Example:
  python wait_for_ap_stabilization.py -c site_list.csv --target-version 0.14.29967
  python wait_for_ap_stabilization.py -c site_list.csv --target-version 0.14.29967 --poll-interval 120 --max-wait 1800
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

    # Read input file
    try:
        rows = read_csv_rows(input_file)
    except Exception as e:
        console.error(f"Failed to read input file: {e}")
        sys.exit(1)

    site_names_with_scope = {r["site_name"]: r.get("scope", "all") for r in rows}
    console.info(f"Loaded {len(site_names_with_scope)} site(s)")

    # Parse baseline
    baseline = parse_baseline_report()
    if not baseline:
        console.error("No baseline data found. Run pre-validation first!")
        sys.exit(1)

    total_baseline_aps = sum(len(v) for v in baseline.values())
    console.info(f"Baseline: {total_baseline_aps} APs tracked")

    # Polling loop
    console.info(f"Starting stabilization checks (interval={poll_interval}s, max_wait={max_wait}s)")
    console.info(f"Target version: {target_version}")
    console.info(f"Validation: connected + current_version == target_version + not upgrading\n")

    start_time = time.time()
    poll_count = 0

    while time.time() - start_time < max_wait:
        poll_count += 1
        elapsed = int(time.time() - start_time)
        console.info(f"--- Poll #{poll_count} (elapsed: {elapsed}s) ---")

        try:
            # Run validation for ALL sites at once
            report_path = run_validation(site_names_with_scope, target_version)

            # Parse and compare
            current = parse_current_validation_report(report_path)
            summary = compare_baseline_to_current(baseline, current, target_version)

            # Print summary
            ready_count = sum(1 for s in summary["sites"].values() if s["status"] == "READY")
            upgrading_count = sum(1 for s in summary["sites"].values() if s["status"] == "UPGRADING")
            version_mismatch_count = sum(1 for s in summary["sites"].values() if s["status"] == "VERSION_MISMATCH")
            disconnected_count = sum(1 for s in summary["sites"].values() if s["status"] == "DISCONNECTED")
            
            console.info(f"Status: {ready_count} READY, {upgrading_count} UPGRADING, {version_mismatch_count} VERSION_MISMATCH, {disconnected_count} DISCONNECTED")

            # Print problem sites
            for site_name, site_status in summary["sites"].items():
                if site_status["status"] != "READY":
                    console.warning(
                        f"  {site_name}: {site_status['status']} "
                        f"({site_status['connected']}/{site_status['total']} connected, "
                        f"{site_status['correct_version']}/{site_status['total']} correct version, "
                        f"{site_status['upgrading']} upgrading)"
                    )
                    for issue in site_status["issues"]:
                        console.warning(f"    - {issue}")

            if summary["all_ready"]:
                console.success("\n✓✓✓ All sites stabilized and versions updated! ✓✓✓")
                sys.exit(0)

        except Exception as e:
            console.error(f"Error during validation: {e}")
            console.error("Continuing to next poll...")

        remaining = max_wait - (time.time() - start_time)
        if remaining > poll_interval:
            console.info(f"Waiting {poll_interval}s before next check ({int(remaining)}s remaining)...\n")
            time.sleep(poll_interval)
        else:
            break

    # Timeout reached
    console.error(f"\n✗✗✗ Timeout reached after {elapsed}s ✗✗✗")
    console.error("Not all APs have stabilized within the timeout period.")
    console.error("\nRun post-validation manually to see current status:")
    console.error(f"  python Dev/AP upgrade/validate_ap_status.py -c {input_file} --tag post --env .env")
    sys.exit(1)
