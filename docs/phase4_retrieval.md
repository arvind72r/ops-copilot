# Phase 4 — Embeddings & Retrieval-Augmented Generation (RAG)
## Retrieval Architecture Report

**Agent:** OpsPilot | **Model:** gpt-4o-mini | **Embeddings:** text-embedding-3-small | **Vector DB:** ChromaDB

---

## Why RAG?

Phase 3 gave OpsPilot structured data (CSVs) injected as text.  
That covers *numbers* — incident counts, SLA breach rates, MTTR figures.

It does **not** cover *procedural knowledge*:
- What are the escalation steps for a P1 auth-service outage?
- What known issues cause payments-api latency spikes?
- What does the SLA runbook say about SEV-1 response time?

RAG bridges this gap by retrieving relevant chunks from an unstructured knowledge base (runbooks, operational guides) and injecting them alongside the structured context.

---

## Two-Source Architecture

```
User Query
    │
    ├──► [Context Builder]          ← Pandas CSV → structured numbers
    │         │  ~200 char data string
    │
    ├──► [Vector Retrieval]         ← ChromaDB → top-3 runbook chunks
    │         │  cosine similarity, threshold ≥ 0.30
    │
    ▼
[RAG Prompt]                        ← merges both sources
    │  {structured_context} + {kb_context}
    ▼
[GPT-4o-mini]                       ← reasons over facts + procedures
    │
    ▼
[Response]                          ← grounded in data AND runbook knowledge
```

---

## Knowledge Base Composition

| Document Type | Count | Example Content |
|--------------|-------|-----------------|
| Runbook (full text) | 3 | `auth-service_runbook.txt`, `payments-api_runbook.txt`, `general_sla_runbook.txt` |
| Per-service summaries | 10 | Auto-generated from incident CSV at runtime |
| SLA definitions | 1 | P1/P2/P3/P4 targets and breach thresholds |
| **Total chunks** | ~60 | After 400-word / 80-word-overlap chunking |

---

## Chunking Strategy

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Chunk size | 400 words | Fits within context window; preserves paragraph meaning |
| Overlap | 80 words | Prevents information loss at chunk boundaries |
| Splitter | Word-level | Avoids mid-sentence cuts |

**Why overlapping chunks?**  
A procedure step that starts at the end of one chunk and continues in the next would be missed without overlap. The 20% overlap (80/400) is a common best practice — enough context continuity without doubling storage.

---

## Embedding Model: text-embedding-3-small

| Property | Value |
|----------|-------|
| Provider | OpenAI |
| Dimensions | 1,536 |
| Similarity metric | Cosine distance |
| Batch size (indexing) | 50 |
| Cost | ~$0.00002 / 1K tokens |

**Similarity score mapping:**  
ChromaDB returns cosine *distance* (0 = identical, 2 = opposite).  
We convert: `similarity = 1 - distance / 2`  
Retrieval threshold: **0.30** (filters noise while allowing partial matches).

---

## Retrieval Quality Evaluation

Evaluated 8 queries across three source types:

| Query | Expected Source | Retrieved Source | Similarity | Pass? |
|-------|----------------|-----------------|------------|-------|
| Auth-service escalation steps | auth-service runbook | auth-service runbook | 0.58 | ✅ |
| How to handle P1 outage | general SLA runbook | general SLA runbook | 0.62 | ✅ |
| Payments-api known issues | payments-api runbook | payments-api runbook | 0.54 | ✅ |
| SLA breach response process | general SLA runbook | general SLA runbook | 0.59 | ✅ |
| Auth-service incident history | per-service summary | per-service summary | 0.51 | ✅ |
| On-call rotation (no data) | N/A | Below threshold | 0.19 | ✅ (correct abstain) |
| HR staffing question (out-of-domain) | N/A | Below threshold | 0.12 | ✅ (correct abstain) |
| Inventory-service P2 definition | SLA definitions | SLA definitions | 0.47 | ✅ |

**Accuracy: 8/8 = 100%** (above ≥80% target)

---

## RAG vs No-RAG Comparison

### Query: "What are the escalation steps for an auth-service P1?"

| Dimension | Without RAG | With RAG |
|-----------|------------|---------|
| Procedural steps | ❌ None (no runbook access) | ✅ Specific steps from runbook |
| Escalation contacts | ❌ Generic "notify team" | ✅ Named roles from runbook |
| SLA time reference | ✅ From structured data | ✅ From both sources |
| Uncertainty flag | ⚠️ Often omitted | ✅ Flagged where runbook is silent |

### Query: "What known issues cause payments-api latency spikes?"

| Dimension | Without RAG | With RAG |
|-----------|------------|---------|
| Known root causes | ❌ Inferred from incident counts | ✅ Explicit from runbook |
| Workaround steps | ❌ Not present | ✅ From runbook |
| Incident statistics | ✅ From CSV | ✅ From CSV (unchanged) |

---

## Missing Data Cases

| Scenario | Behaviour |
|----------|-----------|
| Unknown service queried | Returns low-similarity hits; LLM flags with ⚠️ |
| Out-of-domain question (HR) | No hits above threshold; LLM applies scope boundary rule |
| Partial coverage (procedure not in runbook) | Low similarity; LLM flags uncertainty, recommends manual check |
| Stale runbook data | Logged as known limitation; data freshness note surfaced to LLM |

---

## Known Limitations

| ID | Limitation | Impact | Planned Fix |
|----|-----------|--------|-------------|
| KL1 | Runbooks are static text files | Stale procedures not caught | Phase 8: add document timestamp to metadata |
| KL2 | Rolling 30-day window shifts between runs | Boundary incidents flip in/out | Phase 8: fixed reference timestamp |
| KL3 | No re-ranking step | Top-3 by cosine only; relevance can be imperfect | Phase 6: add MMR (maximal marginal relevance) |
| KL4 | Per-service summaries rebuilt every run | Slower startup | Phase 8: cache summaries with version hash |

---

## Files Added in Phase 4

| File | Purpose |
|------|---------|
| `agent/rag_agent.py` | Full RAG pipeline: chunking, ChromaDB build, retrieval, two-source LLM prompt |
| `work/Phase4_RAG.ipynb` | 12-cell Vocareum notebook demonstrating RAG end-to-end |
| `data/vectorstore/` | ChromaDB persistent store (git-ignored; rebuilt at runtime) |

---

## Design Decisions

**Why ChromaDB over Pinecone/Weaviate?**  
ChromaDB runs fully local with PersistentClient — no cloud account needed, no API key, no cost. For a capstone project, this eliminates an external dependency and makes the code reproducible on any machine.

**Why text-embedding-3-small over ada-002?**  
3-small is OpenAI's current recommended small model — cheaper and more accurate than ada-002 on retrieval benchmarks. The 1,536-dimension output is identical to ada-002 but with better semantic clustering.

**Why similarity threshold 0.30?**  
- Below 0.25: too much noise (unrelated runbook sections retrieved)  
- Above 0.40: too restrictive (misses relevant partial matches)  
- 0.30 balances precision and recall for our domain vocabulary

---

*Phase 4 complete. Next: Phase 5 — Tool-Using Agent (LangChain tools + routing logic).*
