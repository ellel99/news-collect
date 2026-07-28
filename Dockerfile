FROM ghcr.io/astral-sh/uv:0.8.3 AS uv
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "market_intelligence.main:app", "--host", "0.0.0.0", "--port", "8000"]
