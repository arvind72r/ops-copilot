"""
Phase 5 — Tool-Using Agent
OpsPilot: LangChain AgentExecutor wired to 4 structured, read-only tools.

Tools
-----
query_incidents      — incident counts / MTTR / trends
check_sla_breaches   — SLA compliance rates
get_service_health   — per-service health snapshot
search_runbook       — ChromaDB knowledge-base retrieval

Usage
-----
    from agent.tool_agent import init_agent_data, build_agent, run_query

    init_agent_data(incidents_df, sla_df, chroma_collection)
    executor = build_agent(api_key=os.environ["OPENAI_API_KEY"])
    result   = run_query(executor, "Is auth-service healthy?")
    print(result["response"])
"""

import json
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# ─── Module-level data store ──────────────────────────────────────────────────
# Populated once via init_agent_data() before any tool is called.

_incidents_df: Optional[pd.DataFrame] = None
_sla_targets: Optional[pd.DataFrame] = None
_collection = None   # ChromaDB collection; optional

KNOWN_SERVICES = [
    "auth-service", "payments-api", "user-portal", "notification-service",
    "database-cluster", "api-gateway", "reporting-engine", "inventory-service",
    "search-service", "email-relay",
]
KNOWN_PRIORITIES = ["P1", "P2", "P3", "P4"]


def init_agent_data(
    incidents: pd.DataFrame,
    sla_targets: pd.DataFrame,
    collection=None,
) -> None:
    """
    Inject data dependencies into the global tool layer.
    Must be called once before build_agent() / run_query().
    """
    global _incidents_df, _sla_targets, _collection
    _incidents_df = incidents.copy()
    _incidents_df["created_at"] = pd.to_datetime(_incidents_df["created_at"])
    _sla_targets = sla_targets
    _collection = collection


# ─── Tool 1: Query Incidents ──────────────────────────────────────────────────

@tool
def query_incidents(service: str = "", priority: str = "", days: int = 30) -> str:
    """
    Query incident records and return a summary matching the given filters.
    Use this tool for questions about incident counts, MTTR, open incidents,
    recent trends, or patterns by service or priority level.

    Args:
        service: Service name to filter on (e.g. 'auth-service', 'payments-api').
                 Leave empty to query all services.
        priority: Priority level to filter on: 'P1', 'P2', 'P3', or 'P4'.
                  Leave empty to include all priorities.
        days: How many past days to look at (default 30, max 365).

    Returns JSON with: total count, breakdown by priority/status,
    average MTTR hours, and count of open incidents.
    READ-ONLY — does not modify any data.
    """
    if _incidents_df is None:
        return "Error: data not initialised. Call init_agent_data() first."

    df = _incidents_df.copy()

    if service:
        svc = service.lower().strip()
        if svc not in KNOWN_SERVICES:
            return (
                f"Unknown service '{svc}'. "
                f"Valid services: {', '.join(KNOWN_SERVICES)}"
            )
        df = df[df["service"] == svc]

    if priority:
        pri = priority.upper().strip()
        if pri not in KNOWN_PRIORITIES:
            return f"Invalid priority '{priority}'. Valid values: P1, P2, P3, P4."
        df = df[df["priority"] == pri]

    days = min(max(int(days), 1), 365)
    cutoff = datetime.now() - timedelta(days=days)
    df = df[df["created_at"] >= cutoff]

    if df.empty:
        svc_part = f" for {service.lower().strip()}" if service else ""
        pri_part = f" with priority {priority.upper()}" if priority else ""
        return f"No incidents found{svc_part}{pri_part} in the last {days} days."

    by_priority = df["priority"].value_counts().to_dict()
    by_status   = df["status"].value_counts().to_dict()
    resolved    = df[df["resolution_time_hours"] > 0]
    avg_mttr    = resolved["resolution_time_hours"].mean() if not resolved.empty else None

    return json.dumps({
        "total_incidents":   len(df),
        "time_window_days":  days,
        "service_filter":    service.lower().strip() if service else "all",
        "priority_filter":   priority.upper() if priority else "all",
        "by_priority":       by_priority,
        "by_status":         by_status,
        "avg_mttr_hours":    round(avg_mttr, 1) if avg_mttr is not None else "N/A",
        "open_count":        int(by_status.get("open", 0)),
    }, indent=2)


# ─── Tool 2: Check SLA Breaches ───────────────────────────────────────────────

@tool
def check_sla_breaches(service: str = "", priority: str = "") -> str:
    """
    Check SLA breach status across all (or filtered) incidents.
    Use this tool for questions about SLA compliance, breach rates,
    which services are breaching SLAs most, or overall SLA health.

    Args:
        service: Filter to a specific service. Empty = all services.
        priority: Filter to a specific priority level. Empty = all priorities.

    Returns JSON with: total incidents checked, breach count, breach rate (%),
    and a ranked list of the top 5 services by breach rate.
    READ-ONLY — does not modify any data.
    """
    if _incidents_df is None:
        return "Error: data not initialised. Call init_agent_data() first."

    df = _incidents_df.copy()

    if service:
        svc = service.lower().strip()
        if svc not in KNOWN_SERVICES:
            return (
                f"Unknown service '{svc}'. "
                f"Valid services: {', '.join(KNOWN_SERVICES)}"
            )
        df = df[df["service"] == svc]

    if priority:
        pri = priority.upper().strip()
        if pri not in KNOWN_PRIORITIES:
            return f"Invalid priority '{priority}'. Valid values: P1, P2, P3, P4."
        df = df[df["priority"] == pri]

    if df.empty:
        return "No incidents found for the specified filters."

    breached = df[df["sla_breached"] == True]

    top_breach = (
        df.groupby("service")
        .apply(lambda x: round(x["sla_breached"].sum() / len(x) * 100, 1))
        .sort_values(ascending=False)
        .head(5)
        .to_dict()
    )

    return json.dumps({
        "total_incidents_checked":         len(df),
        "total_breached":                  len(breached),
        "breach_rate_pct":                 round(len(breached) / len(df) * 100, 1),
        "service_filter":                  service.lower().strip() if service else "all",
        "priority_filter":                 priority.upper() if priority else "all",
        "top_5_breach_rate_by_service":    top_breach,
    }, indent=2)


# ─── Tool 3: Get Service Health ────────────────────────────────────────────────

@tool
def get_service_health(service: str) -> str:
    """
    Return a health snapshot for a specific service.
    Use this tool for 'is X healthy?', 'what's the status of X service?',
    service health checks, or per-service dashboard queries.

    Args:
        service: Name of the service to assess (required).
                 E.g. 'auth-service', 'payments-api', 'database-cluster'.

    Returns JSON with: health status (HEALTHY / DEGRADED / CRITICAL),
    open incident count, recent P1/P2 count (30 days), all-time total,
    SLA breach rate (%), and average MTTR in hours.
    READ-ONLY — does not modify any data.
    """
    if _incidents_df is None:
        return "Error: data not initialised. Call init_agent_data() first."

    svc = service.lower().strip()
    if svc not in KNOWN_SERVICES:
        return (
            f"Unknown service '{svc}'. "
            f"Valid services: {', '.join(KNOWN_SERVICES)}"
        )

    df      = _incidents_df[_incidents_df["service"] == svc].copy()
    cutoff  = datetime.now() - timedelta(days=30)
    recent  = df[df["created_at"] >= cutoff]

    open_inc        = df[df["status"] == "open"]
    critical_recent = recent[recent["priority"].isin(["P1", "P2"])]
    breach_rate     = df["sla_breached"].mean() * 100
    resolved        = df[df["resolution_time_hours"] > 0]
    avg_mttr        = resolved["resolution_time_hours"].mean() if not resolved.empty else None

    if len(open_inc) == 0 and breach_rate < 20:
        health = "HEALTHY"
    elif len(open_inc) <= 2 and breach_rate < 40:
        health = "DEGRADED"
    else:
        health = "CRITICAL"

    return json.dumps({
        "service":               svc,
        "health_status":         health,
        "open_incidents":        len(open_inc),
        "recent_p1_p2_30d":      len(critical_recent),
        "all_time_total":        len(df),
        "sla_breach_rate_pct":   round(breach_rate, 1),
        "avg_mttr_hours":        round(avg_mttr, 1) if avg_mttr is not None else "N/A",
    }, indent=2)


# ─── Tool 4: Search Runbook / Knowledge Base ──────────────────────────────────

@tool
def search_runbook(query: str) -> str:
    """
    Search the operational knowledge base for runbooks, escalation guides,
    and SLA procedures.
    Use this tool for questions about: how to handle an incident type,
    escalation steps, known issues, mitigation actions, on-call procedures.

    Args:
        query: Natural language description of the procedure or guidance needed.
               E.g. 'escalation steps for P1 auth-service outage'.

    Returns relevant runbook excerpts with source filename and similarity score.
    Returns a 'not found' message if nothing meets the relevance threshold (0.25).
    READ-ONLY — does not modify any data.
    """
    if _collection is None:
        return (
            "Knowledge base not available (ChromaDB collection not initialised). "
            "Structured incident data is still accessible via other tools."
        )

    results = _collection.query(
        query_texts=[query],
        n_results=3,
        include=["documents", "distances", "metadatas"],
    )

    docs      = results["documents"][0]
    distances = results["distances"][0]
    metadatas = results["metadatas"][0]

    hits = []
    for doc, dist, meta in zip(docs, distances, metadatas):
        similarity = round(1 - dist / 2, 3)
        if similarity >= 0.25:
            hits.append({
                "similarity": similarity,
                "source":     meta.get("filename", meta.get("source", "unknown")),
                "excerpt":    doc[:350] + "…" if len(doc) > 350 else doc,
            })

    if not hits:
        return (
            "No relevant runbook content found above the similarity threshold (0.25). "
            "This topic may not be covered in the current knowledge base."
        )

    lines = [f"Found {len(hits)} relevant runbook section(s):\n"]
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] Source: {h['source']}  |  Similarity: {h['similarity']}")
        lines.append(h["excerpt"])
        lines.append("")
    return "\n".join(lines)


# ─── Registered tool list ─────────────────────────────────────────────────────

TOOLS = [query_incidents, check_sla_breaches, get_service_health, search_runbook]


# ─── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are OpsPilot — a read-only AI Decision Support Copilot for NovaTech IT Operations.

AVAILABLE TOOLS:
  • query_incidents      — incident counts, MTTR, trends by service/priority/time window
  • check_sla_breaches   — SLA compliance rates and breach analysis
  • get_service_health   — health snapshot for a specific service
  • search_runbook       — escalation steps, known issues, runbook procedures

TOOL USE RULES:
- Choose the most specific tool for each question.
- If a query spans multiple areas (e.g. health + runbook), call each tool once in sequence.
- Pass only the required arguments; rely on defaults where sensible.
- Do not call tools just to confirm data you already have.

SAFETY RULES:
- You are READ-ONLY. Never suggest, simulate, or perform any system-modifying action.
- Never fabricate numbers. If a tool returns no results, state that clearly.
- Cite which tool produced which data point.
- Use ⚠️ to flag uncertainty, stale data, or partial results.
- Action requests (restart, deploy, rollback, kill, scale): refuse and recommend escalation.

SCOPE BOUNDARIES:
- In scope: IT incident analysis, SLA tracking, service health, MTTR, root cause patterns,
  escalation procedures, shift handoff support.
- Out of scope: staffing levels, hiring, budgets, vendor contracts, HR decisions.
  For out-of-scope questions: "That's outside my scope as an IT ops copilot."

RESPONSE FORMAT:
- Lead with the direct answer.
- Bullet points for multi-part responses.
- Cite tool name for key facts (e.g. "per query_incidents…").
- End complex analyses with: Recommend: [next action for the human analyst]
"""


# ─── Agent Factory ────────────────────────────────────────────────────────────

def build_agent(api_key: str, verbose: bool = False) -> AgentExecutor:
    """
    Build and return a LangChain AgentExecutor wired to all 4 tools.

    Args:
        api_key: OpenAI API key string.
        verbose: Print LangChain's internal reasoning trace if True.
    """
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, api_key=api_key)

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_openai_tools_agent(llm, TOOLS, prompt)

    return AgentExecutor(
        agent=agent,
        tools=TOOLS,
        verbose=verbose,
        max_iterations=6,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )


# ─── Query Runner ─────────────────────────────────────────────────────────────

def run_query(executor: AgentExecutor, query: str) -> dict:
    """
    Run a single natural-language query through the tool-using agent.

    Returns a dict with:
        query        — the original query string
        response     — the agent's final text answer
        tool_calls   — list of {tool, input, output_preview} for each step
        latency_ms   — wall-clock time in milliseconds
        error        — exception message if the agent crashed, else None
    """
    t0 = time.time()
    try:
        result     = executor.invoke({"input": query})
        latency_ms = round((time.time() - t0) * 1000)

        tool_calls = []
        for action, observation in result.get("intermediate_steps", []):
            tool_calls.append({
                "tool":           action.tool,
                "input":          action.tool_input,
                "output_preview": str(observation)[:300],
            })

        return {
            "query":      query,
            "response":   result["output"],
            "tool_calls": tool_calls,
            "latency_ms": latency_ms,
            "error":      None,
        }
    except Exception as exc:
        return {
            "query":      query,
            "response":   None,
            "tool_calls": [],
            "latency_ms": round((time.time() - t0) * 1000),
            "error":      str(exc),
        }
