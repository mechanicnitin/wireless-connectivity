# Bulk Mist AP Upgrade (Excel-driven)

Bulk-trigger immediate AP firmware upgrades in Juniper Mist by site, using an Excel sheet as input.

This script:
- Resolves `site_name` to `site_id` via Mist API
- Targets only allowed AP models (default: AP45)
- Optionally upgrades only connected APs (scope control)
- Skips upgrade calls when APs are already on the target version (pre-check)
- Includes rate limiting and retry/backoff for reliability
- Supports corporate proxy settings via `.env`

---

## Requirements

- Python 3.10+ recommended
- Packages:
  - `requests`
  - `openpyxl`

Install dependencies:

```bash
python -m pip install requests openpyxl
python bulk_ap_upgrade.py -x site_list.xlsx --dry_run --preflight
python bulk_ap_upgrade.py -x site_list.xlsx --dry_run
python bulk_ap_upgrade.py -x site_list.xlsx

python Validate_ap_status.py -x site_list.xlsx --tag pre
python Validate_ap_status.py -x site_list.xlsx --tag post




Pre-check flow
  A[Start PRE-CHECK] --> B[Load .env<br/>base_url, org_id, token,<br/>proxy(optional), allowed_models]
  B --> C[Read Excel rows<br/>site_name, target_version, scope]
  C --> D[Fetch org sites<br/>GET /orgs/:org_id/sites]
  D --> E{For each Excel row}

  E --> F{target_version present?}
  F -- No --> S1[FLAGGED<br/>reason=missing target_version] --> E
  F -- Yes --> G{site_name resolves to site_id?}
  G -- No --> S2[SKIPPED<br/>reason=site not found/ambiguous] --> E
  G -- Yes --> H[Fetch site devices stats<br/>GET /sites/:site_id/stats/devices]

  H --> I[Filter eligible APs<br/>model in ALLOWED_MODELS<br/>+ scope=connected => status==connected]
  I --> J{Any eligible APs?}
  J -- No --> S3[SKIPPED<br/>reason=no eligible APs] --> E
  J -- Yes --> K[Evaluate each eligible AP<br/>- status==connected?<br/>- version==target_version?<br/>- upgrade state indicates downloading/upgrading?]

  K --> L{Any AP has issues?}
  L -- No --> O1[BASELINE_OK<br/>already on target + healthy] --> E
  L -- Yes --> O2[FLAGGED<br/>summary + list only problematic APs] --> E

  E --> Z[End + write report]



Post-check flow
  A[Start POST-CHECK] --> B[Load .env<br/>base_url, org_id, token,<br/>proxy(optional), allowed_models]
  B --> C[Read Excel rows<br/>site_name, target_version, scope]
  C --> D[Fetch org sites<br/>GET /orgs/:org_id/sites]
  D --> E{For each Excel row}

  E --> F{target_version present?}
  F -- No --> S1[FLAGGED<br/>reason=missing target_version] --> E
  F -- Yes --> G{site_name resolves to site_id?}
  G -- No --> S2[SKIPPED<br/>reason=site not found/ambiguous] --> E
  G -- Yes --> H[Fetch site devices stats<br/>GET /sites/:site_id/stats/devices]

  H --> I[Filter eligible APs<br/>model in ALLOWED_MODELS<br/>+ scope=connected => status==connected]
  I --> J{Any eligible APs?}
  J -- No --> S3[SKIPPED<br/>reason=no eligible APs] --> E
  J -- Yes --> K[Evaluate each eligible AP<br/>- status==connected?<br/>- version==target_version?<br/>- upgrade state indicates downloading/upgrading?]

  K --> L{Any AP has issues?}
  L -- No --> O1[SUCCESS<br/>all eligible APs match target + healthy] --> E
  L -- Yes --> O2[FLAGGED<br/>summary + list only problematic APs] --> E

  E --> Z[End + write report]
