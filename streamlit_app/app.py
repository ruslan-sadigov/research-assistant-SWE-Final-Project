"""Streamlit front end for the research assistant.

Talks to the same FastAPI `/ask` endpoint the React app uses (see
`src/webapi/api.py`). This file contains no research logic of its own -
it only collects input, calls the API, and renders the response.
"""

import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
import streamlit as st

API_URL = os.environ.get("RESEARCH_API_URL", "http://127.0.0.1:8000/ask")
ALL_SOURCES = ["wiki", "arxiv", "web"]
HISTORY_PATH = Path.home() / ".cache" / "research-assistant" / "streamlit_history.json"
HISTORY_LIMIT = 30
REQUEST_TIMEOUT_SECONDS = 120

st.set_page_config(page_title="Research Assistant", page_icon="🔮", layout="centered")

st.markdown(
    """
    <style>
    .stApp {
        background: radial-gradient(ellipse 1200px 800px at 50% -10%,
            #2c1f52 0%, #140f2c 45%, #08060f 100%);
    }
    h1 {
        background: linear-gradient(135deg, #aa3bff, #22d3ee);
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
        letter-spacing: -0.5px;
    }
    [data-testid="stSidebar"] {
        border-right: 1px solid rgba(170, 59, 255, 0.2);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_history() -> list[dict]:
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_history(history: list[dict]) -> None:
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(json.dumps(history), encoding="utf-8")
    except OSError:
        pass  # History just won't persist; not worth failing the request over.


def safe_link_markdown(label: str, url: str) -> str:
    """Only render as a clickable link when the scheme is http(s).

    Source URLs come from external providers (Wikipedia/arXiv/web search);
    rendering an untrusted scheme (e.g. javascript:) as a markdown link
    would be an XSS vector, so untrusted schemes fall back to plain text.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme in ("http", "https"):
        return f"[{label}]({url})"
    return f"{label} ({url})"


if "history" not in st.session_state:
    st.session_state.history = load_history()
if "active_id" not in st.session_state:
    st.session_state.active_id = None
if "question_input" not in st.session_state:
    st.session_state.question_input = ""
if "sources_input" not in st.session_state:
    st.session_state.sources_input = list(ALL_SOURCES)
if "result" not in st.session_state:
    st.session_state.result = None
if "error" not in st.session_state:
    st.session_state.error = None


def start_new_chat() -> None:
    st.session_state.question_input = ""
    st.session_state.sources_input = list(ALL_SOURCES)
    st.session_state.result = None
    st.session_state.error = None
    st.session_state.active_id = None


def load_history_item(item: dict) -> None:
    st.session_state.active_id = item["id"]
    st.session_state.question_input = item["question"]
    st.session_state.sources_input = item["sources"]
    st.session_state.result = item["result"]
    st.session_state.error = None


def delete_history_item(item_id: str) -> None:
    st.session_state.history = [h for h in st.session_state.history if h["id"] != item_id]
    save_history(st.session_state.history)
    if item_id == st.session_state.active_id:
        start_new_chat()


with st.sidebar:
    st.markdown("### History")
    if st.button("+ New question", use_container_width=True):
        start_new_chat()
        st.rerun()

    if not st.session_state.history:
        st.caption("Your past questions will appear here.")
    else:
        for item in st.session_state.history:
            row = st.container()
            col_select, col_delete = row.columns([5, 1])
            is_active = item["id"] == st.session_state.active_id
            if col_select.button(
                item["question"],
                key=f"hist-select-{item['id']}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                load_history_item(item)
                st.rerun()
            if col_delete.button("✕", key=f"hist-delete-{item['id']}"):
                delete_history_item(item["id"])
                st.rerun()

st.title("Research Assistant")

with st.container(border=True):
    with st.form("ask-form"):
        question = st.text_input(
            "Question",
            key="question_input",
            placeholder="Ask a research question...",
            label_visibility="collapsed",
        )
        sources = st.pills(
            "Sources",
            ALL_SOURCES,
            selection_mode="multi",
            key="sources_input",
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("Ask", type="primary")

if submitted:
    st.session_state.result = None
    st.session_state.error = None

    if not question.strip():
        st.session_state.error = "Please enter a question."
    elif not sources:
        st.session_state.error = "Select at least one source."
    else:
        with st.spinner("Researching..."):
            try:
                response = requests.post(
                    API_URL,
                    json={"question": question, "sources": sources},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code >= 400:
                    try:
                        detail = response.json().get("detail")
                    except ValueError:
                        detail = None
                    st.session_state.error = detail or f"Request failed with status {response.status_code}"
                else:
                    data = response.json()
                    st.session_state.result = data
                    entry = {
                        "id": str(uuid.uuid4()),
                        "question": data["question"],
                        "sources": sources,
                        "result": data,
                    }
                    st.session_state.history = [entry, *st.session_state.history][:HISTORY_LIMIT]
                    st.session_state.active_id = entry["id"]
                    save_history(st.session_state.history)
            except requests.RequestException as exc:
                st.session_state.error = (
                    f"Could not reach the research API at {API_URL}. "
                    f"Is the FastAPI server running? ({exc})"
                )

    # The sidebar (rendered earlier in this same top-to-bottom run) already
    # drew the old history before this block ran. Force one more run so it
    # picks up the update; session_state already holds the result to show.
    st.rerun()

if st.session_state.error:
    st.error(st.session_state.error)

if st.session_state.result:
    result = st.session_state.result
    with st.container(border=True):
        st.subheader(f"Q: {result['question']}")

        if result["answer"]:
            st.markdown(result["answer"])
        else:
            st.caption("No answer could be produced.")

        if result["citations"]:
            st.markdown("**References**")
            for citation in result["citations"]:
                # Plain st.markdown (unsafe_allow_html left False) escapes any HTML
                # in title/origin, since both come from external, untrusted sources.
                with st.container(border=True):
                    st.markdown(f"**[{citation['index']}]** ({citation['origin']}) {citation['title']}")
                    st.markdown(safe_link_markdown(citation["url"], citation["url"]))

        if result["warnings"]:
            st.markdown("**Warnings**")
            for warning in result["warnings"]:
                st.warning(warning)
