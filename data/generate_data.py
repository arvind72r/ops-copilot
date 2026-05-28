"""
generate_data.py
Generates synthetic IT operations data for OpsPilot capstone.
Run once: python data/generate_data.py
"""

import csv
import random
import os
from datetime import datetime, timedelta

random.seed(42)

# ── Config ────────────────────────────────────────────────────────────────────
SERVICES = [
    "auth-service",
    "payments-api",
    "user-portal",
    "notification-service",
    "database-cluster",
    "api-gateway",
    "reporting-engine",
    "inventory-service",
    "search-service",
    "email-relay",
]

SEVERITIES = ["P1", "P2", "P3", "P4"]
SEV_WEIGHTS = [0.08, 0.22, 0.45, 0.25]   # realistic distribution

ROOT_CAUSES = [
    "Memory leak",
    "Database connection exhaustion",
    "Certificate expiry",
    "Upstream dependency failure",
    "Misconfiguration after deploy",
    "DDoS attack pattern",
    "Disk space exhaustion",
    "Network packet loss",
    "CPU spike from batch job",
    "Third-party API timeout",
]

STATUSES = ["Resolved", "Resolved", "Resolved", "Open", "In Progress"]

ANALYSTS = ["ANL-001", "ANL-002", "ANL-003", "ANL-004", "ANL-005"]   # anonymised

# SLA targets in minutes: P1=60, P2=240, P3=1440, P4=4320
SLA_MTTR = {"P1": 60, "P2": 240, "P3": 1440, "P4": 4320}

BASE_DATE = datetime(2025, 1, 1, 0, 0, 0)
END_DATE  = datetime(2026, 5, 28, 0, 0, 0)


# ── Helpers ───────────────────────────────────────────────────────────────────
def random_datetime(start: datetime, end: datetime) -> datetime:
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def mttr_minutes(severity: str, sla: int) -> int:
    """Return resolution time — ~30% breach SLA."""
    if random.random() < 0.30:
        return sla + random.randint(10, sla)    # breach
    return random.randint(max(5, sla // 4), sla - 5)


# ── Generate incidents.csv ────────────────────────────────────────────────────
incidents = []
for i in range(1, 501):
    sev   = random.choices(SEVERITIES, weights=SEV_WEIGHTS)[0]
    sla   = SLA_MTTR[sev]
    mttr  = mttr_minutes(sev, sla)
    svc   = random.choice(SERVICES)
    cause = random.choice(ROOT_CAUSES)
    opened = random_datetime(BASE_DATE, END_DATE)
    status = random.choice(STATUSES)
    if status in ("Resolved",) :
        resolved = opened + timedelta(minutes=mttr)
    else:
        resolved = ""
        mttr     = ""

    # Make auth-service and payments-api intentionally incident-heavy
    if i % 7 == 0:
        svc = "auth-service"
    if i % 11 == 0:
        svc = "payments-api"

    incidents.append({
        "incident_id":   f"INC-{i:04d}",
        "service":        svc,
        "severity":       sev,
        "status":         status,
        "root_cause":     cause,
        "opened_at":      opened.strftime("%Y-%m-%d %H:%M:%S"),
        "resolved_at":    resolved if resolved else "",
        "mttr_minutes":   mttr,
        "sla_minutes":    sla,
        "sla_breached":   "Yes" if mttr and int(mttr) > sla else "No",
        "assigned_to":    random.choice(ANALYSTS),
        "notes":          f"Incident on {svc}. Root cause: {cause}.",
    })

incidents_path = os.path.join(os.path.dirname(__file__), "incidents.csv")
with open(incidents_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=incidents[0].keys())
    writer.writeheader()
    writer.writerows(incidents)
print(f"✅  incidents.csv — {len(incidents)} rows → {incidents_path}")


# ── Generate services.csv ─────────────────────────────────────────────────────
services = []
for svc in SERVICES:
    uptime_pct = round(random.uniform(95.5, 99.99), 2)
    services.append({
        "service_name":      svc,
        "team_owner":        random.choice(["Platform", "Payments", "Identity", "Data", "Core"]),
        "environment":       "Production",
        "uptime_pct_30d":    uptime_pct,
        "avg_mttr_minutes":  random.randint(25, 180),
        "open_incidents":    random.randint(0, 4),
        "last_p1_date":      random_datetime(
            datetime(2025, 10, 1), END_DATE
        ).strftime("%Y-%m-%d"),
        "criticality":       random.choice(["Critical", "High", "Medium"]),
    })

services_path = os.path.join(os.path.dirname(__file__), "services.csv")
with open(services_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=services[0].keys())
    writer.writeheader()
    writer.writerows(services)
print(f"✅  services.csv  — {len(services)} rows → {services_path}")


# ── Generate sla_targets.csv ──────────────────────────────────────────────────
sla_rows = [
    {"severity": "P1", "sla_mttr_minutes": 60,   "description": "Critical — full outage"},
    {"severity": "P2", "sla_mttr_minutes": 240,  "description": "High — major degradation"},
    {"severity": "P3", "sla_mttr_minutes": 1440, "description": "Medium — partial impact"},
    {"severity": "P4", "sla_mttr_minutes": 4320, "description": "Low — cosmetic / minor"},
]

sla_path = os.path.join(os.path.dirname(__file__), "sla_targets.csv")
with open(sla_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=sla_rows[0].keys())
    writer.writeheader()
    writer.writerows(sla_rows)
print(f"✅  sla_targets.csv — {len(sla_rows)} rows → {sla_path}")


# ── Generate runbooks (text files for Phase 4 RAG) ────────────────────────────
runbooks_dir = os.path.join(os.path.dirname(__file__), "runbooks")
os.makedirs(runbooks_dir, exist_ok=True)

runbook_content = {
    "auth-service.txt": """
AUTH-SERVICE RUNBOOK — NovaTech IT Operations
==============================================
Service: auth-service
Owner: Identity Team
Criticality: Critical

COMMON FAILURE MODES
---------------------
1. Certificate Expiry
   - Symptom: 401 errors spike, login failures > 5%
   - Detection: Check cert expiry via: curl -vI https://auth.novatech.internal
   - Resolution: Rotate certificate via PKI portal (requires approval)
   - SLA: P1 — resolve within 60 minutes

2. Memory Leak
   - Symptom: Gradual latency increase, OOM kills in pods
   - Detection: Check pod memory via monitoring dashboard
   - Resolution: Rolling restart (requires ops-lead approval)
   - Recurrence: Seen after version deployments — check changelog

3. Database Connection Exhaustion
   - Symptom: 503 errors from auth endpoints, connection pool logs show max_conn hit
   - Detection: Query pg_stat_activity on auth-db
   - Resolution: Identify long-running queries, kill stale connections
   - Escalation: If not resolved in 20 min, page DB team

ESCALATION PATH
---------------
L1 → L2 (15 min) → Ops Lead (30 min) → Identity Team On-Call (45 min)

KNOWN RECURRING ISSUES
-----------------------
- Certificate renewal missed in Jan 2025 — resulted in 47-min P1
- Memory leak introduced in v3.2.1 — patch in v3.2.3
""",

    "payments-api.txt": """
PAYMENTS-API RUNBOOK — NovaTech IT Operations
=============================================
Service: payments-api
Owner: Payments Team
Criticality: Critical

COMMON FAILURE MODES
---------------------
1. Third-Party API Timeout
   - Symptom: Payment processing latency > 5s, timeout errors in logs
   - Detection: Check upstream gateway health dashboard
   - Resolution: Enable fallback payment processor (requires payments team)
   - SLA: P1 — resolve within 60 minutes

2. Database Connection Exhaustion
   - Symptom: Transaction failures, HTTP 500 on /process endpoint
   - Detection: Check connection pool metrics
   - Resolution: Scale connection pool or restart pool manager

3. DDoS Attack Pattern
   - Symptom: Request rate > 10x baseline, unusual geo distribution
   - Detection: WAF dashboard shows spike
   - Resolution: Enable rate limiting rules, page security team

ESCALATION PATH
---------------
L1 → L2 (10 min) → Payments On-Call (20 min) → Payments Engineering Lead (30 min)

IMPORTANT: Payments-api outages must be communicated to Finance team
within 15 minutes of P1 declaration.
""",

    "general_ops.txt": """
GENERAL OPERATIONS RUNBOOK — NovaTech IT Operations
====================================================

SLA DEFINITIONS
---------------
P1 (Critical): Full service outage or data loss risk. MTTR target: 60 minutes.
P2 (High): Major feature degraded. MTTR target: 4 hours.
P3 (Medium): Minor feature affected. MTTR target: 24 hours.
P4 (Low): Cosmetic or non-urgent. MTTR target: 72 hours.

ESCALATION PRINCIPLES
---------------------
1. If MTTR is projected to exceed SLA, escalate immediately — do not wait.
2. P1 incidents require Ops Lead notification within 10 minutes.
3. When root cause is unknown after 20 minutes, escalate to engineering.

SHIFT HANDOFF PROTOCOL
-----------------------
At end of each 12-hour shift, the outgoing analyst must:
1. List all open incidents with current status
2. Note any SLA-at-risk items
3. Identify any recurring patterns observed
4. Brief incoming analyst verbally + log summary in ops channel

DATA MODIFICATION POLICY
--------------------------
NOC Analysts do NOT have write access to production systems.
Any remediation action (restart, config change, rollback) requires:
- Ops Lead approval for P2/P3
- Ops Lead + Engineering approval for P1
All actions are taken by the on-call engineer, not the NOC analyst.
""",
}

for filename, content in runbook_content.items():
    path = os.path.join(runbooks_dir, filename)
    with open(path, "w") as f:
        f.write(content.strip())
    print(f"✅  runbooks/{filename}")

print("\n🎉  All synthetic data generated successfully.")
