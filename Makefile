.PHONY: up down logs test lint evals evals-quick mcp

up:            ## start database + API (http://localhost:8000)
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f api
test:          ## unit tests (no LLM needed)
	pytest -q
lint:
	ruff check .
evals:         ## full evaluation suite
	docker compose exec api python -m evals.run_evals --delay 1
evals-quick:
	docker compose exec api python -m evals.run_evals --limit 15 --delay 1
mcp:           ## run the MCP server locally (stdio)
	python -m app.mcp_server
