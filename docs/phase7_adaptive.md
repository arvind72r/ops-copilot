# Phase 7 — Adaptive Behaviour & Feedback Signals
## Feedback-Driven Prompt Adaptation Report

**Agent:** OpsPilot | **Adaptation:** Prompt-only (no fine-tuning) | **Model:** gpt-4o-mini

---

## Core Idea

The agent changes its *behaviour* based on how users rate its responses — **without retraining the model**.
Adaptation lives entirely in the system prompt.

```
User rates response (1–5)
        ↓
FeedbackStore records rating
        ↓
FeedbackStore.suggest() checks rolling average against thresholds
        ↓
AdaptiveConfig updates (verbosity / recommendations / uncertainty flags)
        ↓
build_adaptive_agent() rebuilds AgentExecutor with new style instructions
        ↓
Next query gets a different system prompt → measurably different response
```

Python decides the style rule. The LLM executes it. No fine-tuning required.

---

## AdaptiveConfig

Holds the current style preferences for the agent.

| Attribute | Default | Range |
|-----------|---------|-------|
| `verbosity` | `"standard"` | `"standard"` \| `"concise"` \| `"detailed"` |
| `include_recommendations` | `True` | `True` / `False` |
| `uncertainty_sensitivity` | `0.7` | 0.0 (never flag) → 1.0 (always flag) |

### Style Instructions Generated

| Config State | System Prompt Injection |
|-------------|------------------------|
| `verbosity = "standard"` | *(none — uses default behaviour)* |
| `verbosity = "concise"` | "Be concise. Max 3 bullet points. Lead with the answer, skip preamble." |
| `verbosity = "detailed"` | "Be thorough. Include context, trends, and examples. Explain the 'why'." |
| `include_recommendations = False` | "Do NOT include a 'Recommend:' section at the end." |
| `uncertainty_sensitivity < 0.4` | "Only use ⚠️ for genuinely critical missing data." |
| `uncertainty_sensitivity > 0.8` | "Flag any uncertainty or data gap with ⚠️." |

### Adaptation Log

Every config change is recorded with a timestamp:
```python
[{"ts": "2026-05-29T10:15:42", "msg": "verbosity: standard → concise"}, …]
```

---

## FeedbackStore

Records explicit ratings (1–5) per query and surfaces adaptation suggestions.

### Rating Scale

| Rating | Meaning | Action |
|--------|---------|--------|
| 5 | Excellent — keep exactly this | No change (or restore if previously reduced) |
| 4 | Good | No change unless high threshold crossed |
| 3 | Acceptable | No change |
| 2 | Could be better | Review after 3 ratings |
| 1 | Poor | Review after 3 ratings |

### Adaptation Thresholds

| Condition | Action |
|-----------|--------|
| Rolling avg of last 3 ratings < **2.5** | Decrease verbosity (standard → concise → suppress recommendations) |
| Rolling avg of last 3 ratings > **4.0** AND currently concise | Restore verbosity to standard |

### Adaptation Cascade (Low Ratings)

```
verbosity = "detailed"  → restore to "standard"
verbosity = "standard"  → decrease to "concise"
verbosity = "concise" + recommendations on  → suppress recommendations
verbosity = "concise" + recommendations off → "Already at most concise setting"
```

---

## Before / After Measurement

The same 3 test queries are run before and after simulating low ratings (2/5 × 3).

**Test queries:**
1. "How many P1 incidents has auth-service had in the last 30 days?"
2. "What is the SLA breach rate for payments-api?"
3. "Give me a health summary of database-cluster."

**Expected effect:** After `suggest()` reduces verbosity to `"concise"`, response
word counts decrease measurably. The agent still calls the correct tools and
cites data — only the surrounding language becomes more terse.

| Style | Avg Words | Change |
|-------|----------|--------|
| Standard (before) | ~80–120 | — |
| Concise (after)   | ~20–40  | −50–70% reduction typical |

---

## Explicit vs Implicit Feedback

### Explicit (Numeric Rating)

Collected via `FeedbackStore.record(rating=N)` — direct 1–5 signal from the analyst.

### Implicit Signals (Phase 7 demo)

| Signal | Inferred Meaning | Adaptive Action |
|--------|-----------------|-----------------|
| "Can you be shorter?" | Verbosity complaint | decrease verbosity |
| User asks same question again | Response was unclear | switch to detailed mode |
| "Stop with the recommendations" | Recommendation fatigue | suppress Recommend: endings |
| "Why do you keep saying ⚠️?" | Uncertainty flag overuse | decrease uncertainty_sensitivity |
| 3+ follow-up questions on same topic | High engagement | increase verbosity |

Implicit signals map to the same `apply_adaptation(dimension, direction)` calls as
explicit ratings — the feedback source is different, the adaptation mechanism is identical.

---

## What Stays Constant During Adaptation

Adaptation changes **style only**. The following are always enforced regardless of config:

| Invariant | Mechanism |
|-----------|-----------|
| Read-only safety rules | Fixed in `BASE_SYSTEM_PROMPT` before `{style_instructions}` |
| Tool routing accuracy | Tools and their schemas are unchanged |
| Data accuracy | Agent still calls tools; no invented facts |
| Refusal of action requests | Safety section of prompt is not adaptive |

---

## Feedback Persistence

`FeedbackStore.save('phase7_feedback.json')` writes a structured JSON log:

```json
{
  "saved_at": "2026-05-29T...",
  "count": 7,
  "ratings": [
    {"ts": "...", "rating": 2, "dimension": "verbosity",
     "query": "How many P1...", "response_len": 87, "note": "Too long"}
  ]
}
```

---

## Known Limitations Introduced

| ID | Limitation | Impact | Planned Fix |
|----|-----------|--------|-------------|
| KL13 | Config resets between Python sessions | Adaptation lost on kernel restart | Phase 8: API-layer state persists for session duration |
| KL14 | Adaptation is per-session, not per-analyst | Different analysts share the same config | Future: user-keyed config store |
| KL15 | Implicit signals require NLP intent parsing | Phase 7 demo maps them manually | Phase 9: classify via LLM |
| KL16 | No upper bound on adaptation iterations | Could over-correct in long sessions | Future: adaptation history cap |

---

## Files Added in Phase 7

| File | Purpose |
|------|---------|
| `agent/adaptive_agent.py` | `AdaptiveConfig`, `FeedbackStore`, `build_adaptive_agent()`, `run_adaptive()` |
| `work/Phase7_Adaptive.ipynb` | 13-cell Vocareum notebook: before/after comparison, feedback loop, implicit signals, feedback log |

---

*Phase 7 complete. Next: Phase 8 — Deployment Readiness.*
