"""
Phase 7 — Adaptive Behaviour & Feedback Signals
OpsPilot: Feedback-driven prompt adaptation.

How it works
------------
1. Agent answers a query in a given style (standard / concise / detailed).
2. User rates the response (1–5).
3. FeedbackStore records the rating.
4. AdaptiveConfig.update() reads the trend and adjusts style if needed.
5. Next query gets a modified system prompt matching the new style.
6. Before/after comparison shows measurable change in response length/structure.

Adaptive dimensions
-------------------
  verbosity        : standard → concise (too verbose) or detailed (too shallow)
  recommendations  : include/suppress "Recommend:" endings
  uncertainty_flags: how aggressively to use ⚠️

Design principle: adaptation lives in the *prompt*, not the model.
Python decides the style rule; LLM executes it. No fine-tuning required.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tool_agent    import TOOLS, init_agent_data          # noqa: F401
from memory_agent  import SessionMemory, run_with_memory  # noqa: F401


# ── Adaptive Configuration ─────────────────────────────────────────────────────

class AdaptiveConfig:
    """
    Holds the current style preferences for the agent.
    Updated by FeedbackStore when ratings cross thresholds.

    Attributes
    ----------
    verbosity              : 'standard' | 'concise' | 'detailed'
    include_recommendations: whether to end responses with Recommend:
    uncertainty_sensitivity: 0.0 (never flag) → 1.0 (always flag)
    adaptation_log         : list of (timestamp, change_description) entries
    """

    def __init__(self):
        self.verbosity               = "standard"
        self.include_recommendations = True
        self.uncertainty_sensitivity = 0.7
        self.adaptation_log: list    = []

    def style_instructions(self) -> str:
        """Return a prompt snippet reflecting the current config."""
        lines = []

        if self.verbosity == "concise":
            lines.append(
                "STYLE: Be concise. Max 3 bullet points. "
                "Lead with the answer, skip preamble and elaboration."
            )
        elif self.verbosity == "detailed":
            lines.append(
                "STYLE: Be thorough. Include context, trends, and examples. "
                "Explain the 'why' behind each data point."
            )
        # 'standard' → no override; use default prompt behaviour

        if not self.include_recommendations:
            lines.append("Do NOT include a 'Recommend:' section at the end.")

        if self.uncertainty_sensitivity < 0.4:
            lines.append("Only use ⚠️ for genuinely critical missing data.")
        elif self.uncertainty_sensitivity > 0.8:
            lines.append("Flag any uncertainty or data gap with ⚠️.")

        return "\n".join(lines) if lines else ""

    def _log(self, msg: str) -> None:
        self.adaptation_log.append({
            "ts":  datetime.now().isoformat(),
            "msg": msg,
        })

    def apply_adaptation(self, dimension: str, direction: str) -> str:
        """
        Directly apply an adaptation. Called by FeedbackStore.suggest().

        dimension : 'verbosity' | 'recommendations' | 'uncertainty'
        direction : 'increase' | 'decrease' | 'suppress' | 'restore'
        """
        msg = ""
        if dimension == "verbosity":
            if direction == "decrease" and self.verbosity != "concise":
                self.verbosity = "concise"
                msg = "verbosity: standard → concise"
            elif direction == "increase" and self.verbosity != "detailed":
                self.verbosity = "detailed"
                msg = "verbosity: standard → detailed"
            elif direction == "restore":
                self.verbosity = "standard"
                msg = "verbosity: restored to standard"

        elif dimension == "recommendations":
            if direction == "suppress" and self.include_recommendations:
                self.include_recommendations = False
                msg = "recommendations: suppressed"
            elif direction == "restore" and not self.include_recommendations:
                self.include_recommendations = True
                msg = "recommendations: restored"

        elif dimension == "uncertainty":
            if direction == "decrease":
                self.uncertainty_sensitivity = max(0.2, self.uncertainty_sensitivity - 0.2)
                msg = f"uncertainty_sensitivity → {self.uncertainty_sensitivity:.1f}"
            elif direction == "increase":
                self.uncertainty_sensitivity = min(1.0, self.uncertainty_sensitivity + 0.2)
                msg = f"uncertainty_sensitivity → {self.uncertainty_sensitivity:.1f}"

        if msg:
            self._log(msg)
        return msg or "no change"

    def __repr__(self):
        return (f"AdaptiveConfig(verbosity={self.verbosity!r}, "
                f"recommendations={self.include_recommendations}, "
                f"uncertainty={self.uncertainty_sensitivity:.1f})")


# ── Feedback Store ─────────────────────────────────────────────────────────────

class FeedbackStore:
    """
    Stores explicit ratings (1–5) per query and surfaces adaptation suggestions.

    Rating scale
    ------------
    5 — excellent, keep exactly this style
    4 — good
    3 — acceptable
    2 — could be better (triggers review)
    1 — poor (triggers immediate adaptation)

    Thresholds
    ----------
    avg < 2.5 in last 3 ratings → suggest decrease verbosity
    avg > 4.0 in last 3 ratings → suggest restore if previously reduced
    """

    LOW_THRESHOLD  = 2.5
    HIGH_THRESHOLD = 4.0
    WINDOW         = 3     # how many recent ratings to average

    def __init__(self, log_dir: str = "logs"):
        self.ratings: list = []
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)

    def record(
        self,
        query:     str,
        response:  str,
        rating:    int,
        dimension: str = "overall",
        note:      str = "",
    ) -> None:
        """Record a user rating. Rating must be 1–5."""
        if not 1 <= rating <= 5:
            raise ValueError(f"Rating must be 1–5, got {rating}")
        self.ratings.append({
            "ts":        datetime.now().isoformat(),
            "query":     query[:120],
            "response_len": len(response.split()),
            "rating":    rating,
            "dimension": dimension,
            "note":      note,
        })

    def recent_avg(self, n: int = WINDOW) -> Optional[float]:
        """Average rating over the last n entries. None if no ratings yet."""
        recent = self.ratings[-n:]
        return sum(r["rating"] for r in recent) / len(recent) if recent else None

    def suggest(self, config: AdaptiveConfig) -> str:
        """
        Check recent ratings and apply one adaptation if warranted.
        Returns a description of what changed (or 'no change needed').
        """
        avg = self.recent_avg()
        if avg is None:
            return "No feedback recorded yet."

        if avg < self.LOW_THRESHOLD:
            if config.verbosity == "detailed":
                return config.apply_adaptation("verbosity", "restore")
            elif config.verbosity == "standard":
                return config.apply_adaptation("verbosity", "decrease")
            elif config.verbosity == "concise" and not config.include_recommendations:
                return "Already at most concise setting."
            elif config.verbosity == "concise":
                return config.apply_adaptation("recommendations", "suppress")

        elif avg > self.HIGH_THRESHOLD and config.verbosity == "concise":
            return config.apply_adaptation("verbosity", "restore")

        return f"Avg rating {avg:.1f}/5 — no adaptation needed."

    def summary(self) -> str:
        if not self.ratings:
            return "No feedback recorded."
        avg = self.recent_avg(len(self.ratings))
        return (f"{len(self.ratings)} rating(s) | "
                f"Overall avg: {avg:.1f}/5 | "
                f"Last 3 avg: {self.recent_avg():.1f}/5")

    def save(self, filename: str = "feedback_log.json") -> str:
        path = self.log_dir / filename
        path.write_text(json.dumps({
            "saved_at": datetime.now().isoformat(),
            "count":    len(self.ratings),
            "ratings":  self.ratings,
        }, indent=2))
        return str(path)


# ── Adaptive Agent Factory ─────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are OpsPilot — a read-only AI Decision Support Copilot for NovaTech IT Operations.

AVAILABLE TOOLS:
  • query_incidents      — incident counts, MTTR, trends
  • check_sla_breaches   — SLA compliance and breach analysis
  • get_service_health   — per-service health snapshot
  • search_runbook       — escalation steps, known issues

SAFETY RULES:
- READ-ONLY. Never suggest system-modifying actions.
- Never fabricate data. Cite tool name for key facts.
- Action requests: refuse and recommend escalation.
- Out of scope (HR, budget, staffing): decline politely.

{style_instructions}
"""


def build_adaptive_agent(
    api_key: str,
    config:  AdaptiveConfig,
    verbose: bool = False,
) -> AgentExecutor:
    """
    Build an AgentExecutor whose system prompt reflects the current AdaptiveConfig.
    Call again after config changes to get an agent with updated instructions.
    """
    style = config.style_instructions()
    system_prompt = BASE_SYSTEM_PROMPT.format(
        style_instructions=style if style else "STYLE: Standard — balanced detail and brevity."
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, api_key=api_key)

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
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


def run_adaptive(
    executor: AgentExecutor,
    query:    str,
    memory:   SessionMemory,
) -> dict:
    """
    Run a query through the adaptive agent with memory.
    Returns the standard result dict plus word_count for comparison.
    """
    result = run_with_memory(executor, query, memory)
    result["word_count"] = len((result["response"] or "").split())
    return result
