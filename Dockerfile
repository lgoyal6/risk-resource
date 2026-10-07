FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN pip install uv && uv sync --frozen --no-dev
COPY src migrations ./
CMD [".venv/bin/uvicorn", "risk_resource.transport.api:app", "--host", "0.0.0.0", "--port", "8000"]
