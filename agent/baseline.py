"""
baseline.py — Phase 2: Rules-Based Baseline Agent
OpsPilot: IT Operations Copilot

This agent uses keyword matching and Pandas queries ONLY.
No LLM. No embeddings. Deliberately limited to expose gaps.

Run:
    python agent/baseline.py
"""

import os
import re
import json
import csv
import logging
from datetime import datetime, timedelta

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR   = os.path.join(BASE_DIR, "data")
LOG_DIR    = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ── PII-Safe Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "baseline_interactions.log"),
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def log_interaction(query: str, response: str, duration_ms: float) -> None:
    """Log query/response pair. Strip any analyst names or IDs."""
    safe_query    = _strip_pii(query)
    safe_response = _strip_pii(response[:200])          # truncate for log
    logging.info(
        json.dumps({
            "query":       safe_query,
            "response":    safe_response,
            "duration_ms": round(duration_ms, 1),
            "agent":       "baseline-v1",
        })
    )

def _strip_pii(text: str) -> str:
    """Remove analyst names and employee IDs from logged text."""
    text = re.sub(r"\bANL-\d{3}\b",          "[ANALYST]",  text)
    text = re.sub(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", "[NAME]", text)
    return text


# ── Data Loading ──────────────────────────────────────────────────────────────
def load_data() -> dict[str, pd.DataFrame]:
    """Load all CSV datasets once at startup."""
    incidents = pd.read_csv(os.path.join(DATA_DIR, "incidents.csv"))
    incidents["opened_at"]   = pd.to_datetime(incidents["opened_at"],   errors="coerce")
    incidents["resolved_at"] = pd.to_datetime(incidents["resolved_at"], errors="coerce")
    incidents["mttr_minutes"] = pd.to_numeric(incidents["mttr_minutes"], errors="coerce")

    services    = pd.read_csv(os.path.join(DATA_DIR, "services.csv"))
    sla_targets = pd.read_csv(os.path.join(DATA_DIR, "sla_targets.csv"))

    return {"incidents": incidents, "services": services, "sla": sla_targets}


# ── Keyword Matching ──────────────────────────────────────────────────────────
# Maps regex patterns → handler function names
RULES: list[tuple[str, str]] = [
    (r"\b(how many|count)\b.*(incident|p1|p2|p3|p4)", "count_incidents"),
    (r"\bsla\b|\bbreach(es|ed)?\b",                    "sla_breaches"),
    (r"\b(top|root|common)\b.*(cause|causes)\b",       "top_root_causes"),
    (r"\b(mttr|resolution time|mean time)\b",          "avg_mttr"),
    (r"\b(open|ongoing|active)\b.*(incident)",         "open_incidents"),
    (r"\b(uptime|availability)\b",                     "service_uptime"),
    (r"\b(restart|reboot|shutdown|kill|deploy|modif|change|update)\b",
                                                       "refuse_action"),
    (r"\b(help|what can you|commands)\b",              "show_help"),
    (r"\binc-\d{4}\b",                                 "lookup_incident"),
]

def match_rule(query: str) -> str:
    """Return handler name for first matching rule, else 'no_match'."""
    q = query.lower()
    for pattern, handler in RULES:
        if re.search(pattern, q):
            return handler
    return "no_match"


# ── Handlers ──────────────────────────────────────────────────────────────────
def count_incidents(query: str, data: dict) -> str:
    df = data["incidents"]

    # Extract severity if mentioned
    sev = None
    for s in ["P1", "P2", "P3", "P4"]:
        if s.lower() in query.lower():
            sev = s
            break

    # Extract time window — LIMITATION: only supports "last 30 days" or "last N days"
    window_match = re.search(r"last (\d+) day", query.lower())
    if window_match:
        days = int(window_match.group(1))
        cutoff = datetime.now() - timedelta(days=days)
        df = df[df["opened_at"] >= cutoff]
        period = f"last {days} days"
    else:
        period = "all time"

    if sev:
        df = df[df["severity"] == sev]
        return (
            f"📊 {sev} incidents ({period}): {len(df)}\n"
            f"   Resolved: {(df['status'] == 'Resolved').sum()}  |  "
            f"Open: {(df['status'] != 'Resolved').sum()}"
        )

    counts = df.groupby("severity").size().reindex(["P1","P2","P3","P4"], fill_value=0)
    lines  = [f"📊 Incident count ({period}):"]
    for sev_label, cnt in counts.items():
        lines.append(f"   {sev_label}: {cnt}")
    lines.append(f"   Total: {counts.sum()}")
    return "\n".join(lines)


def sla_breaches(query: str, data: dict) -> str:
    df  = data["incidents"]
    sev = None
    for s in ["P1", "P2", "P3", "P4"]:
        if s.lower() in query.lower():
            sev = s
            break

    breach_df = df[df["sla_breached"] == "Yes"]
    if sev:
        breach_df = breach_df[breach_df["severity"] == sev]

    by_svc = breach_df.groupby("service").size().sort_values(ascending=False).head(5)
    lines  = [f"⚠️  SLA Breaches{' (' + sev + ')' if sev else ''}: {len(breach_df)} total"]
    lines.append("   Top services by breach count:")
    for svc, cnt in by_svc.items():
        lines.append(f"   • {svc}: {cnt}")
    return "\n".join(lines)


def top_root_causes(query: str, data: dict) -> str:
    df  = data["incidents"]

    # Extract severity filter
    sev = None
    for s in ["P1", "P2", "P3", "P4"]:
        if s.lower() in query.lower():
            sev = s
            break
    if sev:
        df = df[df["severity"] == sev]

    top = df["root_cause"].value_counts().head(5)
    lines = [f"🔍 Top root causes{' for ' + sev if sev else ''}:"]
    for rank, (cause, cnt) in enumerate(top.items(), 1):
        lines.append(f"   {rank}. {cause} — {cnt} incidents")
    return "\n".join(lines)


def avg_mttr(query: str, data: dict) -> str:
    df  = data["incidents"]
    df  = df[df["mttr_minutes"].notna()]

    # Service filter
    svc = _extract_service(query, data)
    if svc:
        df = df[df["service"] == svc]
        label = svc
    else:
        label = "all services"

    by_sev = df.groupby("severity")["mttr_minutes"].mean().reindex(["P1","P2","P3","P4"])
    sla_df = data["sla"].set_index("severity")

    lines = [f"⏱️  Average MTTR for {label}:"]
    for sev_label, mttr_val in by_sev.items():
        if pd.isna(mttr_val):
            lines.append(f"   {sev_label}: no data")
            continue
        target = sla_df.loc[sev_label, "sla_mttr_minutes"]
        flag   = "⛔ over SLA" if mttr_val > target else "✅ within SLA"
        lines.append(f"   {sev_label}: {mttr_val:.0f} min (target {target} min) {flag}")
    return "\n".join(lines)


def open_incidents(query: str, data: dict) -> str:
    df   = data["incidents"]
    open_df = df[df["status"].isin(["Open", "In Progress"])]

    svc = _extract_service(query, data)
    if svc:
        open_df = open_df[open_df["service"] == svc]
        label   = svc
    else:
        label   = "all services"

    if open_df.empty:
        return f"✅ No open incidents for {label}."

    lines = [f"🔴 Open incidents for {label}: {len(open_df)}"]
    for _, row in open_df.head(5).iterrows():
        lines.append(
            f"   • {row['incident_id']} | {row['severity']} | {row['service']} "
            f"| opened {row['opened_at'].strftime('%Y-%m-%d %H:%M')}"
        )
    if len(open_df) > 5:
        lines.append(f"   ... and {len(open_df) - 5} more.")
    return "\n".join(lines)


def service_uptime(query: str, data: dict) -> str:
    df  = data["services"]
    svc = _extract_service(query, data)
    if svc:
        row = df[df["service_name"] == svc]
        if row.empty:
            return f"⚠️  No uptime data found for '{svc}'."
        r = row.iloc[0]
        return (
            f"📡 {svc} uptime (30d): {r['uptime_pct_30d']}%\n"
            f"   Avg MTTR: {r['avg_mttr_minutes']} min | "
            f"Open incidents: {r['open_incidents']} | "
            f"Criticality: {r['criticality']}"
        )
    # All services
    lines = ["📡 Service uptime summary (30d):"]
    for _, r in df.sort_values("uptime_pct_30d").iterrows():
        flag = "⚠️ " if r["uptime_pct_30d"] < 99.0 else "  "
        lines.append(f"   {flag}{r['service_name']}: {r['uptime_pct_30d']}%")
    return "\n".join(lines)


def lookup_incident(query: str, data: dict) -> str:
    match = re.search(r"inc-(\d{4})", query.lower())
    if not match:
        return "❓ Could not extract incident ID. Format: INC-XXXX"
    inc_id = f"INC-{match.group(1)}"
    df     = data["incidents"]
    row    = df[df["incident_id"] == inc_id]
    if row.empty:
        return f"⚠️  Incident {inc_id} not found in dataset."
    r = row.iloc[0]
    status_line = (
        f"Resolved at {r['resolved_at'].strftime('%Y-%m-%d %H:%M')} "
        f"(MTTR: {int(r['mttr_minutes'])} min)"
        if r["status"] == "Resolved" else f"Status: {r['status']}"
    )
    return (
        f"📋 {inc_id}\n"
        f"   Service  : {r['service']}\n"
        f"   Severity : {r['severity']}\n"
        f"   Root cause: {r['root_cause']}\n"
        f"   Opened   : {r['opened_at'].strftime('%Y-%m-%d %H:%M')}\n"
        f"   {status_line}\n"
        f"   SLA breach: {r['sla_breached']}"
    )


def refuse_action(query: str, data: dict) -> str:
    return (
        "🚫 I'm a read-only Decision Support copilot.\n"
        "   I cannot restart services, modify configuration, "
        "trigger deployments, or take any operational action.\n"
        "   Please escalate to your on-call engineer.\n"
        "   I can help you analyse incident history or SLA data "
        "to support your decision."
    )


def show_help(query: str, data: dict) -> str:
    return (
        "🤖 OpsPilot Baseline — What I can answer:\n"
        "   • How many incidents / P1s / P2s in the last N days?\n"
        "   • Which services had SLA breaches?\n"
        "   • What are the top root causes?\n"
        "   • What is the average MTTR for [service]?\n"
        "   • Are there open incidents for [service]?\n"
        "   • What is the uptime for [service]?\n"
        "   • Tell me about INC-XXXX\n\n"
        "⚠️  LIMITATIONS (Baseline v1):\n"
        "   1. Only understands exact keywords — misses paraphrases\n"
        "   2. Cannot reason across multiple questions at once\n"
        "   3. Cannot explain WHY a trend exists\n"
        "   4. Cannot handle ambiguous time ranges (e.g. 'recently')\n"
        "   5. No memory — each query is independent"
    )


def no_match(query: str, data: dict) -> str:
    return (
        "❓ I didn't understand that query.\n"
        "   This is a known limitation of the baseline agent — "
        "it uses keyword rules and cannot handle paraphrases or complex questions.\n"
        "   Type 'help' to see supported question types."
    )


# ── Helper ────────────────────────────────────────────────────────────────────
def _extract_service(query: str, data: dict) -> str | None:
    """Detect service name mentioned in query."""
    for svc in data["services"]["service_name"].tolist():
        if svc.lower() in query.lower():
            return svc
    return None


# ── Dispatcher ────────────────────────────────────────────────────────────────
HANDLERS = {
    "count_incidents":  count_incidents,
    "sla_breaches":     sla_breaches,
    "top_root_causes":  top_root_causes,
    "avg_mttr":         avg_mttr,
    "open_incidents":   open_incidents,
    "service_uptime":   service_uptime,
    "lookup_incident":  lookup_incident,
    "refuse_action":    refuse_action,
    "show_help":        show_help,
    "no_match":         no_match,
}


def respond(query: str, data: dict) -> str:
    """Route query to handler and return response."""
    t0      = datetime.now()
    handler = match_rule(query)
    fn      = HANDLERS[handler]
    result  = fn(query, data)
    ms      = (datetime.now() - t0).total_seconds() * 1000
    log_interaction(query, result, ms)
    return result


# ── Demo runs (for evidence / screenshots) ────────────────────────────────────
DEMO_QUERIES = [
    # ── Normal queries ──────────────────────────────────────────────────────
    "How many P1 incidents in the last 30 days?",
    "Which services had the most SLA breaches?",
    "What are the top root causes for P1 incidents?",
    "What is the average MTTR for auth-service?",
    "Are there any open incidents for payments-api?",
    "Tell me about INC-0042",
    "What is the uptime for database-cluster?",
    # ── Safety tests ────────────────────────────────────────────────────────
    "Restart the auth-service immediately",
    "Deploy the hotfix to payments-api",
    # ── Limitation demos ────────────────────────────────────────────────────
    "Is the system behaving unusually lately?",     # LIMITATION 1: vague / no keyword match
    "Give me a full health report for this week",   # LIMITATION 2: multi-part, no template
    "Why did payments-api spike last Tuesday?",     # LIMITATION 3: causal reasoning
]


def run_demo(data: dict) -> None:
    """Run all demo queries and print results."""
    print("\n" + "=" * 65)
    print("  OpsPilot — Phase 2 Baseline Agent Demo")
    print("  NovaTech IT Operations Copilot (Read-Only, Rules-Based)")
    print("=" * 65)

    for i, q in enumerate(DEMO_QUERIES, 1):
        print(f"\n[Q{i:02d}] {q}")
        print("-" * 55)
        print(respond(q, data))

    print("\n" + "=" * 65)
    print("  LIMITATIONS DEMONSTRATED:")
    print("  L1 — Vague queries ('lately', 'recently') → no_match")
    print("  L2 — Multi-part questions → no_match or partial answer")
    print("  L3 — Causal reasoning ('Why did X happen?') → no_match")
    print("  L4 — No memory: each query is stateless")
    print("  L5 — Cannot explain uncertainty, just returns no_match")
    print("=" * 65 + "\n")


# ── Interactive REPL ─────────────────────────────────────────────────────────
def run_interactive(data: dict) -> None:
    print("\n" + "=" * 65)
    print("  OpsPilot — Phase 2 Baseline Interactive Mode")
    print("  Type 'demo' to run all demo queries")
    print("  Type 'quit' or 'exit' to stop")
    print("=" * 65 + "\n")

    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit"):
            print("Goodbye.")
            break
        if query.lower() == "demo":
            run_demo(data)
            continue

        print(f"\nOpsPilot:\n{respond(query, data)}\n")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    print("Loading data...")
    data = load_data()
    print(f"  ✅ {len(data['incidents'])} incidents | "
          f"{len(data['services'])} services | "
          f"{len(data['sla'])} SLA rules loaded\n")

    if "--demo" in sys.argv:
        run_demo(data)
    else:
        run_interactive(data)
