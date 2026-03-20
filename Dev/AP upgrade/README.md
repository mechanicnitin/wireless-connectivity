# Bulk Mist AP Upgrade (Excel-driven)

Bulk-trigger immediate AP firmware upgrades in Juniper Mist by site, using an Excel sheet as input.

This script:
- Resolves `site_name` to `site_id` via Mist API
- Targets only allowed AP models (default: AP45)
- Optionally upgrades only connected APs (scope control)
- Skips upgrade calls when APs are already on the target version (pre-check)
- Includes rate limiting and retry/backoff for reliability
- Supports corporate proxy settings via `.env`


You are helping design a GitHub Actions-based automation pipeline for upgrading Mist Access Points (APs) using Python scripts and Mist APIs.

## Context

I already have working Python scripts:

1. `bulk_ap_upgrade.py`

   * Reads input file (currently Excel, moving to CSV)
   * Triggers AP firmware upgrades via Mist API
   * Supports flags like:

     * `--preflight`
     * `--dry_run`
   * Currently includes an interactive confirmation (needs to be removed or replaced with `--yes`)

2. `validate_ap_status.py`

   * Validates AP status for given sites
   * Used for:

     * pre-validation (before upgrade)
     * post-validation (after upgrade)
   * Checks:

     * firmware version
     * connection status
     * upgrade state

## Input

* Input will be stored in GitHub repo as **CSV (not Excel)**
* CSV schema:

```
site_name,target_version,scope
Site A,0.14.xxxx,connected
Site B,0.14.xxxx,all
```

## Requirements

Design a **GitHub Actions workflow system** with the following constraints:

### 1. Trigger model

* Workflow must run **after PR is merged to main**
* NOT on PR approval
* Use `push` to `main` or `workflow_dispatch`

### 2. Runner choice

* Use **GitHub-hosted runners (ubuntu-latest)**
* Assume network/proxy access to Mist APIs is already enabled
* DO NOT use self-hosted runners

### 3. Workflow stages (MANDATORY ORDER)

The workflow must strictly follow this sequence:

1. Pre-validation

   * Run: `validate_ap_status.py --tag pre`
   * Fail fast if validation fails

2. Upgrade

   * Run: `bulk_ap_upgrade.py`
   * Must be non-interactive (`--yes` flag or equivalent)

3. Stabilization wait (VERY IMPORTANT)

   * DO NOT immediately run post-validation
   * APs take ~10–15 minutes to upgrade and reconnect
   * Implement **polling (NOT fixed sleep preferred)**:

     * poll every ~2 minutes
     * max timeout ~30 minutes
     * check:

       * APs are connected
       * APs are on target version
       * no AP is in upgrade state
   * This can be:

     * a new script, OR
     * enhancement to existing validator

4. Post-validation

   * Run: `validate_ap_status.py --tag post`
   * Generate final report

### 4. Artifacts

* Upload artifacts for:

  * pre-validation report
  * post-validation report
  * logs (e.g., script.log)

### 5. Secrets & Config

* Use GitHub Secrets for:

  * MIST_BASE_URL
  * MIST_ORG_ID
  * MIST_ACCESS_TOKEN
  * proxy variables if needed

* Create `.env` dynamically inside workflow

### 6. Script improvements (IMPORTANT)

Recommend changes to scripts:

* Add CSV support (in addition to Excel)
* Add `--yes` flag to remove interactive prompt
* Ensure exit codes:

  * non-zero on failure
  * zero on success

### 7. Output expectations

Provide:

1. A complete GitHub Actions YAML workflow (production-ready)
2. Any required script modifications (Python snippets if needed)
3. Clear explanation of:

   * stabilization polling logic
   * failure handling
   * retry behavior (if applicable)

## Important Constraints

* DO NOT trigger upgrades directly from PR approval
* DO NOT run post-validation immediately after upgrade
* DO NOT use fixed sleep unless clearly justified
* DO NOT assume self-hosted runners

## Goal

Produce a safe, production-ready CI/CD-style pipeline for AP upgrades that mimics real operational behavior:

* validate → upgrade → wait → validate

Focus on reliability, observability, and correctness.















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
