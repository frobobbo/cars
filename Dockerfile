FROM ghcr.io/astral-sh/uv:0.11.6-python3.13-bookworm-slim
WORKDIR /app
ENV UV_NO_CACHE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CARS_DATA_DIR=/data
COPY pyproject.toml uv.lock README.md ./
COPY cars_app ./cars_app
COPY templates ./templates
COPY static ./static
RUN uv sync --frozen --no-dev --no-editable
USER 10001:10001
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "cars_app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
