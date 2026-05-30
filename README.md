# OpsPilot — AI IT Operations Copilot

**Capstone Project | Scenario 1: Business Operations — AI Operations Copilot (Decision Support Only)**  
**Track A: LangChain | Python 3.11+ | GPT-4o-mini (OpenAI)**

OpsPilot is a read-only AI Decision Support Copilot for NovaTech IT Operations.  
It accepts natural language queries from NOC analysts, calls structured tools and a RAG knowledge base,
and returns grounded, explainable answers — without modifying any data or triggering any system actions.

---

## Phase Status

| Phase | Title | Key Artifact | Status |
|-------|-------|-------------|--------|
| 1 | Problem Framing | `docs/phase1_problem_framing.md` | ✅ Done |
| 2 | Baseline Prototype | `agent/baseline.py` · `work/Phase2_Baseline_Agent.ipynb` | ✅ Done |
| 3 | LLM Integration & Prompt Design | `agent/llm_agent.py` · `work/Phase3_LLM_Prompts.ipynb` | ✅ Done |
| 4 | Embeddings & RAG | `agent/rag_agent.py` · `work/Phase4_RAG.ipynb` | ✅ Done |
| 5 | Tool-Using Agent | `agent/tool_agent.py` · `work/Phase5_Tools.ipynb` | ✅ Done |
| 6 | Planning & Memory | `agent/memory_agent.py` · `work/Phase6_Memory.ipynb` | ✅ Done |
| 7 | Adaptive Behaviour | `agent/adaptive_agent.py` · `work/Phase7_Adaptive.ipynb` | ✅ Done |
| 8 | Deployment Readiness | `agent/api_server.py` · `work/Phase8_Deployment.ipynb` | ✅ Done |
| 9 | Evaluation & Review | `agent/eval_harness.py` · `work/Phase9_Evaluation.ipynb` | ✅ Done |

---

## Final Evaluation Results (Run 3)

| Metric | Target | Result |
|--------|--------|--------|
| Tool routing accuracy | ≥ 80% | **100.0%** ✅ |
| Safety refusal rate | 100% | **100.0%** ✅ |
| Error-free rate | ≥ 95% | **100.0%** ✅ |
| P95 latency | ≤ 15,000 ms | **11,248 ms** ✅ |

30/30 eval cases passed across 3 iterative runs. Full reports in `docs/`.

---

## Project Structure

```
ops-copilot/
├── agent/                        # Core agent modules (one per phase)
│   ├── baseline.py               # Phase 2 — Rules-based baseline agent
│   ├── llm_agent.py              # Phase 3 — LLM-powered agent (GPT-4o-mini)
│   ├── rag_agent.py              # Phase 4 — RAG-enhanced agent (ChromaDB)
│   ├── tool_agent.py             # Phase 5 — Tool-using agent (5 structured tools)
│   ├── memory_agent.py           # Phase 6 — Session memory (10-turn sliding window)
│   ├── adaptive_agent.py         # Phase 7 — Feedback-driven style adaptation
│   ├── api_server.py             # Phase 8 — FastAPI deployment wrapper
│   └── eval_harness.py           # Phase 9 — 30-case evaluation harness
│
├── data/                         # Synthetic NovaTech datasets
│   ├── incidents.csv             # 500 synthetic incidents (P1–P4, 10 services)
│   ├── sla_targets.csv           # SLA targets per priority level
│   ├── services.csv              # Service metadata
│   ├── runbooks/                 # Plaintext runbooks (auth-service, payments-api, general)
│   ├── vectorstore/              # ChromaDB persistent store (Phase 4+)
│   └── generate_data.py          # Script that generated the synthetic data
│
├── docs/                         # Phase documents and evaluation reports
│   ├── phase1_problem_framing.md
│   ├── phase2_baseline.md
│   ├── phase3_prompt_comparison.md
│   ├── phase4_retrieval.md
│   ├── phase5_tools.md
│   ├── phase6_memory.md
│   ├── phase7_adaptive.md
│   ├── phase8_deployment.md
│   ├── phase9_Run1_evaluation_report.md
│   ├── phase9_Run2_evaluation_report.md
│   └── phase9_Run3_evaluation_report.md
│
├── notebooks/                    # Jupyter notebooks (upload to work/ on Vocareum)
│   ├── Phase2_Baseline_Agent.ipynb
│   ├── Phase3_LLM_Prompts.ipynb
│   ├── Phase4_RAG.ipynb
│   ├── Phase5_Tools.ipynb
│   ├── Phase6_Memory.ipynb
│   ├── Phase7_Adaptive.ipynb
│   ├── Phase8_Deployment.ipynb
│   ├── Phase9_Evaluation.ipynb
│   └── Streamlit_UI.ipynb        # Chat UI launcher for Vocareum
│
├── logs/                         # Runtime logs (PII-safe JSONL)
├── tests/                        # Reserved for unit tests
├── streamlit_app.py              # Streamlit chat UI (Phase 9+)
├── .streamlit/config.toml        # Streamlit server config (headless, CORS-off)
└── requirements.txt              # All Python dependencies
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | GPT-4o-mini (OpenAI) |
| Orchestration | LangChain `AgentExecutor` + `create_openai_tools_agent` |
| Embeddings | `text-embedding-3-small` (OpenAI) |
| Vector store | ChromaDB (persistent) |
| Tools | LangChain `@tool` decorator — 5 structured read-only tools |
| API | FastAPI + Pydantic v2 + `TestClient` |
| UI | Streamlit |
| Data | Pandas · CSV · synthetic generation |

---

## Setup

```bash
# Clone the repository
git clone https://github.com/arvind72r/ops-copilot.git
cd ops-copilot

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set your OpenAI API key
export OPENAI_API_KEY="sk-..."  # Vocareum sets this automatically
```

---

## Running the Agent

### Streamlit Chat UI (recommended)
```bash
streamlit run streamlit_app.py
```
On Vocareum, open the launcher notebook `work/Streamlit_UI.ipynb` and run cells 1–4
to start the server and get the correct access URL.

### FastAPI Server
```bash
uvicorn agent.api_server:app --reload --port 8000
```
Endpoints: `GET /health` · `GET /metrics` · `POST /query` · `POST /feedback`

### Jupyter Notebooks (Vocareum)
Upload notebooks from `notebooks/` to your Vocareum `work/` directory and run in order.

---

## Phase-by-Phase Summary

### Phase 1 — Problem Framing
Defined the business context (NovaTech, 12 NOC analysts), user personas, daily workflow,
safety constraints (read-only, no PII in logs), and success criteria.  
→ `docs/phase1_problem_framing.md`

### Phase 2 — Baseline Prototype
Rules-based keyword agent with no LLM. Establishes the baseline response quality and
latency benchmark that all later phases are compared against.  
→ `agent/baseline.py` · `work/Phase2_Baseline_Agent.ipynb`

### Phase 3 — LLM Integration & Prompt Design
Replaced rule-matching with GPT-4o-mini. Compared three prompt styles (zero-shot,
few-shot, chain-of-thought) across accuracy, hallucination rate, and response quality.  
→ `agent/llm_agent.py` · `work/Phase3_LLM_Prompts.ipynb` · `docs/phase3_prompt_comparison.md`

### Phase 4 — Embeddings & RAG
Added ChromaDB vector store with 14 runbook chunks (text-embedding-3-small). Agent now
retrieves relevant escalation procedures and known issues alongside structured data.  
→ `agent/rag_agent.py` · `work/Phase4_RAG.ipynb` · `docs/phase4_retrieval.md`

### Phase 5 — Tool-Using Agent
Replaced free-form LLM answers with 5 grounded, structured tools:
`query_incidents` · `check_sla_breaches` · `get_service_health` · `search_runbook` · `get_fleet_summary`.
Agent calls tools before answering — no fabricated data.  
→ `agent/tool_agent.py` · `work/Phase5_Tools.ipynb` · `docs/phase5_tools.md`

### Phase 6 — Planning & Memory
Added `SessionMemory` (10-turn sliding window) so the agent resolves pronouns and
follow-up references across a conversation. Includes save/load/reset and auto-reset
at shift boundaries.  
→ `agent/memory_agent.py` · `work/Phase6_Memory.ipynb` · `docs/phase6_memory.md`

### Phase 7 — Adaptive Behaviour & Feedback
`FeedbackStore` records user ratings (1–5). `AdaptiveConfig` adjusts verbosity
(standard / concise / detailed), recommendation endings, and uncertainty flags
based on rolling rating averages — all via prompt manipulation, no fine-tuning.  
→ `agent/adaptive_agent.py` · `work/Phase7_Adaptive.ipynb` · `docs/phase7_adaptive.md`

### Phase 8 — Deployment Readiness
Wrapped the full agent stack in a FastAPI application with structured Pydantic
request/response models, 45-second timeout protection, PII-safe JSONL logging,
and in-process `TestClient` testing (no uvicorn required on Vocareum).  
→ `agent/api_server.py` · `work/Phase8_Deployment.ipynb` · `docs/phase8_deployment.md`

### Phase 9 — Evaluation & Engineering Review
30-case structured eval harness across 5 categories (routing, chaining, safety,
edge, synthesis). Three iterative runs with RCA-driven fixes between each.
Final Run 3: **100% on all four scoring targets**.  
→ `agent/eval_harness.py` · `work/Phase9_Evaluation.ipynb`  
→ `docs/phase9_Run1_evaluation_report.md`  
→ `docs/phase9_Run2_evaluation_report.md`  
→ `docs/phase9_Run3_evaluation_report.md`

---

## Safety Commitments

| Commitment | Implementation |
|-----------|----------------|
| **Read-only** | No write tools exist; agent cannot restart, deploy, or modify anything |
| **No fabrication** | Every data point is grounded in a live tool call; tool name cited in response |
| **No PII in logs** | Regex strips analyst IDs (ANL-XXX), names, and emails before JSONL writes |
| **Uncertainty-first** | Missing data → explicit ⚠️ flag; never asserted as fact |
| **Escalation-aware** | Ambiguous or high-risk queries always recommend human review |
| **Scope-bounded** | HR, budget, vendor, and staffing questions are politely declined |

---

## Evaluation Run History

| Run | Routing | Safety | Error-Free | P95 Latency | Failures |
|-----|---------|--------|-----------|------------|---------|
| Run 1 (baseline) | 77.3% ❌ | 100% ✅ | 96.7% ✅ | 9,142 ms ✅ | 6 |
| Run 2 (5 fixes) | 77.3% ❌ | 100% ✅ | 100% ✅ | 8,026 ms ✅ | 5 (4 scoring artefacts) |
| Run 3 (final) | **100%** ✅ | **100%** ✅ | **100%** ✅ | **11,248 ms** ✅ | **0** |
