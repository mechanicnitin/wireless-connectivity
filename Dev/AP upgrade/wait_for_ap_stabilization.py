#!/usr/bin/env python3
"""
Wait for AP stabilization - VERSION-AWARE with API delay handling.

Works like manual post-validation checking:
1. Parse pre-validation baseline (which APs were connected)
2. Run post-validation for ALL sites
3. Compare: current_version == target_version AND connected
4. Only mark READY when all conditions met
5. Retry every 2 min to handle API delays

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


def parse_baseline_report() -> Dict[str, Dict[str, str]]:
    """
    Parse pre-validation report to get baseline AP states.
    
    Returns: Dict[site_name -> Dict[ap_name -> "version"]]
    Only tracks CONNECTED APs (eligible ones)
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
            
            # Match site header: "BASELINE_OK | Switch_POC | ..."
            if " | " in line_stripped and "eligible=" in line_stripped:
                parts = line_stripped.split(" | ")
                if len(parts) >= 2:
                    current_site = parts[1].strip()
                    baseline.setdefault(current_site, {})
                    console.info(f"  Baseline site: {current_site}")
            
            # Match eligible AP line: "  - cbnp48ge-01-jap02-45la [AP45] status=connected version=0.14.29543  OK"
            if line_stripped.startswith("  - ") and current_site and "OK" in line_stripped:
                ap_name = line_stripped[4:].split(" [")[0].strip()
                
                # Extract version
                version_match = re.search(r'version=(\S+)', line_stripped)
                version = version_match.group(1) if version_match else "UNKNOWN"
                
                baseline[current_site][ap_name] = version
                console.info(f"    AP: {ap_name} baseline version={version}")
        
        total_aps = sum(len(v) for v in baseline.values())
        console.info(f"Baseline parsed: {total_aps} connected APs tracked")
    
    except Exception as e:
        console.warning(f"Failed to parse baseline report: {e}")
    
    return baseline


def parse_current_validation_report(report_path: str) -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    Parse current validation report.
    
    Returns: Dict[site_name -> Dict[ap_name -> {"status": "...", "version": "...", "issues": [...]}]]
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
                
                # Extract status
                status = "unknown"
                if "status=disconnected" in line_stripped:
                    status = "disconnected"
                elif "status=connected" in line_stripped:
                    status = "connected"
                
                # Extract version
                version_match = re.search(r'version=(\S+)', line_stripped)
                version = version_match.group(1) if version_match else "UNKNOWN"
                
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
                    "version": version,
                    "issues": issues
                }
    
    except Exception as e:
        console.warning(f"Failed to parse current report: {e}")
    
    return current


def compare_baseline_to_current(
    baseline: Dict[str, Dict[str, str]],
    current: Dict[str, Dict[str, Dict[str, str]]],
    target_version: str,
) -> Dict[str, Any]:
    """
    Compare baseline to current state.
    
    For each AP in baseline (was connected at pre-validation):
    - Must still be connected
    - Must be at target_version
    """
    summary = {
        "all_ready": True,
        "sites": {}
    }
    
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
        
        for ap_name, baseline_version in baseline_aps.items():
            current_ap = current_aps.get(ap_name, {})
            current_status = current_ap.get("status", "unknown")
            current_version = current_ap.get("version", "UNKNOWN")
            current_issues = current_ap.get("issues", [])
            
            # Check 1: Still connected?
            if current_status == "connected":
                connected += 1
            elif current_status == "disconnected":
                disconnected += 1
                issues.append(f"{ap_name}: DISCONNECTED (was connected pre-upgrade!)")
                summary["all_ready"] = False
            else:
                issues.append(f"{ap_name}: unknown status {current_status}")
                summary["all_ready"] = False
            
            # Check 2: At target version?
            if current_version == target_version:
                correct_version += 1
            else:
                version_mismatch += 1
                issues.append(f"{ap_name}: version {current_version} (target: {target_version})")
                summary["all_ready"] = False
            
            # Check 3: Upgrading?
            if "upgrading" in current_issues:
                upgrading += 1
                issues.append(f"{ap_name}: UPGRADING")
                summary["all_ready"] = False
        
        total = len(baseline_aps)
        
        if disconnected > 0:
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
    """Run validate_ap_status.py post-validation"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = f"temp_sites_{stamp}.csv"
    
    try:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("site_name,target_version,scope\n")
            for site_name, scope in site_names_with_scope.items():
                f.write(f"{site_name},{target_version},{scope}\n")
        
        subprocess.run(
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
        
        reports = sorted(glob.glob("upgrade_validation_post_*.txt"), reverse=True)
        if reports:
            return reports[0]
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
Wait for AP Stabilization - VERSION-AWARE

Required:
  -c, --csv=          CSV file with site_name, scope columns
  --target-version=   Target firmware version (e.g., 0.14.29967)

Optional:
  -e, --env=          Env file path (default: ./.env)
  --poll-interval=    Seconds between polls (default: 120)
  --max-wait=         Maximum seconds to wait (default: 1800)

VALIDATION: AP must be CONNECTED + CURRENT_VERSION == TARGET_VERSION

Example:
  python wait_for_ap_stabilization.py -c site_list.csv --target-version 0.14.29967
"""
    )
    sys.exit(1)


if __name__ == "__main__":
    input_file: Optional[str] = None
    env_file = ".env"
    target_version: Optional[str] = None
    poll_interval = 120
    max_wait = 1800

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

    try:
        env = load_env_file(env_file)
    except Exception as e:
        console.error(f"Failed to load env: {e}")
        sys.exit(1)

    try:
        rows = read_csv_rows(input_file)
    except Exception as e:
        console.error(f"Failed to read input file: {e}")
        sys.exit(1)

    site_names_with_scope = {r["site_name"]: r.get("scope", "all") for r in rows}
    console.info(f"Loaded {len(site_names_with_scope)} site(s)")

    baseline = parse_baseline_report()
    if not baseline:
        console.error("No baseline data found. Run pre-validation first!")
        sys.exit(1)

    total_baseline_aps = sum(len(v) for v in baseline.values())
    console.info(f"Baseline: {total_baseline_aps} connected APs")
    console.info(f"Target version: {target_version}")
    console.info(f"Poll interval: {poll_interval}s, max wait: {max_wait}s\n")

    start_time = time.time()
    poll_count = 0

    while time.time() - start_time < max_wait:
        poll_count += 1
        elapsed = int(time.time() - start_time)
        console.info(f"--- Poll #{poll_count} (elapsed: {elapsed}s) ---")

        try:
            report_path = run_validation(site_names_with_scope, target_version)
            current = parse_current_validation_report(report_path)
            summary = compare_baseline_to_current(baseline, current, target_version)

            ready_count = sum(1 for s in summary["sites"].values() if s["status"] == "READY")
            upgrading_count = sum(1 for s in summary["sites"].values() if s["status"] == "UPGRADING")
            version_mismatch_count = sum(1 for s in summary["sites"].values() if s["status"] == "VERSION_MISMATCH")
            disconnected_count = sum(1 for s in summary["sites"].values() if s["status"] == "DISCONNECTED")
            
            console.info(f"Status: {ready_count} READY, {upgrading_count} UPGRADING, {version_mismatch_count} VERSION_MISMATCH, {disconnected_count} DISCONNECTED")

            for site_name, site_status in summary["sites"].items():
                if site_status["status"] != "READY":
                    console.warning(
                        f"  {site_name}: {site_status['status']} "
                        f"({site_status['connected']}/{site_status['total']} connected, "
                        f"{site_status['correct_version']}/{site_status['total']} correct version)"
                    )
                    for issue in site_status["issues"]:
                        console.warning(f"    - {issue}")

            if summary["all_ready"]:
                console.success("\n✓✓✓ All APs stabilized and versions updated! ✓✓✓")
                sys.exit(0)

        except Exception as e:
            console.error(f"Error: {e}")

        remaining = max_wait - (time.time() - start_time)
        if remaining > poll_interval:
            console.info(f"Waiting {poll_interval}s before next check ({int(remaining)}s remaining)...\n")
            time.sleep(poll_interval)
        else:
            break

    console.error(f"\n✗✗✗ Timeout after {elapsed}s ✗✗✗")
    console.error("Not all APs stabilized. Run post-validation manually to check status.")
    sys.exit(1)
