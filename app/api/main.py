"""HTTP API.

Tool: FastAPI (+ Uvicorn server)
  What: a Python web framework for building typed APIs. Uvicorn is the server that runs it.
  Why:  request/response validation from type hints, automatic OpenAPI docs at /docs,
        and streaming responses so the UI shows each agent step live.

Endpoints:
  GET  /health       liveness + database check (used by Docker healthcheck)
  POST /ask          full answer as JSON
  POST /ask/stream   Server-Sent Events: one event per agent step, then the result
  GET  /             demo UI
"""

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agents import runner
from app.db import ping

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("analyst.api")

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="Agentic Data Analyst",
    version="1.0.0",
    description="Ask business questions in Arabic or English. A LangGraph multi-agent team answers with SQL, a chart and an insight.",
)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500, examples=["Revenue by country in 2025"])


@app.get("/health")
def health():
    ok = ping()
    if not ok:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ok"}


@app.post("/ask")
def ask(req: AskRequest):
    try:
        result = runner.ask(req.question)
    except Exception as e:
        log.exception("ask failed")
        raise HTTPException(status_code=502, detail=f"Agent error: {type(e).__name__}") from e
    log.info("ask status=%s attempts=%s latency_ms=%s", result["status"], result["attempts"], result["latency_ms"])
    return result


@app.post("/ask/stream")
def ask_stream(req: AskRequest):
    def events():
        try:
            for event in runner.stream(req.question):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:
            log.exception("stream failed")
            yield f"data: {json.dumps({'type': 'error', 'detail': type(e).__name__})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
