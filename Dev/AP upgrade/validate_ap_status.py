#!/usr/bin/env python3
"""
Validate AP firmware status per site (pre/post-check), driven by Excel/CSV.

Inputs:
- Excel/CSV columns required: site_name, target_version, scope
- .env required:
    MIST_BASE_URL
    MIST_ORG_ID
    MIST_ACCESS_TOKEN

Reporting rules (as requested):
  OK | <site> | eligible=<n> | target=<ver> | scope=<scope>
- FLAGGED sites: summary line + only problematic APs
  FLAGGED | <site> | eligible=<n> | mismatched=<n> | disconnected=<n> | upgrading=<n>
    - <ap> [<model>] status=<status> version=<version>  <issue1>; <issue2>
- SKIPPED sites: single line with reason
  SKIPPED | <site> | reason=<reason>

Tagging:
- --tag pre  : labels OK as BASELINE_OK
- --tag post : labels OK as SUCCESS
- otherwise  : labels OK as OK

Exit codes:
- 0: All sites passed validation (OK/BASELINE_OK/SUCCESS)
- 1: Any site flagged, errored, or all skipped

Usage:
  python validate_ap_status.py -x site_list.xlsx --tag pre
  python validate_ap_status.py -c site_list.csv --tag post
  python validate_ap_status.py -x site_list.xlsx
"""

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


def parse_allowed_models(env: Dict[str, str]) -> List[str]:
    raw = (env.get("ALLOWED_MODELS") or "AP45").strip()
    return [m.strip().upper() for m in raw.split(",") if m.strip()]


def parse_int(env: Dict[str, str], key: str, default: int) -> int:
    v = env.get(key)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default


def normalize_scope(scope: str) -> str:
    s = (scope or "").strip().lower()
    if s in ("connected", "connected_only", "online"):
        return "connected"
    return "all"


def ok_label(tag: str) -> str:
    t = (tag or "").strip().lower()
    if t == "pre":
        return "BASELINE_OK"
    if t == "post":
        return "SUCCESS"
    return "OK"


# ----------------------------
# Mist client (GET only)
# ----------------------------
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
        self.session.trust_env = False  # deterministic behavior

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
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)

                # Retry transient errors
                if resp.status_code in (429, 500, 502, 503, 504):
                    if attempt < self.max_retries:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                time.sleep(int(retry_after))
                            except Exception:
                                time.sleep(min(30, 2 ** attempt))
                        else:
                            time.sleep(min(30, 2 ** attempt))
                        continue

                if not resp.ok:
                    raise RuntimeError(f"GET {path} failed ({resp.status_code}): {resp.text}")

                return resp.json() if resp.text.strip() else None

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                raise
            except Exception as e:
                last_err = e
                raise

        if last_err:
            raise last_err
        raise RuntimeError(f"GET {path} failed unexpectedly")


# ----------------------------
# Excel & CSV Input
# ----------------------------
def read_csv_rows(path: str) -> List[Dict[str, str]]:
    """Read CSV file with required columns: site_name, target_version, scope"""
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV is empty")

        required = ["site_name", "target_version", "scope"]
        missing = [c for c in required if c not in reader.fieldnames]
        if missing:
            raise ValueError(f"Missing required column(s): {missing}. Found: {list(reader.fieldnames)}")

        for row_num, row in enumerate(reader, start=2):
            site_name = (row.get("site_name") or "").strip()
            if not site_name:
                continue
            rows.append({
                "site_name": site_name,
                "target_version": (row.get("target_version") or "").strip(),
                "scope": normalize_scope((row.get("scope") or "").strip()),
            })

    if not rows:
        raise ValueError("No valid rows in CSV file")
    return rows


def read_excel_rows(path: str, sheet_name: Optional[str] = None) -> List[Dict[str, str]]:
    wb = load_workbook(filename=path, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.worksheets[0]

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Excel sheet is empty")

    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    idx = {h.lower(): i for i, h in enumerate(headers) if h}

    required = ["site_name", "target_version", "scope"]
    missing = [c for c in required if c not in idx]
    if missing:
        raise ValueError(f"Missing required column(s): {missing}. Found headers: {headers}")

    out: List[Dict[str, str]] = []
    for r in rows[1:]:
        if r is None:
            continue
        site_name = r[idx["site_name"]]
        target_version = r[idx["target_version"]]
        scope = r[idx["scope"]]

        if site_name is None or str(site_name).strip() == "":
            continue

        out.append(
            {
                "site_name": str(site_name).strip(),
                "target_version": str(target_version).strip() if target_version is not None else "",
                "scope": normalize_scope(str(scope).strip() if scope is not None else "all"),
            }
        )

    if not out:
        raise ValueError("No valid rows in Excel sheet")
    return out


# ----------------------------
# Site resolution
# ----------------------------
def build_site_map(client: MistClient, org_id: str) -> Dict[str, str]:
    # Single page fetch; assumes <= 1000 sites. Your org ~450.
    sites = client.get(f"/orgs/{org_id}/sites", params={"limit": 1000, "page": 1})
    if not isinstance(sites, list):
        raise RuntimeError("Unexpected response when listing sites (expected a list)")
    return {str(s["name"]).strip().lower(): s["id"] for s in sites if s.get("name") and s.get("id")}


def get_site_devices(client: MistClient, site_id: str) -> List[Dict[str, Any]]:
    data = client.get(f"/sites/{site_id}/stats/devices")
    return data if isinstance(data, list) else []


# ----------------------------
# Device helpers
# ----------------------------
def device_name(d: Dict[str, Any]) -> str:
    return (d.get("name") or d.get("hostname") or d.get("device_name") or d.get("id") or "unknown").strip()


def device_model(d: Dict[str, Any]) -> str:
    return (d.get("model") or "").strip().upper()


def device_status(d: Dict[str, Any]) -> str:
    return (d.get("status") or "unknown").strip().lower()


def device_version(d: Dict[str, Any]) -> str:
    for k in ("version", "firmware_version", "firmware", "sw_version"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "UNKNOWN"


def device_upgrade_state(d: Dict[str, Any]) -> Optional[str]:
    for k in ("firmware_status", "upgrade_status", "download_status", "status_upgrade", "upgrade"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def is_upgrade_in_progress(state: Optional[str]) -> bool:
    if not state:
        return False
    s = state.lower()
    return any(x in s for x in ("download", "downloading", "upgrade", "upgrading", "install", "installing", "reboot"))


def evaluate_site(
    devices: List[Dict[str, Any]],
    target_version: str,
    scope: str,
    allowed_models: List[str],
) -> Tuple[int, List[Tuple[Dict[str, Any], List[str]]], Dict[str, int], str]:
    """
    Returns:
      eligible_count,
      flagged_devices (device, issues),
      counts dict,
      skipped_reason ("" if not skipped)
    """
    eligible: List[Dict[str, Any]] = []

    # Filter eligible
    for d in devices:
        model = device_model(d)
        if model not in allowed_models:
            continue
        if scope == "connected" and device_status(d) != "connected":
            continue
        eligible.append(d)

    if not eligible:
        return 0, [], {"mismatched": 0, "disconnected": 0, "upgrading": 0}, "no eligible APs"

    flagged: List[Tuple[Dict[str, Any], List[str]]] = []
    counts = {"mismatched": 0, "disconnected": 0, "upgrading": 0}

    for d in eligible:
        issues: List[str] = []
        status = device_status(d)
        ver = device_version(d)
        up_state = device_upgrade_state(d)

        if ver != target_version:
            issues.append(f"VERSION_MISMATCH(current={ver})")
            counts["mismatched"] += 1

        if status != "connected":
            issues.append(f"STATUS={status}")
            counts["disconnected"] += 1

        if is_upgrade_in_progress(up_state):
            issues.append(f"UPGRADE_IN_PROGRESS({up_state})")
            counts["upgrading"] += 1

        if issues:
            flagged.append((d, issues))

    return len(eligible), flagged, counts, ""


# ----------------------------
# Main
# ----------------------------
def usage():
    print(
        """
Validate AP Firmware Status (Pre/Post Upgrade Check)

Required (one of):
  -x, --excel=        Excel file (.xlsx) with site_name, target_version, scope columns
  -c, --csv=          CSV file (.csv) with site_name, target_version, scope columns

Optional:
  -e, --env=          Env file path (default: ./.env)
  --sheet=            Sheet name (Excel only, default: first sheet)
  --tag=              Tag for reporting (pre|post, default: none)
                      pre  → labels OK as BASELINE_OK
                      post → labels OK as SUCCESS

Exit codes:
  0: All sites OK/BASELINE_OK/SUCCESS
  1: Any site FLAGGED, errored, or all sites SKIPPED

Example:
  python validate_ap_status.py -x site_list.xlsx --tag pre
  python validate_ap_status.py -c site_list.csv --tag post
"""
    )
    sys.exit(1)


def main():
    input_file: Optional[str] = None
    input_format = "xlsx"  # "xlsx" or "csv"
    env_path: str = ENV_FILE_DEFAULT
    sheet_name: Optional[str] = None
    tag: str = ""

    try:
        opts, _ = getopt.getopt(
            sys.argv[1:],
            "hx:c:e:",
            ["help", "sheet=", "tag=", "env=", "excel=", "csv="],
        )
    except getopt.GetoptError as err:
        print(f"Error: {err}")
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
        elif o in ("--env", "-e"):
            env_path = a
        elif o == "--sheet":
            sheet_name = a
        elif o == "--tag":
            tag = a.strip().lower()

    if not input_file:
        print("Error: Input file is required (-x/--excel or -c/--csv)")
        usage()

    # Load environment
    try:
        env = load_env_file(env_path)
    except Exception as e:
        print(f"ERROR: Failed to load env file: {e}")
        sys.exit(1)

    base_url = env.get("MIST_BASE_URL")
    org_id = env.get("MIST_ORG_ID")
    token = env.get("MIST_ACCESS_TOKEN")

    if not base_url or not org_id or not token:
        print("ERROR: Missing MIST_BASE_URL, MIST_ORG_ID, or MIST_ACCESS_TOKEN in .env")
        sys.exit(1)

    allowed_models = parse_allowed_models(env)
    max_retries = parse_int(env, "MAX_RETRIES", 5)

    proxy = env.get("ALL_PROXY") or env.get("HTTPS_PROXY") or env.get("HTTP_PROXY")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    no_proxy = env.get("NO_PROXY")

    # Initialize Mist client
    client = MistClient(
        base_url=base_url,
        token=token,
        proxies=proxies,
        no_proxy=no_proxy,
        max_retries=max_retries,
    )

    # Read input file
    try:
        if input_format == "csv":
            rows = read_csv_rows(input_file)
        else:
            rows = read_excel_rows(input_file, sheet_name=sheet_name)
    except Exception as e:
        print(f"ERROR: Failed to read input file: {e}")
        sys.exit(1)

    # Build site map
    try:
        site_map = build_site_map(client, org_id)
    except Exception as e:
        print(f"ERROR: Failed to load sites from Mist: {e}")
        sys.exit(1)

    # Generate report
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag_part = f"_{tag}" if tag else ""
    report_path = f"upgrade_validation{tag_part}_{stamp}.txt"

    lines: List[str] = []
    lines.append(f"Upgrade Validation Report{(' (' + tag + ')') if tag else ''}")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Allowed models: {', '.join(allowed_models)}")
    lines.append(f"Input file: {input_file}")
    lines.append("-" * 80)

    total = ok = flagged = skipped = 0
    ok_word = ok_label(tag)

    for r in rows:
        total += 1
        site_name = r["site_name"]
        target_version = r["target_version"]
        scope = r["scope"]

        if not target_version:
            lines.append(f"FLAGGED | {site_name} | reason=missing target_version")
            flagged += 1
            continue

        site_id = site_map.get(site_name.lower())
        if not site_id:
            lines.append(f"SKIPPED | {site_name} | reason=site not found")
            skipped += 1
            continue

        try:
            devices = get_site_devices(client, site_id)
            eligible_count, flagged_devices, counts, skip_reason = evaluate_site(
                devices, target_version, scope, allowed_models
            )

            if skip_reason:
                lines.append(f"SKIPPED | {site_name} | reason={skip_reason} (scope={scope})")
                skipped += 1
                continue

            if not flagged_devices:
                lines.append(
                    f"{ok_word} | {site_name} | eligible={eligible_count} | target={target_version} | scope={scope}"
                )
                ok += 1
            else:
                lines.append(
                    f"FLAGGED | {site_name} | eligible={eligible_count} | "
                    f"mismatched={counts['mismatched']} | disconnected={counts['disconnected']} | upgrading={counts['upgrading']}"
                )
                for d, issues in flagged_devices:
                    lines.append(
                        f"  - {device_name(d)} [{device_model(d)}] status={device_status(d)} "
                        f"version={device_version(d)}  {'; '.join(issues)}"
                    )
                flagged += 1

        except Exception as e:
            lines.append(f"FLAGGED | {site_name} | reason=API error: {e}")
            flagged += 1

    lines.append("-" * 80)
    lines.append("SUMMARY")
    lines.append(f"  Sites processed: {total}")
    lines.append(f"  {ok_word}: {ok}")
    lines.append(f"  FLAGGED: {flagged}")
    lines.append(f"  SKIPPED: {skipped}")
    lines.append(f"  Report file: {report_path}")

    # Write report
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"✓ Validation complete. Report saved to: {report_path}")
    except Exception as e:
        print(f"ERROR: Failed to write report: {e}")
        sys.exit(1)

    # Print summary to console
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Sites processed: {total}")
    print(f"  {ok_word}: {ok}")
    print(f"  FLAGGED: {flagged}")
    print(f"  SKIPPED: {skipped}")
    print("=" * 80)

    # Determine exit code
    # Fail if any sites flagged, OR all sites skipped (likely config issue)
    if flagged > 0:
        print(f"\n❌ FAILED: {flagged} site(s) flagged")
        sys.exit(1)

    if skipped == total and total > 0:
        print(f"\n⚠️  WARNING: All {total} site(s) were skipped (possible configuration issue)")
        sys.exit(1)

    if ok > 0:
        print(f"\n✅ SUCCESS: All {ok} site(s) validated successfully")
        sys.exit(0)

    print("\n⚠️  No sites were processed")
    sys.exit(0)


if __name__ == "__main__":
    main()
