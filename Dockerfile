# Tool: Docker. What: packages the app and all its dependencies into one image that runs
# the same everywhere. Why: "works on my machine" is not production; one command runs it anywhere.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    EMBEDDING_CACHE_DIR=/models
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image: no download on startup, faster cold starts.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/models')"

COPY app ./app
COPY evals ./evals

RUN useradd --create-home appuser && chown -R appuser /app /models
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["sh", "-c", "python -m app.bootstrap && uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
