"""
Phase 8 — Deployment Readiness
OpsPilot: FastAPI wrapper with structured JSON logging, latency tracking,
PII-safe logs, graceful failure handling, and feedback-driven adaptation.

Endpoints
---------
POST /query      — main agent query (Pydantic models in and out)
GET  /health     — readiness check: data loaded, agent ready, uptime
GET  /metrics    — latency percentiles P50 / P95 / P99, request/error counts
POST /feedback   — record a user rating; may adapt the agent style

Notebook usage (TestClient — no background server process needed)
-----------------------------------------------------------------
    from api_server import app, setup_app
    from fastapi.testclient import TestClient

    setup_app(API_KEY, incidents_df, sla_targets_df, collection)
    client = TestClient(app)
    r = client.post("/query", json={"query": "Is auth-service healthy?"})
    print(r.json())

Architecture note
-----------------
All shared state lives in the module-level _state dict.
setup_app() populates it; endpoint handlers read/write it.
In production, state would move to app.state and be initialised via
a FastAPI lifespan handler — the pattern is identical, just wired
differently.
"""

import json
import re
import time
import statistics
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from tool_agent     import init_agent_data
from adaptive_agent import AdaptiveConfig, FeedbackStore, build_adaptive_agent, run_adaptive
from memory_agent   import SessionMemory


# ── Constants ─────────────────────────────────────────────────────────────────

AGENT_TIMEOUT_S = 45          # wall-clock seconds before returning 504
LOG_DIR         = Path("logs")


# ── PII Utilities ─────────────────────────────────────────────────────────────

_PII_PATTERNS = [
    # Analyst IDs  (ANL-042)
    (re.compile(r'\bANL-\d{3,4}\b'),                              '[ANALYST-ID]'),
    # Name-like pairs  (Firstname Lastname)
    (re.compile(r'\b[A-Z][a-z]{1,20} [A-Z][a-z]{1,20}\b'),       '[NAME]'),
    # Email addresses
    (re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b'), '[EMAIL]'),
]


def strip_pii(text: str) -> str:
    """Remove recognised PII patterns before writing to log files."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── Shared State ──────────────────────────────────────────────────────────────

_state: Dict[str, Any] = {
    "ready":          False,
    "api_key":        None,
    "executor":       None,
    "config":         None,
    "feedback_store": None,
    "memory":         None,
    "start_time":     None,
    "request_count":  0,
    "error_count":    0,
    "latency_log":    [],   # list[int]  — milliseconds per request
    "request_log":    [],   # list[dict] — structured log entries (in-memory mirror)
}


def setup_app(
    api_key:        str,
    incidents_df=None,
    sla_targets_df=None,
    collection=None,
) -> None:
    """
    Initialise the API layer with data and build the adaptive agent.

    Parameters
    ----------
    api_key       : OpenAI API key string.
    incidents_df  : pandas DataFrame from incidents.csv.
    sla_targets_df: pandas DataFrame from sla_targets.csv.
    collection    : ChromaDB collection (optional; search_runbook degrades gracefully).

    Must be called once before any HTTP requests are handled.
    Calling again resets all counters and rebuilds the agent.
    """
    if incidents_df is not None:
        init_agent_data(incidents_df, sla_targets_df, collection)

    LOG_DIR.mkdir(exist_ok=True)

    config         = AdaptiveConfig()
    feedback_store = FeedbackStore(log_dir=str(LOG_DIR))
    memory         = SessionMemory(max_turns=10)
    executor       = build_adaptive_agent(api_key, config)

    _state.update({
        "ready":          True,
        "api_key":        api_key,
        "executor":       executor,
        "config":         config,
        "feedback_store": feedback_store,
        "memory":         memory,
        "start_time":     datetime.now(),
        "request_count":  0,
        "error_count":    0,
        "latency_log":    [],
        "request_log":    [],
    })


# ── Pydantic Request / Response Models ────────────────────────────────────────

class QueryRequest(BaseModel):
    """Inbound payload for POST /query."""
    query: str = Field(
        ..., min_length=3, max_length=500,
        description="Natural language query for OpsPilot",
    )
    include_tool_trace: bool = Field(
        False,
        description="If True, include the full tool call trace in the response",
    )
    session_reset: bool = Field(
        False,
        description="If True, hard-reset session memory before processing this query",
    )


class ToolCallRecord(BaseModel):
    """Single tool invocation captured in the agent trace."""
    tool:           str
    input_summary:  str
    output_preview: str


class QueryResponse(BaseModel):
    """Structured response from POST /query."""
    query:      str
    response:   str
    tool_calls: List[ToolCallRecord]
    latency_ms: int
    word_count: int
    timestamp:  str


class FeedbackRequest(BaseModel):
    """Inbound payload for POST /feedback."""
    query:     str = Field(..., min_length=3)
    response:  str = Field(..., min_length=1)
    rating:    int = Field(..., ge=1, le=5,
                           description="User rating: 1 (poor) to 5 (excellent)")
    dimension: str = Field(
        "overall",
        description="Which aspect to rate: verbosity | recommendations | uncertainty | overall",
    )
    note: str = Field("", description="Optional free-text note")


class FeedbackResponse(BaseModel):
    """Result from POST /feedback."""
    recorded:   bool
    adaptation: str  # description of any config change triggered


class HealthResponse(BaseModel):
    """Result from GET /health."""
    status:          str    # "ok" | "degraded" | "error"
    uptime_seconds:  float
    request_count:   int
    error_count:     int
    error_rate_pct:  float
    data_loaded:     bool
    agent_ready:     bool


class MetricsResponse(BaseModel):
    """Result from GET /metrics."""
    request_count:   int
    error_count:     int
    p50_latency_ms:  Optional[float]
    p95_latency_ms:  Optional[float]
    p99_latency_ms:  Optional[float]
    avg_latency_ms:  Optional[float]
    min_latency_ms:  Optional[float]
    max_latency_ms:  Optional[float]


# ── Internal Helpers ──────────────────────────────────────────────────────────

def _percentile(data: list, p: float) -> Optional[float]:
    """
    Compute the p-th percentile of data using linear interpolation.
    Returns None if data is empty.
    """
    if not data:
        return None
    s   = sorted(data)
    k   = (len(s) - 1) * p / 100
    lo  = int(k)
    hi  = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def _log_request(entry: dict) -> None:
    """
    Persist a structured log entry.
    Appends to logs/api_requests.jsonl (newline-delimited JSON).
    Failures are silently swallowed so logging never crashes the API.
    """
    _state["request_log"].append(entry)
    try:
        with open(LOG_DIR / "api_requests.jsonl", "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ── FastAPI Application ───────────────────────────────────────────────────────

app = FastAPI(
    title       = "OpsPilot API",
    description = (
        "Read-only AI Decision Support Copilot for NovaTech IT Operations. "
        "Wraps Phase 7 adaptive agent with production-grade HTTP endpoints."
    ),
    version     = "1.0.0",
)


# ── GET /health ────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health_check():
    """
    Return server health status.

    status = "ok"       → fully operational
    status = "degraded" → running but error rate > 20 %
    status = "error"    → agent not initialised
    """
    start  = _state.get("start_time")
    uptime = (datetime.now() - start).total_seconds() if start else 0.0
    n      = _state["request_count"]
    e      = _state["error_count"]
    error_rate = round(e / n * 100, 1) if n > 0 else 0.0

    if not _state["ready"]:
        status = "error"
    elif error_rate > 20:
        status = "degraded"
    else:
        status = "ok"

    return HealthResponse(
        status          = status,
        uptime_seconds  = round(uptime, 1),
        request_count   = n,
        error_count     = e,
        error_rate_pct  = error_rate,
        data_loaded     = _state.get("executor") is not None,
        agent_ready     = _state["ready"],
    )


# ── GET /metrics ───────────────────────────────────────────────────────────────

@app.get("/metrics", response_model=MetricsResponse)
def get_metrics():
    """
    Return latency percentiles and request/error counts.
    Computed across all requests since the last setup_app() call.
    """
    lat = _state["latency_log"]
    return MetricsResponse(
        request_count  = _state["request_count"],
        error_count    = _state["error_count"],
        p50_latency_ms = _percentile(lat, 50),
        p95_latency_ms = _percentile(lat, 95),
        p99_latency_ms = _percentile(lat, 99),
        avg_latency_ms = round(statistics.mean(lat), 1) if lat else None,
        min_latency_ms = round(min(lat), 1) if lat else None,
        max_latency_ms = round(max(lat), 1) if lat else None,
    )


# ── POST /query ────────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse)
def query_agent(req: QueryRequest):
    """
    Submit a natural language query to OpsPilot.

    The agent selects and calls the appropriate tool(s), returns a structured
    response, and writes a PII-stripped log entry to logs/api_requests.jsonl.

    Set include_tool_trace=True to receive the full tool call trace.
    Set session_reset=True to clear conversation memory before this query.
    """
    if not _state["ready"]:
        raise HTTPException(
            status_code = 503,
            detail      = "Agent not initialised. Call setup_app() first.",
        )

    _state["request_count"] += 1
    t0 = time.time()
    ts = datetime.now().isoformat()

    if req.session_reset:
        _state["memory"].reset(hard=True)

    try:
        # Run the agent in a worker thread so we can enforce a hard timeout.
        # The ThreadPoolExecutor is created per-request (acceptable for demo scale).
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                run_adaptive,
                _state["executor"],
                req.query,
                _state["memory"],
            )
            try:
                result = future.result(timeout=AGENT_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                _state["error_count"] += 1
                latency_ms = round((time.time() - t0) * 1000)
                _state["latency_log"].append(latency_ms)
                _log_request({
                    "ts": ts, "latency_ms": latency_ms,
                    "query_safe": strip_pii(req.query[:120]),
                    "tools_used": [], "word_count": 0,
                    "error": f"timeout after {AGENT_TIMEOUT_S}s",
                })
                raise HTTPException(
                    status_code = 504,
                    detail      = f"Agent timed out after {AGENT_TIMEOUT_S}s. Try a simpler query.",
                )

        latency_ms = round((time.time() - t0) * 1000)
        _state["latency_log"].append(latency_ms)

        all_tool_calls = result.get("tool_calls") or []
        tools_used     = [tc["tool"] for tc in all_tool_calls]

        # Build tool trace for response (only if caller requested it)
        tool_records: List[ToolCallRecord] = []
        if req.include_tool_trace:
            for tc in all_tool_calls:
                tool_records.append(ToolCallRecord(
                    tool           = tc["tool"],
                    input_summary  = str(tc.get("input", {}))[:120],
                    output_preview = (tc.get("output_preview") or "")[:200],
                ))

        # PII-stripped structured log
        _log_request({
            "ts":         ts,
            "latency_ms": latency_ms,
            "query_safe": strip_pii(req.query[:120]),
            "tools_used": tools_used,
            "word_count": result.get("word_count", 0),
            "error":      result.get("error"),
        })

        if result.get("error"):
            _state["error_count"] += 1

        return QueryResponse(
            query      = req.query,
            response   = result.get("response") or "No response generated.",
            tool_calls = tool_records,
            latency_ms = latency_ms,
            word_count = result.get("word_count", 0),
            timestamp  = ts,
        )

    except HTTPException:
        raise
    except Exception as exc:
        _state["error_count"] += 1
        latency_ms = round((time.time() - t0) * 1000)
        _state["latency_log"].append(latency_ms)
        _log_request({
            "ts":         ts,
            "latency_ms": latency_ms,
            "query_safe": strip_pii(req.query[:120]),
            "tools_used": [],
            "word_count": 0,
            "error":      str(exc),
        })
        raise HTTPException(
            status_code = 500,
            detail      = f"Agent error: {str(exc)[:200]}",
        )


# ── POST /feedback ─────────────────────────────────────────────────────────────

@app.post("/feedback", response_model=FeedbackResponse)
def record_feedback(req: FeedbackRequest):
    """
    Record a user rating (1–5) and, if accumulated ratings cross a threshold,
    adapt the agent style (verbosity / recommendations / uncertainty flags).

    The adaptation is prompt-level only — no model retraining.
    If style changes, the AgentExecutor is rebuilt with the updated prompt.
    """
    if not _state["ready"]:
        raise HTTPException(status_code=503, detail="Server not initialised.")

    store  = _state["feedback_store"]
    config = _state["config"]

    store.record(
        query     = req.query,
        response  = req.response,
        rating    = req.rating,
        dimension = req.dimension,
        note      = req.note,
    )
    change = store.suggest(config)

    # Rebuild executor only when an actual config change occurred
    _no_change_phrases = {
        "no change needed",
        "No feedback recorded yet.",
        "Already at most concise setting.",
    }
    if change not in _no_change_phrases and not change.startswith("Avg rating"):
        _state["executor"] = build_adaptive_agent(_state["api_key"], config)

    return FeedbackResponse(recorded=True, adaptation=change)
