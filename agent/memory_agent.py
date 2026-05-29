"""
Phase 6 — Planning, Memory & Context
OpsPilot: Session memory, multi-turn context, persistence, and reset rules.

Key additions over Phase 5
--------------------------
- SessionMemory  : stores (human, ai) turn pairs; injected as chat_history
- Memory-aware AgentExecutor  : resolves pronouns and follow-up references
- Session save / load         : persist to JSON, restore on next run
- Reset rules                 : explicit trigger + shift-boundary auto-reset
- run_with_memory()           : wrapper that tracks and injects history

Usage
-----
    from agent.memory_agent import (
        SessionMemory, build_memory_agent, run_with_memory,
        check_reset_trigger, should_auto_reset
    )

    mem      = SessionMemory(max_turns=10)
    executor = build_memory_agent(api_key=os.environ["OPENAI_API_KEY"])

    # Turn 1
    r1 = run_with_memory(executor, "How many incidents has auth-service had?", mem)
    # Turn 2 — agent resolves "it" from memory
    r2 = run_with_memory(executor, "What is its SLA breach rate?", mem)
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage

# Re-use Phase 5 tools and base system prompt unchanged
from tool_agent import TOOLS, SYSTEM_PROMPT, init_agent_data   # noqa: F401


# ── Session Memory ─────────────────────────────────────────────────────────────

class SessionMemory:
    """
    Lightweight sliding-window conversation memory for OpsPilot.

    Stores (human, ai) turn pairs and provides:
      - LangChain message conversion  → inject as chat_history
      - JSON persistence              → save / load across runs
      - Hard reset (clear all)        → new shift, explicit request
      - Soft reset (keep last 1 turn) → context seed without full history
    """

    def __init__(self, max_turns: int = 10, session_dir: str = "logs"):
        self.max_turns     = max_turns
        self.turns: list   = []           # [{human, ai, ts}, ...]
        self.session_start = datetime.now()
        self.session_dir   = Path(session_dir)
        self.session_dir.mkdir(exist_ok=True)

    # ── Core operations ───────────────────────────────────────────────────────

    def add_turn(self, human: str, ai: str) -> None:
        """Append a completed turn and enforce the sliding window."""
        self.turns.append({
            "human": human,
            "ai":    ai,
            "ts":    datetime.now().isoformat(),
        })
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def to_langchain_messages(self) -> list:
        """Return chat_history as LangChain HumanMessage / AIMessage pairs."""
        msgs = []
        for t in self.turns:
            msgs.append(HumanMessage(content=t["human"]))
            msgs.append(AIMessage(content=t["ai"]))
        return msgs

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, filename: Optional[str] = None) -> str:
        """Persist full session to JSON. Returns the file path written."""
        if not filename:
            ts       = self.session_start.strftime("%Y%m%d_%H%M%S")
            filename = f"session_{ts}.json"
        path = self.session_dir / filename
        path.write_text(json.dumps({
            "session_start": self.session_start.isoformat(),
            "saved_at":      datetime.now().isoformat(),
            "turn_count":    len(self.turns),
            "turns":         self.turns,
        }, indent=2))
        return str(path)

    @classmethod
    def load(cls, path: str, max_turns: int = 10) -> "SessionMemory":
        """
        Restore a previously saved session.
        Only the most recent max_turns turns are loaded (respects window size).
        """
        data  = json.loads(Path(path).read_text())
        mem   = cls(max_turns=max_turns)
        mem.session_start = datetime.fromisoformat(data["session_start"])
        mem.turns         = data["turns"][-max_turns:]
        return mem

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self, hard: bool = True) -> str:
        """
        Reset conversation memory.

        hard=True  → wipe everything; start a fresh session clock.
                     Use for: new shift, explicit "reset" command.
        hard=False → keep only the last turn as a context seed.
                     Use for: topic change within the same shift.
        """
        n = len(self.turns)
        if hard:
            self.turns         = []
            self.session_start = datetime.now()
            return f"Hard reset: {n} turn(s) cleared. Fresh session started."
        else:
            self.turns = self.turns[-1:] if self.turns else []
            kept       = len(self.turns)
            return f"Soft reset: cleared {n - kept} turn(s), kept {kept} as context seed."

    # ── Helpers ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        if not self.turns:
            return "No conversation yet."
        age_min = round((datetime.now() - self.session_start).total_seconds() / 60, 1)
        recent  = [t["human"][:55] for t in self.turns[-3:]]
        return (f"{len(self.turns)} turn(s) | "
                f"Session age: {age_min} min | "
                f"Recent: {' → '.join(recent)}")

    def __len__(self):
        return len(self.turns)

    def __repr__(self):
        return f"SessionMemory(turns={len(self.turns)}, max={self.max_turns})"


# ── Reset Rules ────────────────────────────────────────────────────────────────

RESET_TRIGGERS = [
    "new session", "start over", "reset", "clear history",
    "clear memory", "fresh start", "new shift", "beginning of shift",
]

def check_reset_trigger(query: str) -> bool:
    """Return True if the query contains an explicit reset phrase."""
    return any(t in query.lower() for t in RESET_TRIGGERS)


def should_auto_reset(memory: SessionMemory, max_hours: float = 12.0) -> bool:
    """
    Return True if the session has exceeded max_hours (shift boundary).
    In production this would fire at shift handoff time.
    """
    if not memory.turns:
        return False
    age_h = (datetime.now() - memory.session_start).total_seconds() / 3600
    return age_h >= max_hours


# ── System Prompt with Memory Rules ───────────────────────────────────────────

MEMORY_SYSTEM_PROMPT = SYSTEM_PROMPT + """

MEMORY & CONTEXT RULES:
- The conversation history (chat_history) shows what has already been discussed.
- Use it to resolve pronouns: "that service", "the one with more incidents", "it",
  "the worse one", "both of them", "the first one you mentioned".
- Do not repeat data already given this session unless explicitly asked to.
- When a question is ambiguous, check chat_history before asking for clarification.
- If the user says 'reset', 'new session', 'start over', or 'clear history',
  acknowledge it — the system handles the actual memory wipe externally.
- At the start of a restored session, acknowledge the loaded history briefly.
"""


# ── Memory-Aware Agent Factory ─────────────────────────────────────────────────

def build_memory_agent(api_key: str, verbose: bool = False) -> AgentExecutor:
    """
    Build an AgentExecutor that accepts chat_history for multi-turn memory.

    The prompt has a MessagesPlaceholder("chat_history") slot that receives
    the full turn history from SessionMemory on every call.
    """
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, api_key=api_key)

    prompt = ChatPromptTemplate.from_messages([
        ("system", MEMORY_SYSTEM_PROMPT),
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


# ── Query Runner with Memory ───────────────────────────────────────────────────

def run_with_memory(
    executor: AgentExecutor,
    query: str,
    memory: SessionMemory,
) -> dict:
    """
    Run one query with full conversation history injected.
    Stores the completed turn in memory automatically.

    Returns a dict with: query, response, tool_calls, latency_ms,
                         memory_turns (after this turn), error.
    """
    t0 = time.time()
    try:
        result     = executor.invoke({
            "input":        query,
            "chat_history": memory.to_langchain_messages(),
        })
        latency_ms = round((time.time() - t0) * 1000)
        response   = result["output"]

        memory.add_turn(human=query, ai=response)

        tool_calls = [
            {
                "tool":           a.tool,
                "input":          a.tool_input,
                "output_preview": str(obs)[:200],
            }
            for a, obs in result.get("intermediate_steps", [])
        ]

        return {
            "query":        query,
            "response":     response,
            "tool_calls":   tool_calls,
            "latency_ms":   latency_ms,
            "memory_turns": len(memory),
            "error":        None,
        }
    except Exception as exc:
        return {
            "query":        query,
            "response":     None,
            "tool_calls":   [],
            "latency_ms":   round((time.time() - t0) * 1000),
            "memory_turns": len(memory),
            "error":        str(exc),
        }
