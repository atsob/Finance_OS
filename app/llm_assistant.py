import streamlit as st
import psycopg2
import requests
import json
from duckduckgo_search import DDGS

# ─────────────────────────────────────────────
# CONFIG — adjust to your environment
# ─────────────────────────────────────────────
OLLAMA_URL = "http://192.168.4.20:11434"   # Replace with your Pi's IP
OLLAMA_MODEL = "llama3.2:3b"
DB_CONFIG = {
    "host": "192.168.4.20",         # Docker service name or IP
    "port": 5432,
    "database": "Finance",
    "user": "admin",
    "password": "31.12.1969",
}

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def get_db_schema() -> str:
    """Fetch table and column names to give the LLM context."""
    query = """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position;
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
    schema_lines = {}
    for table, column, dtype in rows:
        schema_lines.setdefault(table, []).append(f"  {column} ({dtype})")
    return "\n".join(
        f"Table: {t}\n" + "\n".join(cols)
        for t, cols in schema_lines.items()
    )

def run_sql(sql: str) -> list[dict]:
    """Execute a SELECT query and return results as a list of dicts."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
    return [dict(zip(columns, row)) for row in rows]

# ─────────────────────────────────────────────
# WEB SEARCH
# ─────────────────────────────────────────────
def web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo and return a formatted string of results."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No web results found."
        return "\n\n".join(
            f"[{r['title']}]\n{r['body']}\nSource: {r['href']}"
            for r in results
        )
    except Exception as e:
        return f"Web search failed: {e}"

# ─────────────────────────────────────────────
# OLLAMA
# ─────────────────────────────────────────────
def call_ollama(prompt: str, system: str = "") -> str:
    """Send a prompt to Ollama and return the response text."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    except Exception as e:
        return f"Ollama error: {e}"

# ─────────────────────────────────────────────
# AGENT LOGIC
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """You are a personal finance assistant. 
You help the user understand their finances by querying their database and searching the web when needed.
When you need data from the database, output ONLY a valid SQL SELECT statement wrapped like this:
  <sql>SELECT ...</sql>
When you need to search the web, output ONLY the search query wrapped like this:
  <search>your search query</search>
When you have enough information, answer the user clearly and concisely.
Never invent data. Never write INSERT, UPDATE, or DELETE statements."""

def run_agent(user_question: str, schema: str) -> str:
    """
    Simple ReAct-style agent loop:
    1. Ask LLM what to do
    2. If it wants SQL → run it, feed results back
    3. If it wants web search → run it, feed results back
    4. Repeat until LLM gives a plain answer (max 4 iterations)
    """
    context = f"Database schema:\n{schema}\n\nUser question: {user_question}"
    conversation = context

    for _ in range(4):
        reply = call_ollama(conversation, system=SYSTEM_PROMPT)

        # LLM wants to run SQL
        if "<sql>" in reply and "</sql>" in reply:
            sql = reply.split("<sql>")[1].split("</sql>")[0].strip()
            st.info(f"🗄️ Running query:\n```sql\n{sql}\n```")
            try:
                results = run_sql(sql)
                db_context = f"SQL result ({len(results)} rows): {json.dumps(results, default=str)}"
            except Exception as e:
                db_context = f"SQL error: {e}"
            conversation += f"\n\nAssistant wanted SQL: {sql}\n{db_context}\nNow answer the user."

        # LLM wants to search the web
        elif "<search>" in reply and "</search>" in reply:
            query = reply.split("<search>")[1].split("</search>")[0].strip()
            st.info(f"🌐 Searching the web for: *{query}*")
            web_results = web_search(query)
            conversation += f"\n\nAssistant searched for: {query}\nWeb results:\n{web_results}\nNow answer the user."

        # LLM gave a plain answer
        else:
            return reply

    return reply  # Return whatever the last reply was

# ─────────────────────────────────────────────
# STREAMLIT UI
# ─────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Finance Assistant", page_icon="💰")
    st.title("💰 Personal Finance Assistant")
    st.caption("Powered by Llama 3.2 · Fully local · Your data stays on your Pi")

    # Load schema once per session
    if "schema" not in st.session_state:
        with st.spinner("Loading database schema..."):
            st.session_state.schema = get_db_schema()

    # Chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input
    if question := st.chat_input("Ask about your finances..."):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                answer = run_agent(question, st.session_state.schema)
            st.markdown(answer)

        st.session_state.messages.append({"role": "assistant", "content": answer})

if __name__ == "__main__":
    main()
