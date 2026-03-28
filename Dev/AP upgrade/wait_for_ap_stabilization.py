#!/usr/bin/env python3

import sys
import os
import time
import glob
import re
import subprocess
import argparse
from datetime import datetime


# =========================
# Console helpers
# =========================
class console:
    @staticmethod
    def info(msg): print(f"[INFO] {msg}")
    @staticmethod
    def warn(msg): print(f"[WARN] {msg}")
    @staticmethod
    def error(msg): print(f"[ERROR] {msg}")
    @staticmethod
    def success(msg): print(f"[SUCCESS] {msg}")


# =========================
# Parse baseline report
# =========================
def parse_baseline():
    files = sorted(glob.glob("upgrade_validation_pre_*.txt"), reverse=True)
    if not files:
        console.error("No pre-validation report found!")
        sys.exit(1)

    file = files[0]
    console.info(f"Using baseline file: {file}")

    baseline = {}
    current_site = None

    with open(file, "r") as f:
        for line in f:
            line = line.strip()

            # Site header
            if " | " in line and "eligible=" in line:
                current_site = line.split("|")[1].strip()
                baseline[current_site] = {}

            # AP line (only connected + OK)
            if line.startswith("- ") or line.startswith("  - "):
                if "OK" in line and "status=connected" in line:
                    ap = line.split("[")[0].replace("-", "").strip()

                    version_match = re.search(r"version=(\S+)", line)
                    version = version_match.group(1) if version_match else "UNKNOWN"

                    baseline[current_site][ap] = version

    total = sum(len(v) for v in baseline.values())
    console.info(f"Baseline APs tracked: {total}")

    return baseline


# =========================
# Run post validation
# =========================
def run_post_validation(csv_file):
    cmd = [
        "python",
        "validate_ap_status.py",
        "-c", csv_file,
        "--tag", "post"
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        console.warn("Post-validation script failed. Retrying next poll...")
        return None

    files = sorted(glob.glob("upgrade_validation_post_*.txt"), reverse=True)
    return files[0] if files else None


# =========================
# Parse post report
# =========================
def parse_post(file):
    data = {}
    current_site = None

    with open(file, "r") as f:
        for line in f:
            line = line.strip()

            if " | " in line and "eligible=" in line:
                current_site = line.split("|")[1].strip()
                data[current_site] = {}

            if line.startswith("- ") or line.startswith("  - "):
                ap = line.split("[")[0].replace("-", "").strip()

                status = "connected" if "status=connected" in line else "disconnected"

                version_match = re.search(r"version=(\S+)", line)
                version = version_match.group(1) if version_match else "UNKNOWN"

                upgrading = "UPGRADE_IN_PROGRESS" in line

                data[current_site][ap] = {
                    "status": status,
                    "version": version,
                    "upgrading": upgrading
                }

    return data


# =========================
# Compare logic
# =========================
def evaluate(baseline, current, target_version):
    ready = 0
    upgrading = 0
    disconnected = 0

    for site in baseline:
        for ap in baseline[site]:

            if site not in current or ap not in current[site]:
                continue

            state = current[site][ap]

            # HARD FAIL
            if state["status"] != "connected":
                console.error(f"{ap}: DISCONNECTED (was connected pre-upgrade!)")
                sys.exit(1)

            if state["version"] == target_version and not state["upgrading"]:
                ready += 1
            elif state["upgrading"]:
                upgrading += 1
            else:
                upgrading += 1  # still old version

    return ready, upgrading, disconnected


# =========================
# Main
# =========================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--csv", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--poll-interval", type=int, default=120)
    parser.add_argument("--max-wait", type=int, default=1800)

    args = parser.parse_args()

    baseline = parse_baseline()
    total_aps = sum(len(v) for v in baseline.values())

    console.info(f"Target version: {args.target_version}")
    console.info("")

    start_time = time.time()
    poll = 1

    while True:
        elapsed = int(time.time() - start_time)

        if elapsed > args.max_wait:
            console.error("Timeout: Not all APs stabilized")
            sys.exit(1)

        console.info(f"--- Poll #{poll} (elapsed: {elapsed}s) ---")

        report = run_post_validation(args.csv)
        if not report:
            time.sleep(args.poll_interval)
            continue

        current = parse_post(report)

        ready, upgrading, disconnected = evaluate(
            baseline, current, args.target_version
        )

        console.info(f"Status: {ready} READY, {upgrading} UPGRADING, {disconnected} DISCONNECTED")

        if ready == total_aps:
            console.success("✓✓✓ All APs stabilized and upgraded! ✓✓✓")
            sys.exit(0)

        remaining = args.max_wait - elapsed
        console.info(f"Waiting {args.poll_interval}s before next check ({remaining}s remaining)...\n")

        time.sleep(args.poll_interval)
        poll += 1


if __name__ == "__main__":
    main()
