# OpsPilot — AI IT Operations Copilot

**Capstone Project | Scenario 1: Business Operations — AI Operations Copilot**  
**Track A: LangChain | Python 3.11+**

---

## Project Structure

```
ops-copilot/
├── docs/                    # Phase documents & reports
│   ├── phase1_problem_framing.md
│   ├── phase2_baseline.md
│   ├── phase3_llm_prompts.md
│   ├── phase4_retrieval.md
│   ├── phase5_tools.md
│   ├── phase6_memory.md
│   ├── phase7_adaptive.md
│   ├── phase8_deployment.md
│   └── phase9_evaluation.md
├── data/                    # Synthetic datasets
│   ├── incidents.csv
│   ├── services.csv
│   ├── sla_targets.csv
│   └── runbooks/
├── agent/                   # Core agent code
│   ├── baseline.py          # Phase 2: Rules-based agent
│   ├── llm_agent.py         # Phase 3: LLM-powered agent
│   ├── rag_agent.py         # Phase 4: RAG-enhanced agent
│   ├── tools.py             # Phase 5: Tool definitions
│   ├── memory.py            # Phase 6: Memory handling
│   ├── adaptive.py          # Phase 7: Feedback loop
│   └── ops_copilot.py       # Phase 8: Full production agent
├── tests/                   # Phase 9: Evaluation harness
│   ├── test_prompts.json
│   ├── eval_harness.py
│   └── results/
├── logs/                    # Runtime logs (PII-safe)
└── notebooks/               # Optional Jupyter notebooks
```

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run (Phase 2 baseline)
```bash
python agent/baseline.py
```

## Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Problem Framing | ✅ Done |
| 2 | Baseline Prototype | 🔄 Next |
| 3 | LLM Integration & Prompt Design | ⏳ Pending |
| 4 | Embeddings & RAG | ⏳ Pending |
| 5 | Tool-Using Agent | ⏳ Pending |
| 6 | Planning & Memory | ⏳ Pending |
| 7 | Adaptive Behaviour | ⏳ Pending |
| 8 | Deployment Readiness | ⏳ Pending |
| 9 | Evaluation & Review | ⏳ Pending |

## Safety Commitments
- Read-only: Agent cannot modify data or trigger actions
- No PII in logs: All logs strip analyst names and employee IDs
- Uncertainty-first: Agent says "I don't know" rather than guessing
- Escalation-aware: Ambiguous/high-risk queries always recommend human review
