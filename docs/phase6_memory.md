# Phase 6 — Planning, Memory & Multi-turn Context
## Session Memory Design & Evaluation Report

**Agent:** OpsPilot | **Framework:** LangChain | **Model:** gpt-4o-mini | **Memory:** Sliding window (10 turns)

---

## The Problem Phase 5 Left Unsolved

A Phase 5 (stateless) agent cannot resolve pronoun references across turns:

```
Turn 1: "How many incidents has auth-service had?" → answered correctly
Turn 2: "What is its SLA breach rate?"             → "its" is ambiguous — agent fails
```

Phase 6 adds a `SessionMemory` layer that injects the full conversation
history as `chat_history` into every AgentExecutor invocation, enabling
pronoun resolution, topic continuity, and multi-turn reasoning.

---

## SessionMemory Design

```python
class SessionMemory:
    max_turns:    int  = 10      # sliding window — oldest turn dropped when full
    turns:        list           # [{human, ai, ts}, …]
    session_start: datetime
```

### Core Operations

| Method | Behaviour |
|--------|-----------|
| `add_turn(human, ai)` | Appends turn; enforces `max_turns` sliding window |
| `to_langchain_messages()` | Converts to `[HumanMessage, AIMessage, …]` for `chat_history` |
| `save(filename)` | Persists to `logs/<filename>.json` |
| `SessionMemory.load(path)` | Restores from JSON; respects `max_turns` on reload |
| `reset(hard=True)` | Hard reset: clears all turns, resets session clock |
| `reset(hard=False)` | Soft reset: keeps only the last turn as context seed |

### Sliding Window

```
max_turns = 10
Turn 11 arrives → Turn 1 is dropped → always the 10 most recent turns are in context
```

This caps token cost and avoids context-length errors, at the cost of losing
early-session context after long conversations (KL9).

---

## Memory Injection Pattern

```python
# Every query passes the full turn history
result = executor.invoke({
    "input":        query,
    "chat_history": memory.to_langchain_messages(),  # ← key addition
})
# Store completed turn
memory.add_turn(human=query, ai=result["output"])
```

The `ChatPromptTemplate` has a `MessagesPlaceholder("chat_history", optional=True)`
slot. When `chat_history` is empty (Turn 1), it behaves identically to Phase 5.

---

## System Prompt Extension

`MEMORY_SYSTEM_PROMPT` extends the Phase 5 system prompt with explicit resolution rules:

```
MEMORY & CONTEXT RULES:
- Use chat_history to resolve pronouns: "that service", "the one with more incidents",
  "it", "the worse one", "both of them", "the first one you mentioned".
- Do not repeat data already given this session unless explicitly asked.
- When a question is ambiguous, check chat_history before asking for clarification.
```

---

## Reset Rules

### Explicit Trigger

The function `check_reset_trigger(query)` matches against a keyword list:

```
"new session", "start over", "reset", "clear history",
"clear memory", "fresh start", "new shift", "beginning of shift"
```

When matched, the application layer calls `memory.reset(hard=True)` before invoking the agent.

### Soft Reset (Topic Change)

`memory.reset(hard=False)` retains only the last turn as a "seed" — useful when
the analyst switches focus within the same shift without wanting to lose the most
recent context.

### Auto-Reset (Shift Boundary)

`should_auto_reset(memory, max_hours=12.0)` returns `True` when the session
has exceeded 12 hours. In production, this fires at the shift-handoff boundary.

---

## Demo Results

### Demo 1 — Pronoun Resolution

| Turn | Query | Memory Used? | Resolved Correctly? |
|------|-------|-------------|---------------------|
| 1 | "How many incidents has auth-service had?" | None (Turn 1) | n/a |
| 2 | "What is **its** SLA breach rate?" | Turn 1 | ✅ resolved "its" → auth-service |
| 3 | "Is that rate higher or lower than payments-api?" | Turns 1–2 | ✅ compared correctly |

### Demo 2 — Memory vs No Memory (Same Query)

```
Query: "What is its SLA breach rate?"  (no prior context)

WITHOUT memory → "I'm not sure which service you're referring to. Could you clarify?"
WITH memory    → (resolved "its" from Turn 1) → returns auth-service breach rate
```

### Demo 3 — Multi-turn Shift Handoff (4 turns)

| Turn | Query | Tools Called |
|------|-------|-------------|
| 1 | "Give me a health summary of auth-service." | get_service_health |
| 2 | "And what about payments-api?" | get_service_health |
| 3 | "Which of those two has more open incidents?" | query_incidents |
| 4 | "Which service should the incoming shift analyst prioritise and why?" | **none** — synthesised from memory |

Turn 4 called zero tools, demonstrating that accumulated memory enables reasoning
without redundant API calls.

### Demo 4 — Save / Load / Resume

Session saved to `logs/demo_session.json` after 4 turns.
Restored via `SessionMemory.load()`, resumed with:

```
"Remind me which service we flagged as higher priority."
→ Agent correctly recalled from restored history
```

---

## Known Limitations Introduced

| ID | Limitation | Impact | Planned Fix |
|----|-----------|--------|-------------|
| KL9 | Sliding window loses turns > 10 | Early context dropped in long sessions | Phase 9: summarisation |
| KL10 | No compression — old turns dropped, not summarised | Information lost, not condensed | Future: summary buffer |
| KL11 | Memory is per-session, in-process | Not shared across analysts or machines | Phase 8: API layer |
| KL12 | Auto-reset uses wall-clock time | No timezone or shift-schedule awareness | Production: configurable schedule |

---

## Files Added in Phase 6

| File | Purpose |
|------|---------|
| `agent/memory_agent.py` | `SessionMemory`, `build_memory_agent()`, `run_with_memory()`, reset utilities |
| `notebooks/Phase6_Memory.ipynb` | 12-cell Vocareum notebook: pronoun resolution, save/load, reset demos, multi-step planning |

---

*Phase 6 complete. Next: Phase 7 — Adaptive Behaviour & Feedback Signals.*
