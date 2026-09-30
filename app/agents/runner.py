"""Entry points used by the API, the MCP server and the eval suite."""

import time
from collections.abc import Iterator

from app.agents.graph import graph
from app.llm import get_callbacks

PUBLIC_FIELDS = ("status", "language", "answer", "sql", "columns", "rows", "chart", "sources", "steps")


def _config() -> dict:
    return {"callbacks": get_callbacks(), "recursion_limit": 25}


def _public(state: dict, started: float) -> dict:
    result = {k: state.get(k) for k in PUBLIC_FIELDS}
    if result["status"] != "ok":  # don't expose SQL/rows for refused or failed requests
        result.update(sql=None, columns=[], rows=[], chart=None)
    result["attempts"] = state.get("attempts", 0)
    result["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def ask(question: str) -> dict:
    started = time.perf_counter()
    state = graph.invoke({"question": question, "steps": []}, config=_config())
    return _public(state, started)


def stream(question: str) -> Iterator[dict]:
    """Yields one event per completed step, then a final result event."""
    started = time.perf_counter()
    state: dict = {"question": question, "steps": []}
    for update in graph.stream(state, config=_config(), stream_mode="updates"):
        for _node, delta in update.items():
            for key, value in (delta or {}).items():
                state[key] = state.get(key, []) + value if key == "steps" else value
            for step in (delta or {}).get("steps", []):
                yield {"type": "step", **step}
    yield {"type": "result", **_public(state, started)}
