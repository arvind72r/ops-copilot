"""
OpsPilot — Streamlit Chat UI
Read-only AI Decision Support Copilot for NovaTech IT Operations.

Run:
    streamlit run streamlit_app.py

On Vocareum the OPENAI_API_KEY is set automatically in the environment.
No other configuration is required — data files are loaded from data/.
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
    pass  # pysqlite3 not installed — native sqlite3 will be used

import pandas as pd
import streamlit as st

warnings.filterwarnings('ignore')

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
for p in [str(ROOT), str(ROOT / 'agent'), str(ROOT / 'data')]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Streamlit page config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title  = "OpsPilot",
    page_icon   = "🛡️",
    layout      = "wide",
    initial_sidebar_state = "expanded",
)


# ══════════════════════════════════════════════════════════════════════════════
# Resource loader  (cached for the lifetime of the Streamlit process)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner="Loading OpsPilot — please wait…")
def _load_resources():
    """
    Load data and initialise the agent tool layer exactly once per process.
    Returns (api_key, incident_count, kb_chunk_count).
    """
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. "
            "On Vocareum it is injected automatically; "
            "locally, export it before running."
        )

    data_dir    = ROOT / 'data'
    incidents   = pd.read_csv(data_dir / 'incidents.csv')
    incidents['opened_at'] = pd.to_datetime(incidents['opened_at'])
    sla_targets = pd.read_csv(data_dir / 'sla_targets.csv')

    collection = None
    kb_count   = 0
    try:
        import chromadb
        from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
        chroma  = chromadb.PersistentClient(path=str(data_dir / 'vectorstore'))
        ef      = OpenAIEmbeddingFunction(api_key=api_key, model_name='text-embedding-3-small')
        collection = chroma.get_collection('ops_knowledge', embedding_function=ef)
        kb_count   = collection.count()
    except Exception:
        pass   # ChromaDB optional — structured tools still work

    from tool_agent import init_agent_data
    init_agent_data(incidents, sla_targets, collection)

    return api_key, len(incidents), kb_count


def _build_executor():
    """Build (or rebuild after style change) the adaptive agent executor."""
    from adaptive_agent import build_adaptive_agent
    api_key, _, _ = _load_resources()
    return build_adaptive_agent(api_key, st.session_state.config, verbose=False)


# ══════════════════════════════════════════════════════════════════════════════
# Session state bootstrap
# ══════════════════════════════════════════════════════════════════════════════

def _init_session():
    from memory_agent   import SessionMemory
    from adaptive_agent import AdaptiveConfig, FeedbackStore

    if 'ready' in st.session_state:
        return   # already initialised this session

    st.session_state.ready          = False
    st.session_state.messages       = []     # list of message dicts (see below)
    st.session_state.memory         = SessionMemory(max_turns=10)
    st.session_state.config         = AdaptiveConfig()
    st.session_state.feedback_store = FeedbackStore()
    st.session_state.executor       = None
    st.session_state.query_count    = 0
    st.session_state.total_ms       = 0
    st.session_state.last_msg_idx   = None   # index of last assistant message
    # Message dict schema:
    #   role       : 'user' | 'assistant'
    #   content    : str
    #   query      : str  (for assistant messages — the query that produced them)
    #   tools      : list[{tool, input, output_preview}]
    #   latency_ms : int
    #   word_count : int


_init_session()

# Bootstrap the executor on the very first run
if not st.session_state.ready:
    try:
        _load_resources()
        st.session_state.executor = _build_executor()
        st.session_state.ready    = True
    except Exception as exc:
        st.error(f"❌ Failed to start OpsPilot: {exc}")
        st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:

    # ── Branding & data summary ───────────────────────────────────────────────
    st.markdown("## 🛡️ OpsPilot")
    st.caption("NovaTech IT Operations · AI Decision Support")

    _, inc_count, kb_count = _load_resources()
    st.caption(f"📊 {inc_count:,} incidents  ·  📚 {kb_count} KB chunks")

    if st.session_state.ready:
        st.success("Agent ready", icon="✅")
    else:
        st.error("Agent not ready")

    st.divider()

    # ── Response style ────────────────────────────────────────────────────────
    st.subheader("⚙️ Response style")
    cfg = st.session_state.config
    style_label = {"standard": "🟢 Standard", "concise": "🟡 Concise", "detailed": "🔵 Detailed"}
    st.markdown(f"**Verbosity:** {style_label.get(cfg.verbosity, cfg.verbosity)}")
    st.markdown(f"**Recommendations:** {'on ✅' if cfg.include_recommendations else 'off ❌'}")
    st.markdown(f"**Uncertainty flags:** {cfg.uncertainty_sensitivity:.1f} / 1.0")

    if cfg.adaptation_log:
        with st.expander("Adaptation history", expanded=False):
            for entry in reversed(cfg.adaptation_log[-5:]):
                ts = entry['ts'][11:16]
                st.caption(f"⚙️ {ts}  {entry['msg']}")

    st.divider()

    # ── Feedback ──────────────────────────────────────────────────────────────
    st.subheader("⭐ Rate last response")

    if st.session_state.last_msg_idx is not None:
        rating = st.select_slider(
            "Quality  (1 = poor  ·  5 = excellent)",
            options=[1, 2, 3, 4, 5],
            value=4,
            key=f"rating_slider_{st.session_state.query_count}",
        )
        if st.button("Submit rating", use_container_width=True):
            last_msg = st.session_state.messages[st.session_state.last_msg_idx]
            st.session_state.feedback_store.record(
                query    = last_msg.get('query', ''),
                response = last_msg.get('content', ''),
                rating   = rating,
            )
            change = st.session_state.feedback_store.suggest(st.session_state.config)
            if any(kw in change for kw in ['→', 'restored', 'suppressed']):
                st.session_state.executor = _build_executor()   # rebuild with new style
                st.toast(f"Style adapted: {change}", icon="⚙️")
            else:
                st.toast(f"Feedback recorded — {change}", icon="⭐")

        fb_summary = st.session_state.feedback_store.summary()
        if fb_summary != "No feedback recorded.":
            st.caption(fb_summary)
    else:
        st.caption("Send a message to enable feedback.")

    st.divider()

    # ── Session metrics ───────────────────────────────────────────────────────
    st.subheader("📈 Session metrics")
    col_q, col_l = st.columns(2)
    col_q.metric("Queries", st.session_state.query_count)
    if st.session_state.query_count:
        avg = st.session_state.total_ms // st.session_state.query_count
        col_l.metric("Avg latency", f"{avg:,} ms")
    else:
        col_l.metric("Avg latency", "—")

    mem_turns = len(st.session_state.memory)
    st.caption(f"💬 {mem_turns} turn(s) in memory  (window: 10)")

    st.divider()

    # ── Controls ──────────────────────────────────────────────────────────────
    if st.button("🔄  New conversation", use_container_width=True):
        from memory_agent import SessionMemory
        st.session_state.messages     = []
        st.session_state.memory       = SessionMemory(max_turns=10)
        st.session_state.last_msg_idx = None
        st.session_state.query_count  = 0
        st.session_state.total_ms     = 0
        st.rerun()

    if st.button("🎨  Reset style to standard", use_container_width=True):
        from adaptive_agent import AdaptiveConfig, FeedbackStore
        st.session_state.config         = AdaptiveConfig()
        st.session_state.feedback_store = FeedbackStore()
        st.session_state.executor       = _build_executor()
        st.toast("Style reset to standard.", icon="🎨")
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CHAT AREA
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("# 🛡️ OpsPilot")
st.caption(
    "Read-only AI Decision Support for NovaTech IT Operations. "
    "Answers are grounded in live incident data, SLA records, and the runbook."
)

# ── Example query chips (shown only when the chat is empty) ──────────────────
if not st.session_state.messages:
    st.markdown("##### Try asking:")
    examples = [
        "Is database-cluster healthy right now?",
        "Which service has the highest SLA breach rate?",
        "How many P1 incidents has payments-api had in the last 30 days?",
        "Which two services should the shift analyst focus on first?",
        "What does the runbook say about P1 incident response?",
        "Give me a 5-minute briefing on overall fleet health.",
    ]
    cols = st.columns(3)
    for i, ex in enumerate(examples):
        if cols[i % 3].button(ex, use_container_width=True, key=f"ex_{i}"):
            st.session_state['_pending_query'] = ex
            st.rerun()

# ── Render conversation history ───────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        if msg["role"] == "assistant":
            tools      = msg.get("tools", [])
            latency_ms = msg.get("latency_ms", 0)
            word_count = msg.get("word_count", 0)

            if tools:
                tool_names = " → ".join(t["tool"] for t in tools)
                with st.expander(
                    f"🔧 {tool_names}   ·   {latency_ms:,} ms   ·   {word_count} words",
                    expanded=False,
                ):
                    for tc in tools:
                        st.markdown(f"**`{tc['tool']}`**")
                        inp = tc.get("input")
                        if inp:
                            st.json(inp, expanded=False)
                        preview = tc.get("output_preview", "")
                        if preview:
                            st.code(preview[:500], language="json")
            else:
                st.caption(f"⏱ {latency_ms:,} ms   ·   {word_count} words")

# ── Chat input ────────────────────────────────────────────────────────────────
# Pick up either a directly typed query or an example-button injection.
pending = None
if '_pending_query' in st.session_state:
    pending = st.session_state['_pending_query']
    del st.session_state['_pending_query']

user_input = st.chat_input("Ask about incidents, SLA, service health, runbooks…") or pending

if user_input:
    from memory_agent   import check_reset_trigger, SessionMemory
    from adaptive_agent import run_adaptive

    # ── Handle explicit memory-reset phrases ─────────────────────────────────
    if check_reset_trigger(user_input):
        st.session_state.memory       = SessionMemory(max_turns=10)
        st.session_state.messages     = []
        st.session_state.last_msg_idx = None
        st.session_state.query_count  = 0
        st.session_state.total_ms     = 0
        st.toast("Conversation memory cleared. Starting fresh.", icon="🔄")
        st.rerun()

    # ── Render user bubble immediately ────────────────────────────────────────
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # ── Run agent and render assistant bubble ─────────────────────────────────
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

    # ── Persist to session state ──────────────────────────────────────────────
    assistant_idx = len(st.session_state.messages)
    st.session_state.messages.append({
        "role":       "assistant",
        "content":    response,
        "query":      user_input,
        "tools":      tools,
        "latency_ms": latency_ms,
        "word_count": word_count,
    })
    st.session_state.last_msg_idx  = assistant_idx
    st.session_state.query_count  += 1
    st.session_state.total_ms     += latency_ms

    st.rerun()
