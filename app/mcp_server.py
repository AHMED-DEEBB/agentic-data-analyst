"""MCP server: plug the analyst into Claude Desktop, Cursor or any MCP client.

Tool: MCP (Model Context Protocol)
  What: an open standard for connecting AI assistants to tools and data. A server exposes
        "tools" (functions with typed inputs); any MCP-compatible client can call them.
  Why:  build the integration once, use it from every AI client. The same guardrails
        (validator + read-only role) protect every tool here.

Run (stdio transport, which is what desktop clients use):
    python -m app.mcp_server
"""

from mcp.server.mcpserver import MCPServer

from app.agents import runner
from app.config import get_settings
from app.db import run_readonly
from app.retrieval import load_chunks
from app.safety import ALLOWED_TABLES, UnsafeSQLError, validate_sql

mcp = MCPServer(
    name="agentic-data-analyst",
    instructions="Read-only analytics over a GCC retail sales database (2024-2025). "
    "Use ask_analyst for business questions in English or Arabic.",
)


@mcp.tool()
def ask_analyst(question: str) -> dict:
    """Answer a business question (English or Arabic) with SQL, data, a chart spec and an insight."""
    result = runner.ask(question)
    return {k: result[k] for k in ("status", "answer", "sql", "columns", "rows", "chart")}


@mcp.tool()
def list_tables() -> list[str]:
    """List the tables available for analysis."""
    return sorted(ALLOWED_TABLES)


@mcp.tool()
def describe_table(table: str) -> str:
    """Describe a table's purpose and columns."""
    for chunk in load_chunks():
        if chunk["kind"] == "table" and chunk["name"] == table.lower():
            return chunk["content"]
    return f"Unknown table. Available: {', '.join(sorted(ALLOWED_TABLES))}"


@mcp.tool()
def run_sql(sql: str) -> dict:
    """Run one read-only SELECT query. Writes, DDL and unknown tables are rejected."""
    try:
        safe_sql = validate_sql(sql, get_settings().max_rows)
    except UnsafeSQLError as e:
        return {"error": str(e)}
    columns, rows = run_readonly(safe_sql)
    return {"sql": safe_sql, "columns": columns, "rows": rows}


if __name__ == "__main__":
    mcp.run("stdio")
