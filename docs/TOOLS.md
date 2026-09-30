# Tools and design decisions

For every tool: **what it is**, **what it does in this project**, and **why we use it** (including what we
chose it over). The last section lists the key design decisions.

---

## Agent layer

### LangGraph
- **What:** a framework for building agents as a graph. Nodes are steps (an agent or a tool), edges decide
  what runs next, and a typed state object flows through the graph.
- **Here:** `app/agents/graph.py` defines 8 nodes: planner, retrieve, write_sql, validate, execute, insight,
  refuse, fail. Conditional edges route unsafe questions to `refuse` and failed SQL back to `write_sql`.
- **Why:** production agents need predictable control flow. A free "ReAct loop" can wander, loop forever or
  skip safety checks. With LangGraph the validator can never be skipped, retries are bounded
  (`MAX_SQL_ATTEMPTS`), and every step can be streamed to the UI. Chosen over CrewAI/AutoGen because it gives
  explicit, testable control rather than autonomous conversation between agents.

### LangChain (core + provider packages)
- **What:** a common interface over LLM providers, plus utilities like prompt messages and structured output.
- **Here:** `app/llm.py` creates the model; each agent calls `with_structured_output(PydanticModel)`.
- **Why:** swap Groq, OpenAI or Azure OpenAI with one setting. Structured output means each agent returns a
  typed object (intent, SQL, chart spec) instead of text we would have to parse with regex.

### Pydantic
- **What:** data validation using Python type hints.
- **Here:** `app/agents/schemas.py` defines each agent's output and the shared graph state.
- **Why:** the LLM output is validated at the boundary. A malformed answer fails immediately with a clear
  error instead of breaking a later step silently. `Literal` types restrict values (e.g. intent can only be
  one of three options).

### LLM providers: Groq, OpenAI, Azure OpenAI
- **What:** hosted LLM APIs. Groq runs open models (default `openai/gpt-oss-120b`) very fast with a free tier.
- **Why:** Groq keeps the project free to run. Azure OpenAI support matters because most GCC enterprises
  deploy LLMs through Azure for data residency and compliance.

---

## Retrieval (RAG)

### RAG (Retrieval-Augmented Generation)
- **What:** before the model answers, retrieve relevant knowledge and put it in the prompt.
- **Here:** the schema agent retrieves table descriptions, **business definitions** and **verified example
  queries** from `app/knowledge/knowledge.yaml`.
- **Why:** the biggest text-to-SQL failure is not syntax but meaning: "revenue" must exclude cancelled
  orders and apply discounts. Grounding the model in a business glossary makes it follow company definitions.
  Retrieval also scales: a real warehouse has hundreds of tables that cannot all fit in a prompt.

### fastembed
- **What:** runs embedding models locally on CPU (ONNX). An embedding converts text into a vector so that
  texts with similar meaning are close together.
- **Here:** model `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions), baked into the Docker image.
- **Why:** free, no API key, no network call per query. The multilingual model places Arabic and English in
  the same vector space, so "الإيرادات" retrieves the "revenue" definition.

### PostgreSQL + pgvector
- **What:** pgvector is a Postgres extension that stores vectors and finds the nearest ones.
- **Here:** `kb.chunks` table holds each knowledge chunk, its embedding and a full-text index.
- **Why:** one database for business data and vectors, so there is no separate vector DB (Pinecone, Qdrant)
  to deploy, pay for or keep in sync.

### Hybrid search + Reciprocal Rank Fusion (RRF)
- **What:** run semantic (vector) search and keyword (Postgres full-text) search, then merge the rankings with
  `score = Σ 1 / (60 + rank)`.
- **Why:** semantic search understands meaning ("turnover" ≈ "revenue"); keyword search catches exact terms
  (table names, "AOV"). RRF combines them without calibrating two different score scales.

---

## Safety

### sqlglot
- **What:** a SQL parser that turns SQL text into a syntax tree.
- **Here:** `app/safety.py` walks the tree and rejects anything that is not exactly one SELECT on allowed
  tables, blocks dangerous functions (`pg_sleep`, `pg_read_file`, `set_config`), `SELECT INTO`, row locks and
  data-modifying CTEs, and adds a row limit.
- **Why:** regex checks are easy to bypass (comments, casing, a `DELETE` hidden inside a CTE). Parsing checks
  the actual structure. Rejections are sent back to the SQL agent as feedback so it can repair the query.

### Postgres read-only role and transaction
- **What:** database-level permissions.
- **Why:** defence in depth. Even if a validator bug let a write through, the database itself refuses it.
  `tests/test_safety.py` proves this by running `DELETE` directly against the read-only connection.

---

## Serving

### FastAPI + Uvicorn
- **What:** FastAPI is a Python web framework built on type hints; Uvicorn is the server that runs it.
- **Here:** `/ask` (JSON), `/ask/stream` (Server-Sent Events), `/health`, and the UI at `/`.
- **Why:** automatic request validation and OpenAPI docs (`/docs`), and streaming so users watch each agent
  step instead of staring at a spinner.

### Server-Sent Events (SSE)
- **What:** a simple HTTP standard for the server to push a stream of events to the browser.
- **Why:** simpler than WebSockets for one-way progress updates, and works through most proxies.

### MCP (Model Context Protocol)
- **What:** an open standard for connecting AI assistants to tools. A server exposes typed tools; any MCP
  client (Claude Desktop, Cursor, IDE agents) can call them.
- **Here:** `app/mcp_server.py` exposes `ask_analyst`, `list_tables`, `describe_table`, `run_sql`.
- **Why:** build the integration once, use it from every AI client. The same guardrails protect MCP calls.

---

## Quality and operations

### Evaluation suite (execution accuracy)
- **What:** 51 test cases. For data questions, the agent's result is compared with the result of a hand-written
  gold query. Unsafe and off-topic prompts must be refused.
- **Why:** comparing results, not SQL text, is the standard metric (used by Spider and BIRD benchmarks), since
  two different queries can both be correct. Without evals, every prompt change is a guess.

### pytest
- **What:** Python's standard test framework.
- **Here:** guardrail tests (20 attacks that must be blocked) and scoring tests. No LLM needed, so they run on
  every push in seconds.

### GitHub Actions
- **What:** CI/CD that runs jobs automatically on every push or pull request.
- **Here:** job 1 runs lint + tests against a real Postgres container. Job 2 runs the eval subset if a
  `GROQ_API_KEY` secret is set and **fails the build if accuracy falls below the threshold**.
- **Why:** a quality gate for agent behaviour, the same way unit tests gate normal code.

### Langfuse (optional)
- **What:** open-source LLM observability: traces of every step, prompt, output, latency and token count.
- **Why:** in production you must be able to answer "why did the agent say that?". Enabled by setting
  `LANGFUSE_*` keys; no code changes.

### Docker + Docker Compose
- **What:** Docker packages the app with all dependencies into an image; Compose runs the API and database together.
- **Why:** one command (`docker compose up`) runs the full system identically on any machine or cloud.
  The image runs as a non-root user, has a health check, and includes the embedding model.

### Ruff
- **What:** a fast Python linter.
- **Why:** consistent code style and catches common bugs before review.

---

## Design decisions

1. **Workflow graph, not a free agent loop.** Guarantees the validator always runs and retries are bounded.
2. **Self-correction with feedback.** Validator and database errors are returned to the SQL agent verbatim,
   which fixes most first-attempt mistakes (wrong column, missing join).
3. **Business glossary in RAG.** Accuracy problems in text-to-SQL come mostly from ambiguous definitions,
   not SQL syntax.
4. **Verified examples as few-shot.** Similar past questions with correct SQL are retrieved per query.
5. **Four safety layers.** Planner refusal, AST validation, read-only role, read-only transaction + timeout.
6. **Language handled at the edges.** The planner rewrites the question in English for SQL generation; the
   insight agent answers in the user's language. SQL quality stays consistent across languages.
7. **Structured outputs everywhere.** Typed contracts between agents instead of parsing free text.
8. **Deterministic data.** Fixed seed and date range make evaluation reproducible in CI.
9. **Provider-agnostic.** Groq, OpenAI or Azure OpenAI by configuration.
10. **Chart guardrail.** The chart is dropped if the model references a column that doesn't exist.