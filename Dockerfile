FROM ghcr.io/astral-sh/uv:0.12.19 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY --chown=10001:10001 src ./src
RUN uv sync --locked --no-dev && useradd --uid 10001 --create-home bot
ENV PATH="/app/.venv/bin:$PATH"
USER bot
CMD ["iett-bot", "run"]
