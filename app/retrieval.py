"""Knowledge base: ingestion and hybrid retrieval (the "R" in RAG).

Tool: fastembed
  What: runs embedding models locally (ONNX, CPU, no GPU). An embedding turns text into a
        vector of numbers so that texts with similar MEANING have nearby vectors.
  Why:  free, fast, no API key. The multilingual model maps Arabic and English into the
        same vector space, so "الإيرادات" lands next to "revenue".

Tool: pgvector
  What: a PostgreSQL extension that stores vectors and finds the nearest ones.
  Why:  vector search inside the database we already run. No separate vector DB to host.

Hybrid search:
  Semantic search catches meaning ("turnover" ~ "revenue"); keyword search (Postgres
  full-text) catches exact terms (table names, "AOV"). We run both and merge the two
  rankings with Reciprocal Rank Fusion (RRF): score = sum(1 / (60 + rank)).
  RRF needs no score calibration between the two methods, which is why it is standard.
"""

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pgvector.psycopg import register_vector

from app.config import get_settings
from app.db import admin_connect

KNOWLEDGE_FILE = Path(__file__).parent / "knowledge" / "knowledge.yaml"


@lru_cache
def _embedder():
    from fastembed import TextEmbedding

    s = get_settings()
    return TextEmbedding(model_name=s.embedding_model, cache_dir=s.embedding_cache_dir)


def embed(texts: list[str]) -> list[list[float]]:
    return [vec.tolist() for vec in _embedder().embed(texts)]


def load_chunks() -> list[dict]:
    kb = yaml.safe_load(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    chunks = []
    for t in kb["tables"]:
        chunks.append({"kind": "table", "name": t["name"], "content": (
            f"Table {t['name']}: {t['description']}\nColumns: {t['columns']}\nAlso called: {t['synonyms']}")})
    for g in kb["glossary"]:
        chunks.append({"kind": "glossary", "name": g["name"], "content": (
            f"{g['name']}: {g['definition']}\nAlso called: {g['synonyms']}")})
    for i, e in enumerate(kb["examples"]):
        chunks.append({"kind": "example", "name": f"example_{i + 1}", "content": (
            f"Question: {e['question']}\nSQL:\n{e['sql'].strip()}")})
    return chunks


def ingest(conn) -> int:
    """(Re)build the knowledge base table. Idempotent."""
    dim = get_settings().embedding_dim
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute("CREATE SCHEMA IF NOT EXISTS kb")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS kb.chunks (
            id SERIAL PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding vector({dim}) NOT NULL,
            tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON kb.chunks USING gin(tsv)")
    register_vector(conn)

    chunks = load_chunks()
    vectors = embed([c["content"] for c in chunks])
    conn.execute("TRUNCATE kb.chunks")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO kb.chunks (kind, name, content, embedding) VALUES (%s, %s, %s, %s)",
            [(c["kind"], c["name"], c["content"], v) for c, v in zip(chunks, vectors, strict=True)],
        )
    conn.commit()
    return len(chunks)


def _keyword_query(text: str) -> str:
    tokens = {t.lower() for t in re.findall(r"\w+", text) if len(t) > 2}
    return " | ".join(sorted(tokens))


HYBRID_SQL = """
WITH sem AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(vec)s) AS rnk
    FROM kb.chunks WHERE kind = %(kind)s
    ORDER BY embedding <=> %(vec)s LIMIT 20
), kw AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank(tsv, to_tsquery('simple', %(kw)s)) DESC) AS rnk
    FROM kb.chunks WHERE kind = %(kind)s AND %(kw)s <> '' AND tsv @@ to_tsquery('simple', %(kw)s)
    LIMIT 20
)
SELECT c.name, c.content, SUM(1.0 / (60 + x.rnk)) AS score
FROM (SELECT * FROM sem UNION ALL SELECT * FROM kw) x
JOIN kb.chunks c ON c.id = x.id
GROUP BY c.id, c.name, c.content
ORDER BY score DESC
LIMIT %(k)s
"""


def search(query: str, kind: str, k: int) -> list[dict]:
    vec = embed([query])[0]
    with admin_connect() as conn:
        register_vector(conn)
        rows = conn.execute(HYBRID_SQL, {
            "vec": _as_vector(vec), "kind": kind, "kw": _keyword_query(query), "k": k,
        }).fetchall()
    return [{"name": r[0], "content": r[1], "score": float(r[2])} for r in rows]


def _as_vector(vec: list[float]):
    import numpy as np

    return np.array(vec, dtype=np.float32)


def build_context(question: str, rewritten: str) -> tuple[str, list[str]]:
    """Retrieve tables, business rules and similar examples for a question."""
    query = f"{rewritten}\n{question}"
    tables = search(query, "table", 4)
    glossary = search(query, "glossary", 4)
    examples = search(query, "example", 3)
    sections = [
        "## Tables\n" + "\n\n".join(c["content"] for c in tables),
        "## Business definitions (follow exactly)\n" + "\n\n".join(c["content"] for c in glossary),
        "## Verified examples\n" + "\n\n".join(c["content"] for c in examples),
    ]
    used = [f"table:{c['name']}" for c in tables] + [f"rule:{c['name']}" for c in glossary]
    return "\n\n".join(sections), used
