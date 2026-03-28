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
            status = "
