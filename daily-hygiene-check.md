# Juniper Mist Daily Hygiene / Coffee Check

## 1. Purpose

The Daily Hygiene / Coffee Check is an automated operational health assessment of the Juniper Mist organization.

Its purpose is to give a network engineer a concise "daily cup of coffee" view of the infrastructure:

1. What requires immediate attention?
2. Which sites are degraded?
3. What changed in the last 24 hours?
4. How do today's key metrics compare with yesterday?
5. Are DHCP, ARP/gateway, DNS, Internet, or application performance degraded?
6. What does Marvis Mini report for suspicious/degraded sites?
7. Which conditions are known, expected, administrative, or non-actionable?
8. For every important issue, what happened, since when, what is affected, what is the current state, what is the evidence, what is the likely root cause, and what action is required?

Live Juniper Mist MCP/API telemetry is the primary source of truth. Static knowledge, skills, playbooks, and Marvis-inspired methodology are supporting context only.

IPv6 and DHCPv6 are out of scope unless explicitly requested.

---

## 2. Trigger and Intent

Activate this workflow for requests such as:

- daily hygiene
- daily health check
- morning health check
- daily Mist report
- daily network health
- daily coffee report
- what happened overnight
- what changed today
- organization health
- NOC morning report
- operational health summary

Examples:

- `Run daily hygiene check`
- `Give me today's Mist coffee report`
- `What's going on in the organization today?`
- `Give me the morning NOC report`

Normal MCP queries must remain normal. For example, `Show APs`, `List WLANs`, or `How many sites do we have?` must not automatically trigger the full hygiene investigation.

---

## 3. Investigation Philosophy

This is not a dashboard export.

The workflow is:

**DETECT → PRIORITIZE → CORRELATE → INVESTIGATE → EXPLAIN → RECOMMEND → ACT**

Do not simply report every abnormal metric.

Prioritize using:

- severity
- scope
- duration
- user impact
- trend
- affected population
- business/site importance where available
- recent changes
- related alarms/events
- healthy controls
- shared infrastructure
- RF conditions
- network dependencies
- synthetic test results

A high count does not automatically mean a critical incident. A single client issue does not automatically mean an AP/site-wide problem.

---

## 4. Time Windows

### Primary window

Last 24 hours.

### Comparison window

Previous 24 hours.

CURRENT = [T-24h, T]

BASELINE = [T-48h, T-24h]

Where available, also compare against:

- previous 7 days
- same weekday previous week
- normal site/client baseline

Do not compare raw counts without considering client population, traffic, or test volume. Prefer normalized rates and percentages.

If historical data is unavailable, state:

**Historical comparison unavailable.**

Never fabricate a baseline.

---

## 5. Organization Overview

Begin the final report with a compact organization snapshot.

Collect where available:

- total sites
- sites up/down/degraded
- total APs
- APs up/down/disconnected
- APs restarting/rebooting
- AP model distribution
- firmware distribution
- hardware revisions
- WLAN count
- connected client count
- client distribution
- overall SLE health
- critical alarms
- active alerts
- recent major changes

The first section should answer in seconds:

- How big is the environment?
- Is it healthy?
- Where are the problems?
- How serious are they?

Example:

| Area | Today | Status |
|---|---:|---|
| Sites | 42 / 42 up | 🟢 |
| APs | 1,281 / 1,284 up | 🟠 |
| Clients | 8,621 | 🟢 |
| SLE | 96.8% | 🟠 ↓ |
| Authentication | 98.1% | 🟠 ↓ |
| DHCP | 99.4% | 🟢 |
| DNS | 99.7% | 🟢 |
| RF | 97.2% | 🟢 |
| Roaming | 98.4% | 🟢 |
| Critical alarms | 2 | 🔴 |

Only show metrics actually available from Mist.

---

## 6. SLE Summary

Provide today's SLE health and compare with yesterday.

Where available include:

- overall SLE
- successful connects
- coverage
- capacity
- roaming
- authentication
- DHCP
- DNS
- other exposed SLEs

For each important SLE show:

- today
- yesterday
- absolute change
- percentage change where meaningful
- direction
- interpretation

Example:

| SLE | Today | Yesterday | Trend | Assessment |
|---|---:|---:|---|---|
| Overall | 96.8% | 98.2% | ↓ | Degraded |
| Authentication | 94.2% | 98.7% | ↓ | Critical |
| Capacity | 97.8% | 97.6% | → | Stable |
| Coverage | 99.1% | 99.0% | → | Stable |
| Roaming | 98.4% | 98.8% | ↓ | Minor change |

A stable 96% metric may be less important than a sudden 99.5% → 96% deterioration.

---

## 7. Critical Issue Discovery

Identify issues requiring immediate action.

### P1 — Critical / Immediate Action

Examples:

- complete site outage
- large-scale AP outage
- major WLAN outage
- widespread authentication outage
- RADIUS failure affecting many clients/sites
- DHCP failure affecting many clients
- DNS failure affecting many clients
- gateway/network reachability failure
- major client connectivity degradation
- severe application/network performance issue
- widespread client disconnections
- major SLE collapse
- critical-site outage
- common infrastructure failure

### P2 — High / Action Required

Examples:

- significant site degradation
- sustained authentication failures
- AP instability
- high client disconnect rate
- significant RF degradation
- capacity problems
- recurring RADIUS failures
- configuration change followed by degradation

### P3 — Medium / Monitor

Examples:

- localized client issues
- moderate RF degradation
- isolated DHCP/DNS failures
- increasing but non-critical trends
- isolated AP instability

### Informational

Examples:

- planned maintenance
- firmware upgrades
- planned AP replacement
- administrative changes
- controlled testing
- resolved transient events

Severity must consider scope, duration, user impact, trend, business impact where known, and correlation.

---

## 8. Degraded Site Detection

Rank sites by meaningful degradation.

Evaluate:

- AP availability
- client experience
- SLE
- authentication
- DHCP
- DNS
- RF
- roaming
- alarms
- AP restarts
- switch/network connectivity
- application performance
- recent changes

Classify:

- HEALTHY
- DEGRADED
- CRITICAL

Do not label a site critical because of one isolated client.

---

## 9. Network & Application Experience

Create a dedicated health section covering:

- DHCP
- ARP/gateway
- DNS
- Internet reachability
- application latency/performance
- packet loss where available
- jitter where available
- network path indicators where available

Use the dependency chain:

**Association → Authentication → Authorization → DHCP → ARP/Gateway → DNS → Internet → Application**

Identify the earliest confirmed broken dependency.

Do not automatically classify downstream failures as wireless failures.

---

## 10. Marvis Mini / Synthetic Health

Marvis Mini synthetic test results are a high-value source of site-level network/application validation.

Use them especially for sites already identified as suspicious or degraded.

Where available, collect:

- DHCP test result
- ARP/gateway result
- DNS result
- Internet reachability
- application latency/performance
- packet loss
- latency
- jitter
- network path information
- other available synthetic test measurements

Preferred workflow:

**Site anomaly detected → retrieve/run relevant Marvis Mini test → compare DHCP/ARP/DNS/Internet/application results → correlate with Mist telemetry → update severity/RCA confidence**

Do not blindly run expensive/large-scale tests against the entire organization if targeted testing is more appropriate.

Only report actual available results. If unavailable, say so.

---

## 11. Authentication / RADIUS

When authentication health is degraded, investigate automatically.

Evaluate:

- affected clients
- affected APs
- affected sites
- authentication type
- EAP vs FT/802.11r where available
- failure reasons
- status codes
- actual RADIUS server IP where exposed
- timeout/retry behavior
- affected vs healthy population

Do not infer RADIUS server selection from:

- configured server order
- randomization assumptions
- AP subnet
- NAS-Identifier alone

Compare failing and successful clients, APs, sites, and RADIUS server populations.

A RADIUS server succeeding elsewhere weakens a server-wide failure hypothesis but does not rule out intermittent, policy-specific, client-specific, or AP-specific failures.

---

## 12. RF Health

RF is cross-cutting evidence.

Evaluate where available:

- RSSI
- SNR
- noise
- channel utilization
- interference
- DFS
- channel
- channel width
- transmit power
- neighboring APs
- client distribution
- capacity

Do not claim RF is the root cause merely because RSSI is low or a destination AP has weaker RSSI.

Classify RF as:

- Confirmed contributor
- Strongly correlated
- Possible contributor
- Not supported
- Insufficient evidence

---

## 13. Roaming

Separate:

### Roam Decision

Why the client decided to leave the current AP.

Potential evidence:

- client roaming behavior
- RF degradation
- 802.11k
- 802.11v
- steering
- load balancing
- band preference
- explicit trigger telemetry

### Roam Execution

Whether the client successfully connected to the destination AP.

Evaluate:

- reassociation
- FT/802.11r
- PMK/PMKID
- authentication
- resulting connectivity

A successful AP-A → AP-B reassociation proves execution, not why the client chose AP-B.

Never assign a roam trigger without evidence.

---

## 14. AP Health

Identify APs requiring investigation based on correlated evidence.

Look for:

- AP down
- repeated reconnects
- repeated reboots
- client failures concentrated on AP
- authentication failures
- association failures
- RF anomalies
- roaming anomalies
- switchport issues
- firmware anomalies
- recent configuration changes

Use peer comparison:

- same-site APs
- same model
- same firmware
- same WLAN
- same switch infrastructure
- nearby APs

A reboot is not automatically a fault.

---

## 15. Client Experience

Identify population-level client problems.

Look for:

- repeated association failures
- authentication failures
- DHCP failures
- DNS failures
- repeated disconnects
- roaming failures
- poor RF
- poor performance
- high latency
- voice degradation

Determine whether scope is:

- client-specific
- AP-specific
- WLAN-specific
- site-specific
- infrastructure-wide

---

## 16. Change Detection — Last 24 Hours

Identify meaningful changes.

### Sites
- created
- deleted
- renamed
- modified
- template changed

### APs
- added
- removed
- replaced
- rebooted
- firmware changed
- configuration changed
- moved
- switchport changed

### WLANs
- created
- deleted
- modified
- security changes
- VLAN changes
- authentication changes
- 802.11r/FT changes
- PMF changes
- RF changes

### Network
- switch changes
- VLAN changes
- gateway changes
- DHCP changes
- RADIUS changes
- DNS changes

### Administrative
- configuration pushes
- template changes
- firmware operations
- major administrative changes

---

## 17. Change Correlation

Do not assume:

**Change before issue = root cause.**

For each potentially relevant change ask:

1. What changed?
2. When?
3. Which entities were affected?
4. Did the issue begin afterward?
5. Does the affected scope match the change scope?
6. Did healthy controls remain healthy?
7. Are unaffected entities exposed to the same change?
8. Is there independent evidence supporting causality?

Use:

- DIRECTLY CORRELATED
- STRONGLY CORRELATED
- TEMPORALLY CORRELATED
- WEAKLY CORRELATED
- NOT CORRELATED
- INSUFFICIENT EVIDENCE

Only identify a change as root cause when evidence supports it.

---

## 18. Alarms and Alerts

Review the last 24 hours.

Separate:

- active critical
- active degraded
- resolved
- recurring
- expected
- informational

For important alarms capture:

- timestamp
- site
- AP/device
- alarm type
- severity
- duration
- current state
- related clients
- related events
- related changes

An alarm is evidence, not automatically the root cause.

---

## 19. Known / Expected / No Action

Maintain a separate section.

Examples:

- scheduled maintenance
- planned firmware upgrades
- controlled AP replacement
- test-generated AP reboot
- administrative changes
- known vendor issue
- expected temporary outage
- resolved transient condition

Expected/test-generated events must not increase fault scoring.

---

## 20. Controlled Intervention Awareness

If a controlled test exists, establish an intervention context before RCA.

For example:

AP03 replaced with AP07.
AP07 placed in AP03's original location.
AP03 moved elsewhere.

If AP07 works in AP03's old location and the issue follows AP03 after relocation, this is strong evidence that the problem is AP-identity-specific rather than simply location/RF-specific.

However:

- do not claim the internal hardware/software mechanism unless proven
- treat test-generated reboot/configuration events as expected context
- do not flag the replacement AP merely because it rebooted during the test

Controlled intervention evidence should be weighted more strongly than simple temporal coincidence.

---

## 21. PCAP

If a useful PCAP URL is exposed by Mist telemetry:

**Automatically attempt retrieval before finalizing RCA.**

Do not require a second user prompt.

Workflow:

1. Detect PCAP availability.
2. Attempt retrieval.
3. Validate PCAP/PCAPNG.
4. Analyze if packet-analysis tooling is available.
5. Correlate packet evidence with Mist telemetry.
6. Update RCA confidence.

If retrieval is blocked, record:

**PCAP_RETRIEVAL_BLOCKED**

Continue the investigation.

If a PCAP is manually attached later, resume from validation → parsing → packet analysis → correlation.

Never expose signed PCAP URLs, JWTs, bearer tokens, or sensitive retrieval parameters.

---

## 22. Timeline and RCA Methodology

For each important issue identify:

### First abnormal observation
Earliest unusual signal.

### First observed failure
Earliest actual service failure.

### Earliest confirmed broken dependency
Earliest layer proven to have failed.

### Downstream symptoms
Failures resulting from the upstream condition.

### Contributors
Conditions that made the issue worse.

### Root cause
Only if evidence is strong enough.

If root cause cannot be proven:

**Root cause not conclusively established.**

Never force an RCA.

---

## 23. Minimum Differentiating Factor

A field is not causal merely because it differs.

Always search for counterexamples.

Ask:

**What is the smallest variable that consistently separates affected from healthy cases?**

Do not declare fields such as `time_since_assoc`, RSSI, AP uptime, or similar variables causal merely because they differ between two examples.

---

## 24. Healthy Controls

Use healthy controls for every major RCA.

Examples:

- suspect AP vs peer AP
- failing client on suspect AP vs same client on healthy AP
- failing authentication vs successful authentication
- affected site vs healthy peer site
- affected WLAN vs healthy WLAN

Do not generalize from one client/AP pair to an organization-wide problem without supporting evidence.

---

## 25. Evidence Classification

Every important conclusion must distinguish:

### CONFIRMED
Direct telemetry proves it.

### STRONGLY CORRELATED
Multiple independent observations support it.

### CORRELATED
A relationship exists but mechanism is not proven.

### POSSIBLE
Plausible but insufficient evidence.

### UNSUPPORTED
Available evidence does not support it.

### UNKNOWN
Required evidence is unavailable.

---

# DAILY COFFEE REPORT OUTPUT

The final report MUST follow this order.

# ☕ Juniper Mist Daily Coffee Report

Date:
Reporting Window:
Comparison Window:

---

## 1. 🏢 Organization Overview

Show:

- Sites
- APs
- Clients
- WLANs
- Overall health
- Critical alarms
- Degraded sites
- major SLE status
- network/application health

Keep this section compact.

---

## 2. 📊 SLE Summary

Show:

| SLE | Today | Yesterday | Trend | Assessment |
|---|---:|---:|---|---|

Highlight meaningful deterioration.

---

## 3. 🚨 Critical / Immediate Action

Show only issues requiring attention.

For each:

### [P1/P2] Issue Title

Severity:
Status:
Since:
Duration:
Affected Site(s):
Affected AP(s):
Affected Clients:
Affected WLAN:

**What happened**

**Current state**

**Earliest confirmed failure**

**Root cause**

**Correlation**

**Evidence**

**Confidence**

**Recommendation**

**Action required**

---

## 4. 🟠 Degraded Sites

| Site | Severity | Since | Primary Issue | APs | Clients | Current State | Action |
|---|---|---|---|---|---|---|---|

Only include meaningful degradation.

---

## 5. 🌐 Network & Application Experience

Summarize:

- DHCP
- ARP/Gateway
- DNS
- Internet
- application performance
- latency
- packet loss/jitter where available

---

## 6. 🧪 Marvis Mini / Synthetic Health

Show targeted synthetic results for suspicious/degraded sites.

Healthy sites may be summarized:

**38/42 sites passed available synthetic health checks.**

Do not flood the report with healthy results.

---

## 7. 📈 Key Metrics — Today vs Yesterday

| Metric | Today | Yesterday | Change | Interpretation |
|---|---:|---:|---:|---|

Do not highlight normal statistical noise.

---

## 8. 🔄 What Changed — Last 24 Hours

Group:

- Sites
- APs
- WLANs
- Network
- Authentication
- Administrative/configuration

Highlight changes that correlate with degradation.

---

## 9. 🚨 Alarms & Alerts

Separate:

- active critical
- active degraded
- recurring
- resolved
- informational

---

## 10. 🧠 Operational Insights

Answer:

**"What would I tell another network engineer over coffee?"**

Examples:

- Authentication failures increased significantly and are concentrated at two APs.
- Application latency increased at one site while wireless SLE remained healthy.
- AP restart frequency increased, but most restarts correlate with planned maintenance.
- Roaming health is stable organization-wide.
- DNS failures increased but DHCP and gateway health remain normal.

These must be evidence-driven.

---

## 11. 🟢 Known / Expected — No Action

List:

- planned maintenance
- scheduled upgrades
- controlled testing
- expected reboots
- administrative changes
- known issues

Explain why no action is required.

---

## 12. 🔍 Detailed Investigations

For each important P1/P2 issue provide:

### Issue
### Severity
### Status
### Since When
### Affected Scope
### What Happened
### Timeline
### Current State
### Earliest Confirmed Failure
### Root Cause
### Correlation
### Supporting Evidence
### Healthy Controls
### PCAP Evidence
### Confidence
### Recommendation
### Action Required

If PCAP is unavailable, explicitly state:

**PCAP unavailable/blocked.**

---

## 13. 📋 Action Plan

### P1 — Immediate
1. ...

### P2 — Today
1. ...

### P3 — Monitor
1. ...

Recommendations must be specific and evidence-based.

Bad:
"Check the AP."

Good:
"Investigate AP03 because full-EAP failures are concentrated on AP03 while peer AP04 succeeds for the same WLAN/client population."

---

## 14. 📈 Early Warning / Trending Issues

Identify problems that are not yet critical but are worsening.

Examples:

- rising authentication failures
- increasing AP restarts
- rising RF utilization
- increasing disconnects
- increasing DNS failures
- increasing application latency
- site gradually degrading

Classify:

- TRENDING UP
- TRENDING DOWN
- STABLE
- NO BASELINE

---

## 15. 🔍 Data Gaps

Explicitly state missing evidence.

Examples:

- PCAP unavailable
- historical baseline unavailable
- RADIUS transaction details unavailable
- incomplete client timeline
- Marvis Mini result unavailable
- configuration audit unavailable

Missing evidence must never be converted into assumptions.

---

# REPORT LENGTH PRINCIPLE

The report is a "daily cup of coffee" report.

The first 20–30% should provide approximately 80–90% of the operational picture.

The engineer should be able to:

- understand organization health in ~30 seconds
- identify critical issues in ~2 minutes
- understand important RCA/action items in ~5–10 minutes

Do not bury critical issues beneath raw telemetry.

Do not list every healthy AP/client/event.

Use aggregation for healthy/normal conditions.

Use detail for abnormal/high-impact conditions.

---

# INVESTIGATION QUALITY GATE

Before finalizing:

[ ] Organization snapshot collected.

[ ] SLE health reviewed.

[ ] Today vs yesterday comparison performed.

[ ] Critical alarms reviewed.

[ ] Recent changes reviewed.

[ ] Site health evaluated.

[ ] AP health evaluated.

[ ] Client experience evaluated.

[ ] Authentication/RADIUS evaluated where relevant.

[ ] DHCP evaluated where relevant.

[ ] ARP/gateway evaluated where relevant.

[ ] DNS evaluated where relevant.

[ ] Application/network performance evaluated where relevant.

[ ] RF evaluated where relevant.

[ ] Roaming evaluated where relevant.

[ ] Marvis Mini results retrieved/used for suspicious sites where available.

[ ] PCAP automatically attempted where a useful PCAP exists.

[ ] Controlled intervention context applied where present.

[ ] Test-generated events excluded from fault scoring.

[ ] Healthy controls used for important RCA.

[ ] Timeline constructed for critical issues.

[ ] Earliest confirmed broken dependency identified.

[ ] Root cause separated from correlation.

[ ] Confidence assigned.

[ ] Known/expected conditions separated from actionable issues.

[ ] Recommendations are specific.

[ ] No unsupported root-cause claims.

[ ] No fabricated metrics or historical values.

[ ] No signed URLs, JWTs, bearer tokens, or credentials exposed.

[ ] No unnecessary IPv6/DHCPv6 investigation.

---

# FINAL BEHAVIORAL RULES

The Daily Hygiene Check MUST NOT:

- fabricate telemetry
- fabricate historical comparisons
- treat every alarm as a root cause
- treat every AP reboot as a fault
- infer RADIUS server selection
- infer AP identity from shared NAS information
- blame RF from RSSI alone
- call a site-wide issue from one client
- call an AP faulty from one event
- blame RADIUS globally when healthy transactions exist
- blame 802.11r merely because FT appears in a failure
- blame DHCP/DNS when the failure occurs earlier
- treat downstream symptoms as root cause
- confuse roam decision with roam execution
- assume temporal correlation proves causality
- force a root cause when evidence is insufficient
- require a magic follow-up prompt to perform deeper investigation
- expose PCAP signed URLs or credentials
- allow known/test-generated events to inflate fault scoring

The orchestrator should automatically perform the appropriate evidence collection and investigation.

The user should not need to say:

"Now check alarms."

"Now check RADIUS."

"Now check DHCP."

"Now check changes."

"Now investigate this site."

The Daily Hygiene workflow owns the complete investigation.

---

# CORE OBJECTIVE

At the end of every daily run, the report must answer:

**What is healthy?**

**What is degraded?**

**What is broken?**

**What changed?**

**What started today?**

**What has been ongoing?**

**Who/what is affected?**

**What is the earliest confirmed failure?**

**What is the likely root cause?**

**What evidence supports it?**

**What is only correlation or speculation?**

**What requires immediate action?**

**What should be monitored?**

**What is known/expected and requires no action?**

The result should feel like an experienced wireless/network engineer reviewed the Juniper Mist organization overnight and handed the NOC a concise, evidence-backed morning briefing.
