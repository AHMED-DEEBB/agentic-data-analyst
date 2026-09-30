"""The multi-agent workflow.

Tool: LangGraph
  What: a framework for building agents as a graph: nodes are steps (agents or tools),
        edges decide what runs next, and a shared typed state flows through them.
  Why:  production agents need control, not a free-running loop. LangGraph gives us
        explicit routing (refuse vs. answer), bounded self-correction (max N retries),
        and streaming of every step to the UI.

Flow:
    planner ──► retrieve ──► write_sql ──► validate ──► execute ──► insight ──► END
       │                        ▲             │            │
       │                        └── repair ◄──┴────────────┘  (max_sql_attempts)
       └──► refuse ──► END                     └──► fail ──► END
"""

import json
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app import retrieval
from app.agents import prompts
from app.agents.schemas import AnalystState, InsightOutput, PlannerOutput, SQLDraft
from app.config import get_settings
from app.db import run_readonly
from app.llm import invoke_structured
from app.safety import UnsafeSQLError, validate_sql


def _step(name: str, detail: str, started: float) -> dict:
    return {"step": name, "detail": detail, "ms": int((time.perf_counter() - started) * 1000)}


# ---------- Agents (nodes) ----------

def planner(state: AnalystState) -> dict:
    """Supervisor: classifies intent, detects language, rewrites the question."""
    t = time.perf_counter()
    out: PlannerOutput = invoke_structured(
        PlannerOutput, [SystemMessage(prompts.PLANNER), HumanMessage(state["question"])]
    )
    return {
        "intent": out.intent,
        "language": out.language,
        "rewritten_question": out.rewritten_question,
        "attempts": 0,
        "steps": [_step("planner", f"intent={out.intent}, language={out.language}", t)],
    }


def retrieve(state: AnalystState) -> dict:
    """Schema agent: hybrid RAG over table docs, business rules and examples."""
    t = time.perf_counter()
    context, sources = retrieval.build_context(state["question"], state["rewritten_question"])
    return {"context": context, "sources": sources,
            "steps": [_step("retrieve", ", ".join(sources), t)]}


def write_sql(state: AnalystState) -> dict:
    """SQL agent: drafts a query, or repairs the previous one using the error message."""
    t = time.perf_counter()
    messages = [
        SystemMessage(prompts.SQL_WRITER.format(context=state["context"])),
        HumanMessage(state["rewritten_question"]),
    ]
    if state.get("sql_error"):
        messages.append(HumanMessage(prompts.SQL_REPAIR.format(sql=state["sql"], error=state["sql_error"])))
    draft: SQLDraft = invoke_structured(SQLDraft, messages)
    label = "repair_sql" if state.get("sql_error") else "write_sql"
    return {"sql": draft.sql, "sql_error": None, "steps": [_step(label, draft.reasoning, t)]}


def validate(state: AnalystState) -> dict:
    """Validator: parses the SQL and blocks anything that is not a safe SELECT."""
    t = time.perf_counter()
    try:
        safe_sql = validate_sql(state["sql"], get_settings().max_rows)
        return {"sql": safe_sql, "steps": [_step("validate", "passed", t)]}
    except UnsafeSQLError as e:
        return {"sql_error": str(e), "attempts": state["attempts"] + 1,
                "steps": [_step("validate", f"rejected: {e}", t)]}


def execute(state: AnalystState) -> dict:
    """Tool: runs the query as a read-only role inside a read-only transaction."""
    t = time.perf_counter()
    try:
        columns, rows = run_readonly(state["sql"])
        return {"columns": columns, "rows": rows, "steps": [_step("execute", f"{len(rows)} rows", t)]}
    except Exception as e:  # database errors are fed back to the SQL agent
        error = str(e).splitlines()[0]
        return {"sql_error": f"Database error: {error}", "attempts": state["attempts"] + 1,
                "steps": [_step("execute", f"error: {error}", t)]}


def insight(state: AnalystState) -> dict:
    """Insight agent: writes the answer in the user's language and picks a chart."""
    t = time.perf_counter()
    language_name = "Arabic" if state["language"] == "ar" else "English"
    result = json.dumps({"columns": state["columns"], "rows": state["rows"][:50]}, ensure_ascii=False, default=str)
    out: InsightOutput = invoke_structured(InsightOutput, [
        SystemMessage(prompts.INSIGHT.format(language_name=language_name)),
        HumanMessage(f"Question: {state['question']}\n\nQuery result:\n{result}"),
    ])
    chart = out.chart.model_dump()
    # Guardrail: only keep the chart if it references real columns and there is >1 row.
    if (chart["type"] == "none" or len(state["rows"]) < 2
            or chart["x"] not in state["columns"] or chart["y"] not in state["columns"]):
        chart = None
    return {"answer": out.answer, "chart": chart, "status": "ok", "steps": [_step("insight", "answer ready", t)]}


def refuse(state: AnalystState) -> dict:
    lang = state.get("language", "en")
    return {"answer": prompts.REFUSALS[state["intent"]][lang], "status": "refused",
            "steps": [{"step": "refuse", "detail": state["intent"], "ms": 0}]}


def fail(state: AnalystState) -> dict:
    lang = state.get("language", "en")
    return {"answer": prompts.REFUSALS["failed"][lang], "status": "failed",
            "steps": [{"step": "fail", "detail": state.get("sql_error") or "", "ms": 0}]}


# ---------- Routing ----------

def route_intent(state: AnalystState) -> str:
    return "retrieve" if state["intent"] == "data_question" else "refuse"


def route_after_check(next_node: str):
    def router(state: AnalystState) -> str:
        if not state.get("sql_error"):
            return next_node
        return "write_sql" if state["attempts"] < get_settings().max_sql_attempts else "fail"
    return router


def build_graph():
    g = StateGraph(AnalystState)
    for name, fn in [("planner", planner), ("retrieve", retrieve), ("write_sql", write_sql),
                     ("validate", validate), ("execute", execute), ("insight", insight),
                     ("refuse", refuse), ("fail", fail)]:
        g.add_node(name, fn)

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", route_intent, ["retrieve", "refuse"])
    g.add_edge("retrieve", "write_sql")
    g.add_edge("write_sql", "validate")
    g.add_conditional_edges("validate", route_after_check("execute"), ["execute", "write_sql", "fail"])
    g.add_conditional_edges("execute", route_after_check("insight"), ["insight", "write_sql", "fail"])
    g.add_edge("insight", END)
    g.add_edge("refuse", END)
    g.add_edge("fail", END)
    return g.compile()


graph = build_graph()