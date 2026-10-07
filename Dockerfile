FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
CMD ["sh", "-c", ".venv/bin/python scripts/container_start.py && .venv/bin/uvicorn risk_resource.transport.api:app --host 0.0.0.0 --port 8000"]
