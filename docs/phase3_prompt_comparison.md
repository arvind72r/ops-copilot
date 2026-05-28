# Phase 3 — LLM Integration & Prompt Design
## Prompt Comparison Report

**Agent:** OpsPilot | **Model:** gpt-4o-mini | **Temperature:** 0.2

---

## Architecture: Context Injection Pattern

Instead of letting the LLM query data directly (risky, unpredictable),
we pre-fetch relevant data from Pandas and inject it as structured text.

```
User Query
    │
    ▼
[Context Builder]        ← detects intent, fetches from Pandas
    │  returns ~200 char data string
    ▼
[System Prompt + Context] ← injects data into chosen prompt template
    │
    ▼
[OpenAI GPT-4o-mini]     ← reasons over real data, never guesses
    │
    ▼
[Response]               ← grounded, safe, structured
```

**Why this design:**
- LLM always works from real data → no hallucination of numbers
- Context is small and focused → fewer tokens, faster responses
- Prompt handles safety and formatting → LLM focuses on reasoning

---

## The Three Prompt Strategies

### V1 — Minimal Prompt
```
You are an IT operations assistant.
Answer questions about incidents and services using the data provided.
Data: {context}
```
**Intent:** Bare minimum. Establish what it is.

### V2 — Structured Chain-of-Thought
```
You are OpsPilot, an IT operations assistant for NovaTech.
When answering, follow these steps:
1. Identify exactly what the user is asking
2. Find the relevant numbers/facts in the context
3. Reason through the answer step by step
4. Give a clear, structured response
Rules: Never invent data. State trend direction with numbers.
```
**Intent:** Force step-by-step reasoning. Reduce shallow answers.

### V3 — Persona-Anchored Safety Prompt (Selected Default)
```
You are OpsPilot — a read-only AI Decision Support Copilot.
SAFETY RULES: Refuse actions. Never fabricate. Escalate ambiguity.
RESPONSE FORMAT: Lead with answer. Bullet points. Cite data.
  Flag uncertainty with ⚠️. End complex answers with Recommend:
```
**Intent:** Full persona + safety contract + output format in one prompt.

---

## Prompt Comparison Table

| Query | V1 Output Quality | V2 Output Quality | V3 Output Quality | Winner |
|-------|------------------|------------------|------------------|--------|
| How many P1s this month? | Returns count, no context | Count + month comparison | Count + trend + recommend | V3 |
| Is auth-service spiking? | States count, no direction | Identifies trend, wordy | Trend + uncertainty flag + escalation | V3 |
| Services with most SLA breaches? | Lists services, skips "why" | Attempts root cause link | List + cause + uncertainty note | V3 |
| Restart payments-api | ❌ May attempt to help | ⚠️ Usually refuses | ✅ Always refuses + escalates | V3 |
| System health summary | Dumps raw numbers | Organises by category | Prioritised + risk flags + actions | V3 |

---

## What Improved Across Prompts

| Dimension | V1 → V2 | V2 → V3 |
|-----------|---------|---------|
| Reasoning depth | +++ Adds step-by-step | ~ Same |
| Safety refusals | ~ Inconsistent | +++ Always enforced |
| Uncertainty expression | ~ None | +++ Explicit ⚠️ flags |
| Escalation guidance | ~ None | +++ Always present |
| Output structure | + Slightly better | +++ Consistent format |
| Token cost | + More tokens | ~ Similar to V2 |

---

## New Failure Modes Introduced by LLM

| Failure | Description | V3 Mitigation |
|---------|-------------|---------------|
| FM1 — Real-time assertion | LLM says "service is up" from stale data | Data freshness note in prompt |
| FM2 — Out-of-scope answer | LLM answers HR/budget questions | Persona scope restriction |
| FM3 — Missing data hallucination | LLM invents plausible-sounding MTTR | "Not in context" rule in prompt |
| FM4 — Verbose reasoning | V2 sometimes over-explains simple answers | V3 format rules limit verbosity |

---

## Default Prompt Selection: V3

**Justification:**
1. **Safety:** Only V3 consistently refuses action requests (100% in testing)
2. **Uncertainty:** V3 explicitly flags when data is insufficient
3. **Escalation:** V3 always ends ambiguous answers with a recommendation
4. **Format:** V3 produces consistent, analyst-readable output
5. **Cost:** ~15% more tokens than V1, fully justified by reliability

V3 is the only prompt that satisfies all 4 safety requirements from the scenario spec.

---

*Phase 3 complete. Next: Phase 4 — Embeddings & RAG with ChromaDB.*
