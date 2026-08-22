FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca
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
