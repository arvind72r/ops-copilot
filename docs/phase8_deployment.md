# Phase 8 — Deployment Readiness
## FastAPI Wrapper, Structured Logging & Latency Tracking Report

**Agent:** OpsPilot | **Framework:** FastAPI + LangChain | **Testing:** `fastapi.testclient.TestClient`

---

## Architecture

Phase 8 wraps the full Phase 7 adaptive agent stack in a production-style HTTP API.
No model or tool changes — only a new HTTP layer on top.

```
HTTP Request
    ↓
FastAPI endpoint  ←  Pydantic validates input (422 on bad request)
    ↓
concurrent.futures.ThreadPoolExecutor  ←  hard 45s timeout → 504 on breach
    ↓
run_adaptive(executor, query, memory)  ←  Phase 7 adaptive stack
    ↓
PII stripping → JSONL log write
    ↓
Latency recorded in _state["latency_log"]
    ↓
Pydantic QueryResponse  →  JSON to caller
```

---

## Endpoints

### POST /query

| Property | Value |
|----------|-------|
| Request model | `QueryRequest` |
| Response model | `QueryResponse` |
| Auth | None (Phase 8 scope; KL20) |
| Timeout | 45 seconds via `concurrent.futures` |
| On bad input | 422 Unprocessable Entity (Pydantic, zero LLM cost) |
| On timeout | 504 Gateway Timeout |
| On agent error | 500 Internal Server Error (detail field contains truncated message) |

**`QueryRequest` fields:**

| Field | Type | Validation | Default |
|-------|------|-----------|---------|
| `query` | str | min_length=3, max_length=500 | required |
| `include_tool_trace` | bool | — | `False` |
| `session_reset` | bool | — | `False` |

**`QueryResponse` fields:** `query`, `response`, `tool_calls` (if trace requested), `latency_ms`, `word_count`, `timestamp`

---

### GET /health

Returns server readiness at a glance.

| `status` value | Meaning |
|----------------|---------|
| `"ok"` | Fully operational |
| `"degraded"` | Running but error rate > 20% |
| `"error"` | `setup_app()` not called — agent not ready |

Fields: `status`, `uptime_seconds`, `request_count`, `error_count`, `error_rate_pct`, `data_loaded`, `agent_ready`

---

### GET /metrics

Returns latency percentiles computed over all requests since `setup_app()`.

| Metric | Computation |
|--------|------------|
| P50 | Linear interpolation on sorted latency list |
| P95 | Linear interpolation on sorted latency list |
| P99 | Linear interpolation on sorted latency list |
| Average | `statistics.mean` |
| Min / Max | `min()` / `max()` |

Returns `null` for all metrics if no requests recorded yet.

---

### POST /feedback

Accepts a user rating (1–5) and delegates to the Phase 7 `FeedbackStore`.
If accumulated ratings cross the adaptation threshold, the `AgentExecutor` is
rebuilt with the updated `AdaptiveConfig` prompt — no restart required.

| `adaptation` response value | Meaning |
|-----------------------------|---------|
| `"verbosity: standard → concise"` | Style changed — executor rebuilt |
| `"Avg rating 3.5/5 — no adaptation needed."` | Threshold not crossed |
| `"Already at most concise setting."` | No further reduction possible |

---

## Structured JSON Logging

Every request (success or failure) writes one line to `logs/api_requests.jsonl`:

```json
{
  "ts":         "2026-05-29T10:42:17.831245",
  "latency_ms": 3412,
  "query_safe": "Is auth-service healthy right now?",
  "tools_used": ["get_service_health"],
  "word_count": 45,
  "error":      null
}
```

`query_safe` is the query **after PII stripping** — the raw query never touches the log file.

---

## PII Stripping

Applied to `query` before log writes (not before sending to the LLM — the agent needs the real text).

| Pattern | Replacement | Example |
|---------|------------|---------|
| `ANL-\d{3,4}` | `[ANALYST-ID]` | ANL-042 → [ANALYST-ID] |
| `[A-Z][a-z]+ [A-Z][a-z]+` | `[NAME]` | John Smith → [NAME] |
| email regex | `[EMAIL]` | noc@novatech.com → [EMAIL] |

Non-PII text (service names, query keywords) passes through unchanged.

---

## Timeout Guard

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(run_adaptive, executor, query, memory)
    result = future.result(timeout=45)  # raises TimeoutError → 504
```

The agent thread continues in the background after a timeout (acceptable at demo scale;
production would use proper task cancellation).

---

## Graceful Failure Summary

| Failure Mode | HTTP Status | Source |
|-------------|-------------|--------|
| Query < 3 chars | 422 | Pydantic validation |
| Query > 500 chars | 422 | Pydantic validation |
| Agent not initialised | 503 | `setup_app()` not called |
| Agent exceeds 45s | 504 | `concurrent.futures.TimeoutError` |
| Unhandled exception | 500 | `except Exception` catch-all |
| Safety refusal | **200** | Agent returns policy message (not an error) |
| Out-of-scope query | **200** | Agent declines politely (not an error) |

---

## TestClient Pattern (Vocareum-Compatible)

No `uvicorn`, no `nohup`, no background process. `TestClient` runs the ASGI app
in-process inside the notebook kernel:

```python
from api_server import app, setup_app
from fastapi.testclient import TestClient

setup_app(API_KEY, incidents_df, sla_targets_df, collection)
client = TestClient(app)

r = client.post("/query", json={"query": "Is auth-service healthy?"})
print(r.status_code, r.json()["response"])
```

---

## State Management

All runtime state lives in the module-level `_state` dict, populated by `setup_app()`.
In production, this moves to `app.state` and is initialised via a FastAPI lifespan handler —
the endpoint logic is identical in both cases.

| `_state` key | Purpose |
|-------------|---------|
| `executor` | Current `AgentExecutor` (rebuilt after feedback adaptation) |
| `config` | `AdaptiveConfig` instance |
| `feedback_store` | `FeedbackStore` instance |
| `memory` | Shared `SessionMemory` (single session — KL17) |
| `latency_log` | `list[int]` — ms per request, for percentile calculation |
| `request_log` | `list[dict]` — in-memory mirror of JSONL log |

---

## Known Limitations Introduced

| ID | Limitation | Impact | Planned Fix |
|----|-----------|--------|-------------|
| KL17 | Single shared `SessionMemory` | No per-session or per-analyst isolation | Production: session-id keyed dict |
| KL18 | Latency log resets on `setup_app()` | No cross-restart persistence | Production: write to time-series DB |
| KL19 | `TestClient` is synchronous | Cannot test async endpoints without pytest-asyncio | Production: `httpx.AsyncClient` |
| KL20 | No authentication on endpoints | Any caller can query or inject feedback | Production: API key header or OAuth |

---

## Files Added in Phase 8

| File | Purpose |
|------|---------|
| `agent/api_server.py` | FastAPI app: 4 endpoints, Pydantic models, PII stripping, latency tracker, timeout guard, structured JSONL logging |
| `notebooks/Phase8_Deployment.ipynb` | 14-cell Vocareum notebook: TestClient demos for all endpoints, failure modes, PII log inspection, feedback-driven adaptation |

---

*Phase 8 complete. Next: Phase 9 — Evaluation & Engineering Review.*
