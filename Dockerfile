FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY rules ./rules
COPY web ./web
RUN uv sync --frozen --no-dev
ENV TODOKU_RULES_DIR=/app/rules TODOKU_WEB_DIR=/app/web
EXPOSE 8080
# 鍵（ANTHROPIC_API_KEY）と合言葉（TODOKU_PASSPHRASE）は fly secrets で渡す。イメージには入れない
CMD ["/app/.venv/bin/uvicorn", "todoku.app:app", "--host", "0.0.0.0", "--port", "8080"]
