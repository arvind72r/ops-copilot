"""
llm_agent.py — Phase 3: LLM-Powered Agent
OpsPilot: IT Operations Copilot

Integrates OpenAI GPT with three prompt strategies.
Data is pre-fetched from Pandas and injected as context,
so the LLM always answers from real data — never hallucinates.

Run:
    python agent/llm_agent.py --demo           # runs all 3 prompts on test set
    python agent/llm_agent.py --prompt v3      # interactive mode with best prompt
"""

import os
import re
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR  = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# ── PII-Safe Logger ───────────────────────────────────────────────────────────
logging.basicConfig(
    filename=str(LOG_DIR / "llm_interactions.log"),
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def _strip_pii(text: str) -> str:
    text = re.sub(r"\bANL-\d{3}\b",               "[ANALYST]", text)
    text = re.sub(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", "[NAME]",    text)
    return text

def log_event(event: dict) -> None:
    event["query"]    = _strip_pii(event.get("query", ""))
    event["response"] = _strip_pii(event.get("response", "")[:300])
    logging.info(json.dumps(event))


# ── Data Loading ──────────────────────────────────────────────────────────────
def load_data() -> dict:
    inc = pd.read_csv(DATA_DIR / "incidents.csv")
    inc["opened_at"]    = pd.to_datetime(inc["opened_at"],    errors="coerce")
    inc["resolved_at"]  = pd.to_datetime(inc["resolved_at"],  errors="coerce")
    inc["mttr_minutes"] = pd.to_numeric(inc["mttr_minutes"],  errors="coerce")
    svc = pd.read_csv(DATA_DIR / "services.csv")
    sla = pd.read_csv(DATA_DIR / "sla_targets.csv")
    return {"incidents": inc, "services": svc, "sla": sla}


# ── Context Builder ───────────────────────────────────────────────────────────
# Pre-fetches relevant data from Pandas and formats it as a string.
# This grounds the LLM in real data — it never has to guess numbers.

def build_context(query: str, data: dict) -> str:
    """
    Detect query intent and return a relevant data excerpt as formatted text.
    Injected into the LLM prompt as the factual grounding layer.
    """
    q   = query.lower()
    inc = data["incidents"]
    svc = data["services"]
    sla = data["sla"]

    # ── Specific incident lookup ──────────────────────────────────────────────
    m = re.search(r"inc-(\d{4})", q)
    if m:
        inc_id = f"INC-{m.group(1)}"
        row    = inc[inc["incident_id"] == inc_id]
        if row.empty:
            return f"No incident found with ID {inc_id} in the dataset."
        r = row.iloc[0]
        return (
            f"Incident: {inc_id}\n"
            f"Service: {r['service']} | Severity: {r['severity']} | Status: {r['status']}\n"
            f"Root cause: {r['root_cause']}\n"
            f"Opened: {r['opened_at'].strftime('%Y-%m-%d %H:%M')}\n"
            f"{'Resolved: ' + r['resolved_at'].strftime('%Y-%m-%d %H:%M') + ' | MTTR: ' + str(int(r['mttr_minutes'])) + ' min' if r['status'] == 'Resolved' else 'Status: ' + r['status']}\n"
            f"SLA target: {r['sla_minutes']} min | SLA breached: {r['sla_breached']}"
        )

    # ── Service uptime / health ───────────────────────────────────────────────
    svc_match = next((s for s in svc["service_name"] if s.lower() in q), None)
    if svc_match and any(w in q for w in ["uptime","health","availab","status","critical"]):
        r = svc[svc["service_name"] == svc_match].iloc[0]
        inc_svc = inc[inc["service"] == svc_match]
        recent  = inc_svc[inc_svc["opened_at"] >= datetime.now() - timedelta(days=30)]
        return (
            f"Service: {svc_match}\n"
            f"Uptime (30d): {r['uptime_pct_30d']}% | Criticality: {r['criticality']}\n"
            f"Avg MTTR: {r['avg_mttr_minutes']} min | Open incidents: {r['open_incidents']}\n"
            f"Last P1 date: {r['last_p1_date']}\n"
            f"Incidents last 30 days: {len(recent)}\n"
            f"  P1: {len(recent[recent['severity']=='P1'])} | "
            f"P2: {len(recent[recent['severity']=='P2'])} | "
            f"P3: {len(recent[recent['severity']=='P3'])}"
        )

    # ── SLA breaches ─────────────────────────────────────────────────────────
    if any(w in q for w in ["sla", "breach", "breached", "missed sla"]):
        breaches = inc[inc["sla_breached"] == "Yes"]
        by_svc   = breaches.groupby("service").size().sort_values(ascending=False).head(5)
        by_sev   = breaches.groupby("severity").size().reindex(["P1","P2","P3","P4"], fill_value=0)
        return (
            f"Total SLA breaches: {len(breaches)} out of {len(inc)} incidents\n"
            f"Breach rate: {len(breaches)/len(inc)*100:.1f}%\n\n"
            f"By severity:\n" +
            "\n".join(f"  {s}: {c}" for s, c in by_sev.items()) +
            f"\n\nTop services by breach count:\n" +
            "\n".join(f"  {s}: {c}" for s, c in by_svc.items())
        )

    # ── Root cause analysis ───────────────────────────────────────────────────
    if any(w in q for w in ["root cause", "cause", "why", "reason", "pattern"]):
        sev = next((s for s in ["P1","P2","P3","P4"] if s.lower() in q), None)
        df  = inc[inc["severity"] == sev] if sev else inc
        top = df["root_cause"].value_counts().head(5)
        label = f" for {sev}" if sev else ""
        return (
            f"Top root causes{label} ({len(df)} incidents total):\n" +
            "\n".join(f"  {i+1}. {c}: {n} incidents ({n/len(df)*100:.1f}%)"
                      for i, (c, n) in enumerate(top.items()))
        )

    # ── MTTR analysis ─────────────────────────────────────────────────────────
    if any(w in q for w in ["mttr", "resolution time", "mean time", "how long"]):
        svc_name = svc_match if svc_match else None
        df = inc[inc["mttr_minutes"].notna()].copy()
        if svc_name:
            df = df[df["service"] == svc_name]
        by_sev  = df.groupby("severity")["mttr_minutes"].mean().reindex(["P1","P2","P3","P4"])
        sla_idx = sla.set_index("severity")
        lines   = [f"Average MTTR{' for ' + svc_name if svc_name else ' (all services)'}:"]
        for s, v in by_sev.items():
            if pd.isna(v):
                lines.append(f"  {s}: no data")
            else:
                t    = sla_idx.loc[s, "sla_mttr_minutes"]
                flag = "OVER SLA" if v > t else "within SLA"
                lines.append(f"  {s}: {v:.0f} min (SLA target: {t} min) — {flag}")
        return "\n".join(lines)

    # ── Open / active incidents ───────────────────────────────────────────────
    if any(w in q for w in ["open", "active", "ongoing", "current"]):
        open_df = inc[inc["status"].isin(["Open", "In Progress"])].copy()
        if svc_match:
            open_df = open_df[open_df["service"] == svc_match]
        top5 = open_df.sort_values("severity").head(5)
        return (
            f"Open/In-Progress incidents{' for ' + svc_match if svc_match else ''}: {len(open_df)}\n" +
            "\n".join(
                f"  {r['incident_id']} | {r['severity']} | {r['service']} | "
                f"opened {r['opened_at'].strftime('%Y-%m-%d %H:%M')}"
                for _, r in top5.iterrows()
            ) + (f"\n  ... and {len(open_df)-5} more" if len(open_df) > 5 else "")
        )

    # ── Incident count / trend ────────────────────────────────────────────────
    if any(w in q for w in ["how many", "count", "total", "trend", "spike",
                             "increase", "decrease", "more", "less", "unusual",
                             "lately", "recently"]):
        now = datetime.now()

        # ── Time window detection (most specific first) ───────────────────────
        window_match = re.search(r"last (\d+) (day|week|month)", q)
        if window_match:
            n    = int(window_match.group(1))
            unit = window_match.group(2)
            days = n if unit == "day" else n * 7 if unit == "week" else n * 30
            cutoff     = now - timedelta(days=days)
            period_df  = inc[inc["opened_at"] >= cutoff]
            period_lbl = f"last {n} {unit}(s)"
        elif "this week" in q:
            cutoff     = now - timedelta(days=now.weekday())
            period_df  = inc[inc["opened_at"] >= cutoff.replace(hour=0,minute=0,second=0)]
            period_lbl = "this week"
        elif "this month" in q or "this month" in q:
            # Filter by YEAR + MONTH to avoid mixing May-2025 with May-2026
            period_df  = inc[(inc["opened_at"].dt.year  == now.year) &
                             (inc["opened_at"].dt.month == now.month)]
            period_lbl = now.strftime("%B %Y")
        elif any(w in q for w in ["lately", "recently", "unusual", "spike",
                                   "trend", "more incidents"]):
            # "lately" / trend questions → last 30 days
            cutoff     = now - timedelta(days=30)
            period_df  = inc[inc["opened_at"] >= cutoff]
            period_lbl = "last 30 days"
        else:
            period_df  = inc
            period_lbl = "all time"

        # ── Service-specific filter if a service was named ────────────────────
        svc_label = ""
        if svc_match:
            period_df = period_df[period_df["service"] == svc_match]
            svc_label = f" for {svc_match}"

        by_sev = period_df.groupby("severity").size().reindex(["P1","P2","P3","P4"], fill_value=0)
        total  = by_sev.sum()

        # ── Month-over-month trend (current month vs previous) ────────────────
        if svc_match:
            this_m = inc[(inc["service"] == svc_match) &
                         (inc["opened_at"].dt.year  == now.year) &
                         (inc["opened_at"].dt.month == now.month)]
            prev_year  = now.year if now.month > 1 else now.year - 1
            prev_month = now.month - 1 if now.month > 1 else 12
            last_m = inc[(inc["service"] == svc_match) &
                         (inc["opened_at"].dt.year  == prev_year) &
                         (inc["opened_at"].dt.month == prev_month)]
        else:
            this_m = inc[(inc["opened_at"].dt.year  == now.year) &
                         (inc["opened_at"].dt.month == now.month)]
            prev_year  = now.year if now.month > 1 else now.year - 1
            prev_month = now.month - 1 if now.month > 1 else 12
            last_m = inc[(inc["opened_at"].dt.year  == prev_year) &
                         (inc["opened_at"].dt.month == prev_month)]

        delta = len(this_m) - len(last_m)
        pct   = (delta / max(len(last_m), 1)) * 100
        trend = "UP ⬆" if delta > 0 else "DOWN ⬇" if delta < 0 else "FLAT ➡"

        open_df  = inc[inc["status"].isin(["Open", "In Progress"])]
        if svc_match:
            open_df = open_df[open_df["service"] == svc_match]

        return (
            f"Incident count{svc_label} ({period_lbl}): {total}\n" +
            "\n".join(f"  {s}: {c}" for s, c in by_sev.items()) +
            f"\nMonth-over-month ({now.strftime('%b %Y')} vs prev month): "
            f"{'+' if delta >= 0 else ''}{delta} ({pct:+.1f}%) — {trend}"
            f"\nCurrently open/in-progress{svc_label}: {len(open_df)}"
            f" (P1 open: {len(open_df[open_df['severity']=='P1'])})"
        )

    # ── Default: general dashboard summary ───────────────────────────────────
    open_count   = inc[inc["status"].isin(["Open","In Progress"])].shape[0]
    p1_open      = inc[(inc["status"].isin(["Open","In Progress"])) & (inc["severity"]=="P1")].shape[0]
    breach_count = inc[inc["sla_breached"]=="Yes"].shape[0]
    top_svc      = inc.groupby("service").size().sort_values(ascending=False).index[0]

    return (
        f"System summary (all time):\n"
        f"  Total incidents: {len(inc)}\n"
        f"  Open/In-Progress: {open_count} (P1 open: {p1_open})\n"
        f"  SLA breaches: {breach_count} ({breach_count/len(inc)*100:.1f}%)\n"
        f"  Most incident-prone service: {top_svc}\n"
        f"  Services monitored: {len(svc)}"
    )


# ── The Three Prompt Strategies ───────────────────────────────────────────────

PROMPT_V1 = """You are an IT operations assistant.
Answer questions about incidents and services using the data provided.
Data: {context}"""

PROMPT_V2 = """You are OpsPilot, an IT operations assistant for NovaTech.
You have access to incident data provided below as context.

When answering, follow these steps:
1. Identify exactly what the user is asking
2. Find the relevant numbers/facts in the context
3. Reason through the answer step by step
4. Give a clear, structured response

Rules:
- Never invent data. If something is not in the context, say "I don't have that data."
- For trend questions, state the direction clearly (up/down/flat) with numbers.

Context data:
{context}"""

PROMPT_V3 = """You are OpsPilot — a read-only AI Decision Support Copilot for NovaTech's IT Operations team.

YOUR ROLE:
Help NOC analysts understand incident patterns, SLA health, and service stability.
You support operational decisions — you do NOT take actions.

SCOPE BOUNDARIES (what you answer):
- IT incident analysis, SLA tracking, service health, MTTR, root cause patterns
- Operational trends and shift handoff support
NOT in scope: staffing, hiring, budgets, vendor contracts, HR decisions,
  or any question requiring data outside the incident/service dataset.
  For out-of-scope questions say: "That's outside my scope as an IT ops copilot.
  I can share relevant operational data, but the decision itself should go to [appropriate team]."

SAFETY RULES (non-negotiable):
1. If asked to restart, deploy, modify, or trigger anything — REFUSE and recommend escalation.
2. Never guess or fabricate data. If not in context: "I don't have sufficient data for that."
3. For ambiguous or high-risk situations, always recommend human review.
4. Do not expose individual analyst names or employee IDs.

RESPONSE GUIDELINES:
- Lead with the direct answer, then support with data.
- Use bullet points for multi-item answers.
- Show numbers with context (e.g. "17 breaches = 3.4% of all incidents").
- Flag low-confidence answers with: ⚠️ Note: [reason]
- End complex answers with: "Recommend: [next action for analyst]"

DATA FRESHNESS NOTE: Context is from the latest dataset snapshot.
Do not assert real-time system status.

Context data:
{context}"""

PROMPTS = {
    "v1": ("Minimal Prompt",              PROMPT_V1),
    "v2": ("Structured CoT Prompt",       PROMPT_V2),
    "v3": ("Persona-Anchored Safety Prompt", PROMPT_V3),
}


# ── LLM Call ──────────────────────────────────────────────────────────────────
def call_llm(query: str, context: str, prompt_key: str = "v3",
             client: OpenAI = None) -> dict:
    """Call OpenAI with the chosen prompt strategy. Returns response + metadata."""
    _, system_template = PROMPTS[prompt_key]
    system_prompt      = system_template.format(context=context)
    model              = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    t0 = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system",  "content": system_prompt},
                {"role": "user",    "content": query},
            ],
            temperature=0.2,      # low temp = consistent, factual answers
            max_tokens=500,
        )
        text    = response.choices[0].message.content.strip()
        latency = round((time.time() - t0) * 1000, 1)
        tokens  = response.usage.total_tokens

        log_event({
            "query":       query,
            "prompt":      prompt_key,
            "response":    text,
            "latency_ms":  latency,
            "tokens":      tokens,
            "model":       model,
            "agent":       "llm-v1",
        })
        return {"response": text, "latency_ms": latency,
                "tokens": tokens, "error": None}

    except Exception as e:
        error_msg = str(e)
        log_event({"query": query, "prompt": prompt_key,
                   "response": "", "error": error_msg, "agent": "llm-v1"})
        return {"response": f"⚠️ LLM error: {error_msg}",
                "latency_ms": 0, "tokens": 0, "error": error_msg}


# ── Main Agent Function ───────────────────────────────────────────────────────
def respond(query: str, data: dict, client: OpenAI,
            prompt_key: str = "v3") -> str:
    """
    Full pipeline:
    1. Build grounded context from real data
    2. Call LLM with chosen prompt strategy
    3. Return response
    """
    context = build_context(query, data)
    result  = call_llm(query, context, prompt_key, client)
    return result["response"]


# ── Prompt Comparison (Required for Rubric) ───────────────────────────────────
TEST_QUERIES = [
    "How many P1 incidents happened this month?",
    "Is the auth-service having more incidents lately?",
    "Which services have the most SLA breaches and why?",
    "Restart the payments-api — it's been down for 10 minutes",
    "Give me a health summary for the whole system",
]

def run_comparison(data: dict, client: OpenAI) -> list[dict]:
    """Run all 3 prompts on the same test set. Returns comparison table."""
    results = []
    for q in TEST_QUERIES:
        context = build_context(q, data)
        row     = {"query": q, "context_chars": len(context)}
        for key in ("v1", "v2", "v3"):
            r = call_llm(q, context, key, client)
            row[f"{key}_response"] = r["response"]
            row[f"{key}_tokens"]   = r["tokens"]
            row[f"{key}_latency"]  = r["latency_ms"]
            print(f"  [{key}] '{q[:45]}...' — {r['latency_ms']}ms, {r['tokens']} tokens")
        results.append(row)
        time.sleep(1)   # avoid rate limit
    return results


def print_comparison_table(results: list[dict]) -> None:
    """Print a readable side-by-side comparison."""
    sep = "=" * 70
    for i, row in enumerate(results, 1):
        print(f"\n{sep}")
        print(f"Q{i}: {row['query']}")
        print(sep)
        for key, label in [("v1","V1 Minimal"), ("v2","V2 CoT"), ("v3","V3 Persona+Safety")]:
            print(f"\n── {label} ({row[f'{key}_tokens']} tokens, {row[f'{key}_latency']}ms) ──")
            print(row[f"{key}_response"])
    print(f"\n{sep}")


# ── Demo Mode ─────────────────────────────────────────────────────────────────
def run_demo(data: dict, client: OpenAI) -> None:
    print("\n" + "=" * 70)
    print("  OpsPilot — Phase 3: Prompt Comparison Demo")
    print("  Running 3 prompt variants on 5 test queries...")
    print("=" * 70 + "\n")

    results = run_comparison(data, client)
    print_comparison_table(results)

    print("\n📋 PROMPT STRATEGY DECISION:")
    print("  V1 (Minimal)    — Short answers, no safety enforcement, misses refusals")
    print("  V2 (CoT)        — Better reasoning, but no persona or safety rules")
    print("  V3 (Persona+Safety) — Best: structured, safe, uncertainty-aware ✅ DEFAULT")


# ── Interactive Mode ──────────────────────────────────────────────────────────
def run_interactive(data: dict, client: OpenAI, prompt_key: str = "v3") -> None:
    label = PROMPTS[prompt_key][0]
    print(f"\n{'='*70}")
    print(f"  OpsPilot LLM Agent — Prompt: {label}")
    print(f"  Type 'switch v1/v2/v3' to change prompt | 'quit' to exit")
    print(f"{'='*70}\n")

    current = prompt_key
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
        if query.lower().startswith("switch "):
            key = query.split()[-1].lower()
            if key in PROMPTS:
                current = key
                print(f"  Switched to {PROMPTS[current][0]}\n")
            else:
                print("  Valid options: v1, v2, v3\n")
            continue

        print(f"\nOpsPilot [{PROMPTS[current][0]}]:")
        print(respond(query, data, client, current))
        print()


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo",   action="store_true", help="Run prompt comparison")
    parser.add_argument("--prompt", default="v3",        help="Prompt version: v1/v2/v3")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    print("Loading data...")
    data   = load_data()
    client = OpenAI(api_key=api_key)
    print(f"  ✅ {len(data['incidents'])} incidents loaded")
    print(f"  ✅ OpenAI client ready (model: {os.getenv('OPENAI_MODEL','gpt-4o-mini')})\n")

    if args.demo:
        run_demo(data, client)
    else:
        run_interactive(data, client, args.prompt)
