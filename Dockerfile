FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    FASTEMBED_CACHE_PATH=/opt/fastembed

# Dependencies first, so a code change doesn't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Bake the embedding model into the image: the container starts offline and never re-downloads.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=4)"

# Seeding is idempotent: the first start creates tables, demo data and the index for $VERTICAL;
# later starts only re-ingest documents that changed.
CMD ["sh", "-c", "python -m app.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
