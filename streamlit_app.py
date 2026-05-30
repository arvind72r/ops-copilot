"""
OpsPilot — Streamlit Chat UI
Read-only AI Decision Support Copilot for NovaTech IT Operations.

Run:
    streamlit run streamlit_app.py

On Vocareum the OPENAI_API_KEY is set automatically in the environment.
"""

import os
import sys
import warnings
from pathlib import Path

# ── SQLite3 patch — must come before any chromadb import ─────────────────────
try:
    __import__('pysqlite3')
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

import pandas as pd
import streamlit as st

warnings.filterwarnings('ignore')

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
for p in [str(ROOT), str(ROOT / 'agent'), str(ROOT / 'data')]:
    if p not in sys.path:
        sys.path.insert(0, p)

from tool_agent     import init_agent_data
from memory_agent   import SessionMemory, check_reset_trigger
from adaptive_agent import AdaptiveConfig, FeedbackStore, build_adaptive_agent, run_adaptive

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="OpsPilot",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# Resource loader — runs ONCE per Streamlit process (same pattern as HR chatbot)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource
def load_data():
    """Load incidents, SLA targets, and ChromaDB. Called once; result is cached."""
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        st.error("❌ OPENAI_API_KEY not set. On Vocareum it is injected automatically.")
        st.stop()

    data_dir    = ROOT / 'data'
    incidents   = pd.read_csv(data_dir / 'incidents.csv')
    incidents['opened_at'] = pd.to_datetime(incidents['opened_at'])
    sla_targets = pd.read_csv(data_dir / 'sla_targets.csv')

    collection = None
    kb_count   = 0
    try:
        import chromadb
        from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
        chroma     = chromadb.PersistentClient(path=str(data_dir / 'vectorstore'))
        ef         = OpenAIEmbeddingFunction(api_key=api_key, model_name='text-embedding-3-small')
        collection = chroma.get_collection('ops_knowledge', embedding_function=ef)
        kb_count   = collection.count()
    except Exception:
        pass  # KB is optional — structured tools still work without it

    init_agent_data(incidents, sla_targets, collection)
    return api_key, len(incidents), kb_count


# Load data at module level — exactly like the HR chatbot loads its vectorstore
api_key, inc_count, kb_count = load_data()

# ── Per-session state — simple guards, same pattern as HR chatbot ─────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

if "memory" not in st.session_state:
    st.session_state.memory = SessionMemory(max_turns=10)

if "config" not in st.session_state:
    st.session_state.config = AdaptiveConfig()

if "feedback_store" not in st.session_state:
    st.session_state.feedback_store = FeedbackStore()

if "executor" not in st.session_state:
    st.session_state.executor = build_adaptive_agent(api_key, st.session_state.config)

if "last_response" not in st.session_state:
    st.session_state.last_response = None   # {"query", "content"} for feedback


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## 🛡️ OpsPilot")
    st.caption("NovaTech IT Operations · AI Decision Support")
    st.caption(f"📊 {inc_count:,} incidents  ·  📚 {kb_count} KB chunks")
    st.success("Agent ready", icon="✅")

    st.divider()

    # ── Response style ────────────────────────────────────────────────────────
    st.subheader("⚙️ Response style")
    cfg = st.session_state.config
    style_label = {"standard": "🟢 Standard", "concise": "🟡 Concise", "detailed": "🔵 Detailed"}
    st.markdown(f"**Verbosity:** {style_label.get(cfg.verbosity, cfg.verbosity)}")
    st.markdown(f"**Recommendations:** {'on ✅' if cfg.include_recommendations else 'off ❌'}")
    st.markdown(f"**Uncertainty flags:** {cfg.uncertainty_sensitivity:.1f}")

    if cfg.adaptation_log:
        with st.expander("Adaptation history", expanded=False):
            for entry in reversed(cfg.adaptation_log[-5:]):
                st.caption(f"⚙️ {entry['ts'][11:16]}  {entry['msg']}")

    st.divider()

    # ── Feedback ──────────────────────────────────────────────────────────────
    st.subheader("⭐ Rate last response")
    if st.session_state.last_response is not None:
        rating = st.select_slider(
            "Quality  (1 = poor · 5 = excellent)",
            options=[1, 2, 3, 4, 5],
            value=4,
            key="rating_slider",
        )
        if st.button("Submit rating", use_container_width=True):
            st.session_state.feedback_store.record(
                query    = st.session_state.last_response["query"],
                response = st.session_state.last_response["content"],
                rating   = rating,
            )
            change = st.session_state.feedback_store.suggest(st.session_state.config)
            if any(kw in change for kw in ['→', 'restored', 'suppressed']):
                # Style changed — rebuild executor with updated config
                st.session_state.executor = build_adaptive_agent(
                    api_key, st.session_state.config
                )
                st.toast(f"Style adapted: {change}", icon="⚙️")
            else:
                st.toast(f"Feedback recorded — {change}", icon="⭐")

        fb = st.session_state.feedback_store.summary()
        if fb != "No feedback recorded.":
            st.caption(fb)
    else:
        st.caption("Send a message to enable feedback.")

    st.divider()

    # ── Session metrics ───────────────────────────────────────────────────────
    st.subheader("📈 Session")
    n_msgs = len([m for m in st.session_state.messages if m["role"] == "assistant"])
    st.metric("Queries answered", n_msgs)
    st.caption(f"💬 {len(st.session_state.memory)} turn(s) in memory  (window: 10)")

    st.divider()

    # ── Controls ──────────────────────────────────────────────────────────────
    if st.button("🔄  New conversation", use_container_width=True):
        st.session_state.messages      = []
        st.session_state.memory        = SessionMemory(max_turns=10)
        st.session_state.last_response = None
        st.rerun()

    if st.button("🎨  Reset style to standard", use_container_width=True):
        st.session_state.config         = AdaptiveConfig()
        st.session_state.feedback_store = FeedbackStore()
        st.session_state.executor       = build_adaptive_agent(api_key, st.session_state.config)
        st.toast("Style reset to standard.", icon="🎨")
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CHAT AREA
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("# 🛡️ OpsPilot")
st.caption(
    "Read-only AI Decision Support for NovaTech IT Operations. "
    "Answers are grounded in live incident data, SLA records, and runbooks."
)

# ── Example query chips — shown only when chat is empty ──────────────────────
EXAMPLES = [
    "Is database-cluster healthy right now?",
    "Which service has the highest SLA breach rate?",
    "How many P1 incidents has payments-api had in the last 30 days?",
    "Which two services should the shift analyst focus on first?",
    "What does the runbook say about P1 incident response?",
    "Give me a 5-minute briefing on overall fleet health.",
]

selected_example = None
if not st.session_state.messages:
    st.markdown("##### Try asking:")
    cols = st.columns(3)
    for i, ex in enumerate(EXAMPLES):
        if cols[i % 3].button(ex, use_container_width=True, key=f"ex_{i}"):
            selected_example = ex   # captured in this run — no rerun needed

# ── Render conversation history ───────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("tools"):
            tool_names = " → ".join(t["tool"] for t in msg["tools"])
            with st.expander(
                f"🔧 {tool_names}   ·   {msg.get('latency_ms', 0):,} ms"
                f"   ·   {msg.get('word_count', 0)} words",
                expanded=False,
            ):
                for tc in msg["tools"]:
                    st.markdown(f"**`{tc['tool']}`**")
                    if tc.get("input"):
                        st.json(tc["input"], expanded=False)
                    if tc.get("output_preview", ""):
                        st.code(tc["output_preview"][:500], language="json")
        elif msg["role"] == "assistant":
            st.caption(
                f"⏱ {msg.get('latency_ms', 0):,} ms   ·   {msg.get('word_count', 0)} words"
            )

# ── Chat input (or example button click) ─────────────────────────────────────
user_input = st.chat_input("Ask about incidents, SLA, service health, runbooks…") or selected_example

if user_input:

    # ── Detect explicit memory-reset phrases ─────────────────────────────────
    if check_reset_trigger(user_input):
        st.session_state.memory        = SessionMemory(max_turns=10)
        st.session_state.messages      = []
        st.session_state.last_response = None
        st.info("Conversation memory cleared. Starting fresh.")
        st.stop()

    # ── Show user bubble ──────────────────────────────────────────────────────
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # ── Run the agent and show response ──────────────────────────────────────
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            result = run_adaptive(
                st.session_state.executor,
                user_input,
                st.session_state.memory,
            )

        response   = result.get("response") or "⚠️ No response returned."
        tools      = result.get("tool_calls", [])
        latency_ms = result.get("latency_ms", 0)
        word_count = result.get("word_count", 0)
        error      = result.get("error")

        if error:
            st.error(f"Agent error: {error}")
            response = f"⚠️ {error}"
        else:
            st.markdown(response)

        if tools:
            tool_names = " → ".join(t["tool"] for t in tools)
            with st.expander(
                f"🔧 {tool_names}   ·   {latency_ms:,} ms   ·   {word_count} words",
                expanded=False,
            ):
                for tc in tools:
                    st.markdown(f"**`{tc['tool']}`**")
                    if tc.get("input"):
                        st.json(tc["input"], expanded=False)
                    if tc.get("output_preview", ""):
                        st.code(tc["output_preview"][:500], language="json")
        else:
            st.caption(f"⏱ {latency_ms:,} ms   ·   {word_count} words")

    # ── Append assistant message and update feedback target ───────────────────
    # (no st.rerun() — inline rendering already shows the new turn)
    st.session_state.messages.append({
        "role":       "assistant",
        "content":    response,
        "tools":      tools,
        "latency_ms": latency_ms,
        "word_count": word_count,
    })
    st.session_state.last_response = {"query": user_input, "content": response}
