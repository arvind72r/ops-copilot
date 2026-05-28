"""
rag_agent.py — Phase 4: Embeddings & Semantic Retrieval (RAG)
OpsPilot: IT Operations Copilot

Architecture:
  1. Ingest: chunk runbooks + incident summaries → embed → store in ChromaDB
  2. Retrieve: embed query → find top-k similar chunks
  3. Augment: inject retrieved chunks alongside structured data context
  4. Generate: LLM answers from both structured data AND retrieved knowledge

Run:
    python agent/rag_agent.py --build          # build vector store (run once)
    python agent/rag_agent.py --demo           # with/without RAG comparison
    python agent/rag_agent.py                  # interactive mode
"""

import os
import re
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv
import chromadb
from chromadb.utils import embedding_functions

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).resolve().parent.parent
DATA_DIR      = BASE_DIR / "data"
RUNBOOKS_DIR  = DATA_DIR / "runbooks"
VECTORSTORE   = BASE_DIR / "data" / "vectorstore"
LOG_DIR       = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# ── Logger ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=str(LOG_DIR / "rag_interactions.log"),
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


# ── Document Preparation ──────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> list[str]:
    """
    Split text into overlapping chunks.
    overlap ensures context is not lost at chunk boundaries.
    """
    words  = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        end   = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def prepare_documents(data: dict) -> list[dict]:
    """
    Build the corpus for embedding:
      1. Runbook text files (procedural knowledge)
      2. Per-service incident summaries (statistical knowledge)
      3. SLA definitions
    Returns list of {id, text, metadata} dicts.
    """
    docs = []

    # ── 1. Runbook text files ─────────────────────────────────────────────────
    for rb_file in RUNBOOKS_DIR.glob("*.txt"):
        text = rb_file.read_text()
        for i, chunk in enumerate(chunk_text(text, chunk_size=300, overlap=60)):
            docs.append({
                "id":       f"rb_{rb_file.stem}_{i}",
                "text":     chunk,
                "metadata": {
                    "source":   "runbook",
                    "filename": rb_file.name,
                    "service":  rb_file.stem,
                    "chunk":    i,
                },
            })

    # ── 2. Per-service incident summaries ─────────────────────────────────────
    inc = data["incidents"]
    svc = data["services"]
    for _, srow in svc.iterrows():
        svc_name  = srow["service_name"]
        svc_inc   = inc[inc["service"] == svc_name]
        if svc_inc.empty:
            continue

        top_causes  = svc_inc["root_cause"].value_counts().head(3)
        breach_cnt  = (svc_inc["sla_breached"] == "Yes").sum()
        open_cnt    = svc_inc["status"].isin(["Open", "In Progress"]).sum()
        avg_mttr    = svc_inc["mttr_minutes"].mean()

        summary = (
            f"Service summary for {svc_name}:\n"
            f"Total incidents: {len(svc_inc)}\n"
            f"Open/In-Progress: {open_cnt}\n"
            f"SLA breaches: {breach_cnt} ({breach_cnt/max(len(svc_inc),1)*100:.1f}%)\n"
            f"Average MTTR: {avg_mttr:.0f} min\n"
            f"Criticality: {srow['criticality']}\n"
            f"Uptime (30d): {srow['uptime_pct_30d']}%\n"
            f"Top root causes:\n" +
            "\n".join(f"  - {c}: {n} incidents" for c, n in top_causes.items())
        )
        docs.append({
            "id":       f"svc_summary_{svc_name.replace('-','_')}",
            "text":     summary,
            "metadata": {
                "source":  "service_summary",
                "service": svc_name,
                "chunk":   0,
            },
        })

    # ── 3. SLA definitions ────────────────────────────────────────────────────
    sla_text = (
        "NovaTech SLA Definitions:\n"
        "P1 (Critical) — Full outage or data loss risk. MTTR target: 60 minutes.\n"
        "P2 (High)     — Major feature degraded. MTTR target: 4 hours (240 min).\n"
        "P3 (Medium)   — Minor feature affected. MTTR target: 24 hours (1440 min).\n"
        "P4 (Low)      — Cosmetic or non-urgent. MTTR target: 72 hours (4320 min).\n"
        "Escalation rule: If MTTR is projected to exceed SLA, escalate immediately.\n"
        "P1 incidents require Ops Lead notification within 10 minutes."
    )
    docs.append({
        "id":       "sla_definitions",
        "text":     sla_text,
        "metadata": {"source": "sla", "service": "all", "chunk": 0},
    })

    return docs


# ── Vector Store ──────────────────────────────────────────────────────────────
def build_vectorstore(docs: list[dict], api_key: str) -> chromadb.Collection:
    """Embed all documents and persist to ChromaDB."""
    VECTORSTORE.mkdir(parents=True, exist_ok=True)

    embed_fn = embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name="text-embedding-3-small",
    )

    client = chromadb.PersistentClient(path=str(VECTORSTORE))

    # Delete and recreate to ensure fresh build
    try:
        client.delete_collection("ops_knowledge")
    except Exception:
        pass

    collection = client.get_or_create_collection(
        name="ops_knowledge",
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    # Batch upsert
    BATCH = 50
    for i in range(0, len(docs), BATCH):
        batch = docs[i : i + BATCH]
        collection.upsert(
            ids        = [d["id"]   for d in batch],
            documents  = [d["text"] for d in batch],
            metadatas  = [d["metadata"] for d in batch],
        )
        print(f"  Embedded {min(i+BATCH, len(docs))}/{len(docs)} chunks...")

    print(f"✅ Vector store built — {collection.count()} chunks indexed")
    return collection


def load_vectorstore(api_key: str) -> chromadb.Collection:
    """Load existing ChromaDB collection."""
    embed_fn = embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name="text-embedding-3-small",
    )
    client = chromadb.PersistentClient(path=str(VECTORSTORE))
    return client.get_collection(
        name="ops_knowledge",
        embedding_function=embed_fn,
    )


# ── Retrieval ─────────────────────────────────────────────────────────────────
def retrieve(query: str, collection: chromadb.Collection,
             top_k: int = 3) -> list[dict]:
    """
    Embed query → find top_k most similar chunks.
    Returns list of {text, source, relevance_score}.
    """
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity score (1 = perfect match)
        similarity = round(1 - dist / 2, 3)
        hits.append({
            "text":       doc,
            "source":     meta.get("source", "unknown"),
            "service":    meta.get("service", ""),
            "filename":   meta.get("filename", ""),
            "similarity": similarity,
        })

    return hits


def format_retrieved_context(hits: list[dict],
                              min_similarity: float = 0.3) -> str:
    """
    Format retrieved chunks as a string for LLM injection.
    Filters out low-relevance hits to avoid noise.
    """
    relevant = [h for h in hits if h["similarity"] >= min_similarity]

    if not relevant:
        return "No relevant knowledge base entries found for this query."

    parts = ["Retrieved knowledge base context:"]
    for i, h in enumerate(relevant, 1):
        src = h["filename"] if h["filename"] else h["source"]
        parts.append(
            f"\n[KB-{i}] Source: {src} (relevance: {h['similarity']:.2f})\n"
            f"{h['text']}"
        )
    return "\n".join(parts)


# ── Context Builder (Phase 3 — structured data) ───────────────────────────────
def build_structured_context(query: str, data: dict) -> str:
    """Re-uses Phase 3 context builder logic for structured CSV data."""
    q   = query.lower()
    now = datetime.now()
    inc = data["incidents"]
    svc = data["services"]
    sla = data["sla"]

    m = re.search(r"inc-(\d{4})", q)
    if m:
        inc_id = f"INC-{m.group(1)}"
        row = inc[inc["incident_id"] == inc_id]
        if row.empty:
            return f"No incident found with ID {inc_id}."
        r = row.iloc[0]
        return (f"Incident: {inc_id} | Service: {r['service']} | "
                f"Severity: {r['severity']} | Status: {r['status']}\n"
                f"Root cause: {r['root_cause']}\n"
                f"Opened: {r['opened_at'].strftime('%Y-%m-%d %H:%M')} | "
                f"SLA breached: {r['sla_breached']}")

    svc_match = next((s for s in svc["service_name"] if s.lower() in q), None)

    if any(w in q for w in ["sla", "breach"]):
        b      = inc[inc["sla_breached"] == "Yes"]
        by_svc = b.groupby("service").size().sort_values(ascending=False).head(5)
        by_sev = b.groupby("severity").size().reindex(["P1","P2","P3","P4"], fill_value=0)
        return (f"SLA breaches: {len(b)} of {len(inc)} ({len(b)/len(inc)*100:.1f}%)\n"
                f"By severity: " + " | ".join(f"{s}:{c}" for s,c in by_sev.items()) +
                f"\nTop services: " + " | ".join(f"{s}:{c}" for s,c in by_svc.items()))

    if any(w in q for w in ["root cause","cause","why","pattern"]):
        sev = next((s for s in ["P1","P2","P3","P4"] if s.lower() in q), None)
        df  = inc[inc["severity"] == sev] if sev else inc
        top = df["root_cause"].value_counts().head(5)
        return ("Top root causes:\n" +
                "\n".join(f"  {i+1}. {c}: {n}" for i,(c,n) in enumerate(top.items())))

    if any(w in q for w in ["mttr","resolution time","mean time"]):
        df = inc[inc["mttr_minutes"].notna()].copy()
        if svc_match: df = df[df["service"] == svc_match]
        by_sev  = df.groupby("severity")["mttr_minutes"].mean().reindex(["P1","P2","P3","P4"])
        sla_idx = sla.set_index("severity")
        lines   = [f"MTTR{' for '+svc_match if svc_match else ''}:"]
        for s,v in by_sev.items():
            if pd.isna(v): lines.append(f"  {s}: no data")
            else:
                t = sla_idx.loc[s,"sla_mttr_minutes"]
                lines.append(f"  {s}: {v:.0f}min (target {t}min) — {'OVER' if v>t else 'OK'}")
        return "\n".join(lines)

    wm = re.search(r"last (\d+) (day|week|month)", q)
    if wm or any(w in q for w in ["how many","count","total","trend","spike",
                                   "unusual","lately","recently","this month","this week"]):
        if wm:
            n,unit = int(wm.group(1)),wm.group(2)
            days = n if unit=="day" else n*7 if unit=="week" else n*30
            df, lbl = inc[inc["opened_at"] >= now-timedelta(days=days)], f"last {n} {unit}(s)"
        elif "this month" in q:
            df  = inc[(inc["opened_at"].dt.year==now.year)&(inc["opened_at"].dt.month==now.month)]
            lbl = now.strftime("%B %Y")
        elif any(w in q for w in ["lately","recently","unusual","spike","trend"]):
            df, lbl = inc[inc["opened_at"] >= now-timedelta(days=30)], "last 30 days"
        else:
            df, lbl = inc, "all time"

        svc_label = ""
        if svc_match:
            df, svc_label = df[df["service"]==svc_match], f" for {svc_match}"

        by_sev     = df.groupby("severity").size().reindex(["P1","P2","P3","P4"], fill_value=0)
        prev_year  = now.year if now.month>1 else now.year-1
        prev_month = now.month-1 if now.month>1 else 12
        base   = inc if not svc_match else inc[inc["service"]==svc_match]
        this_m = base[(base["opened_at"].dt.year==now.year)&(base["opened_at"].dt.month==now.month)]
        last_m = base[(base["opened_at"].dt.year==prev_year)&(base["opened_at"].dt.month==prev_month)]
        delta  = len(this_m)-len(last_m)
        pct    = delta/max(len(last_m),1)*100
        open_n = inc[inc["status"].isin(["Open","In Progress"])]
        if svc_match: open_n = open_n[open_n["service"]==svc_match]
        return (f"Incident count{svc_label} ({lbl}): {len(df)}\n" +
                "\n".join(f"  {s}: {c}" for s,c in by_sev.items()) +
                f"\nMonth-over-month: {'+' if delta>=0 else ''}{delta} ({pct:+.1f}%) — "
                f"{'UP' if delta>0 else 'DOWN' if delta<0 else 'FLAT'}"
                f"\nOpen/In-Progress{svc_label}: {len(open_n)} (P1: {len(open_n[open_n['severity']=='P1'])})")

    open_c   = inc[inc["status"].isin(["Open","In Progress"])].shape[0]
    breach_c = inc[inc["sla_breached"]=="Yes"].shape[0]
    top_svc  = inc.groupby("service").size().idxmax()
    return (f"System: {len(inc)} total incidents | {open_c} open | "
            f"{breach_c} SLA breaches | Most incidents: {top_svc}")


# ── RAG Prompt ────────────────────────────────────────────────────────────────
RAG_PROMPT = """You are OpsPilot — a read-only AI Decision Support Copilot for NovaTech's IT Operations team.

YOUR ROLE: Help NOC analysts understand incidents, SLA health, and service stability.

SCOPE: IT incident analysis, SLA tracking, MTTR, root causes, operational trends, runbook procedures.
NOT in scope: hiring, budgets, HR, vendor decisions.

SAFETY RULES:
1. Never restart, deploy, or trigger anything — REFUSE and escalate.
2. Never fabricate data. If not in context: "I don't have sufficient data for that."
3. For ambiguous/high-risk situations, recommend human review.
4. Do not expose analyst names or employee IDs.

YOU HAVE TWO SOURCES OF INFORMATION:
A) Structured data (live incident/service metrics from CSV)
B) Knowledge base (runbooks, service summaries, SLA definitions — retrieved by similarity)

Prefer source A for numbers and counts.
Prefer source B for procedures, escalation paths, and known issues.
When both are relevant, synthesise them.
If KB relevance is low (< 0.4), note that retrieved context may not be directly applicable.

RESPONSE FORMAT:
- Lead with direct answer, support with data.
- Cite your source: [from data] or [from runbook] or [from KB].
- Flag uncertainty with ⚠️ Note:
- End complex answers with: Recommend: [next step]

DATA FRESHNESS: Context is from the latest dataset snapshot. Do not assert real-time status.

--- STRUCTURED DATA CONTEXT ---
{structured_context}

--- KNOWLEDGE BASE CONTEXT ---
{kb_context}"""


# ── RAG Agent Respond ─────────────────────────────────────────────────────────
def respond_rag(query: str, data: dict, collection: chromadb.Collection,
                client: OpenAI, top_k: int = 3) -> dict:
    """
    Full RAG pipeline:
    1. Build structured context from Pandas
    2. Retrieve top_k relevant chunks from ChromaDB
    3. Call LLM with both contexts
    Returns response + retrieval metadata.
    """
    structured_ctx = build_structured_context(query, data)
    hits           = retrieve(query, collection, top_k=top_k)
    kb_ctx         = format_retrieved_context(hits, min_similarity=0.25)

    system_msg = RAG_PROMPT.format(
        structured_context = structured_ctx,
        kb_context         = kb_ctx,
    )

    t0 = time.time()
    resp = client.chat.completions.create(
        model    = os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": query},
        ],
        temperature = 0.2,
        max_tokens  = 600,
    )
    text    = resp.choices[0].message.content.strip()
    latency = round((time.time() - t0) * 1000, 1)

    log_event({
        "query":      query,
        "response":   text,
        "latency_ms": latency,
        "tokens":     resp.usage.total_tokens,
        "kb_hits":    len([h for h in hits if h["similarity"] >= 0.25]),
        "agent":      "rag-v1",
    })

    return {
        "response":   text,
        "hits":       hits,
        "latency_ms": latency,
        "tokens":     resp.usage.total_tokens,
    }


# ── Without-RAG Baseline (Phase 3 style — for comparison) ────────────────────
WITHOUT_RAG_PROMPT = """You are OpsPilot — a read-only IT ops copilot for NovaTech.
Help analysts understand incidents and service health. Never take actions.
If data is not in context, say so explicitly.

Structured data context:
{structured_context}"""

def respond_no_rag(query: str, data: dict, client: OpenAI) -> str:
    """Phase 3-style response: structured data only, no KB retrieval."""
    ctx = build_structured_context(query, data)
    resp = client.chat.completions.create(
        model    = os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages = [
            {"role": "system", "content": WITHOUT_RAG_PROMPT.format(structured_context=ctx)},
            {"role": "user",   "content": query},
        ],
        temperature = 0.2,
        max_tokens  = 400,
    )
    return resp.choices[0].message.content.strip()


# ── Demo: With vs Without RAG ─────────────────────────────────────────────────
DEMO_QUERIES = [
    # These require runbook knowledge — structured data alone can't answer them
    "What is the escalation path if auth-service has a P1 incident?",
    "What are the known recurring issues with auth-service?",
    "What should I do if payments-api times out due to a third-party gateway?",
    # This one benefits from both structured data AND runbook
    "auth-service has been down for 25 minutes — what do I do?",
    # This one should be fine with structured data only
    "How many P1 incidents happened this month?",
]

def run_demo(data: dict, collection: chromadb.Collection, client: OpenAI) -> None:
    print("\n" + "="*70)
    print("  Phase 4 — RAG Demo: With vs Without Retrieval")
    print("="*70)

    for i, q in enumerate(DEMO_QUERIES, 1):
        print(f"\n{'─'*70}")
        print(f"Q{i}: {q}")
        print(f"{'─'*70}")

        no_rag = respond_no_rag(q, data, client)
        print(f"\n[WITHOUT RAG — structured data only]")
        print(no_rag)

        rag_result = respond_rag(q, data, collection, client)
        print(f"\n[WITH RAG — structured data + knowledge base]")
        print(rag_result["response"])

        print(f"\n📎 Retrieved chunks ({len(rag_result['hits'])}):")
        for h in rag_result["hits"]:
            src = h['filename'] if h['filename'] else h['source']
            print(f"   • {src} — similarity: {h['similarity']:.3f}")

        time.sleep(0.5)

    print("\n" + "="*70)


# ── Interactive Mode ───────────────────────────────────────────────────────────
def run_interactive(data: dict, collection: chromadb.Collection,
                    client: OpenAI) -> None:
    print("\n" + "="*70)
    print("  OpsPilot RAG Agent — Phase 4")
    print("  Type 'quit' to exit | 'hits' after a query to see retrieved chunks")
    print("="*70 + "\n")

    last_hits = []
    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query: continue
        if query.lower() in ("quit","exit"): break
        if query.lower() == "hits":
            for h in last_hits:
                print(f"  [{h['similarity']:.3f}] {h['filename'] or h['source']}: {h['text'][:120]}...")
            continue

        result    = respond_rag(query, data, collection, client)
        last_hits = result["hits"]
        print(f"\nOpsPilot: {result['response']}\n")


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="Build vector store")
    parser.add_argument("--demo",  action="store_true", help="Run with/without RAG demo")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY not set.")
        sys.exit(1)

    print("Loading data...")
    data   = load_data()
    client = OpenAI(api_key=api_key)

    if args.build:
        print("Preparing documents...")
        docs = prepare_documents(data)
        print(f"  {len(docs)} chunks to embed")
        collection = build_vectorstore(docs, api_key)
    else:
        try:
            collection = load_vectorstore(api_key)
            print(f"✅ Vector store loaded — {collection.count()} chunks")
        except Exception:
            print("❌ Vector store not found. Run with --build first.")
            sys.exit(1)

    if args.demo:
        run_demo(data, collection, client)
    else:
        run_interactive(data, collection, client)
