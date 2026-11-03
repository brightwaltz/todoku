# 元のイメージは版と digest で固定する（3.12.16-slim-trixie、2026-10-28 作成）。安全の更新は自動では入らない。
# 上げ方: `docker buildx imagetools inspect python:3.12-slim` の Digest と org.opencontainers.image.version を見て、下のタグと sha256 を書き換える
FROM python:3.12.16-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY rules ./rules
COPY web ./web
RUN uv sync --frozen --no-dev
ENV TODOKU_RULES_DIR=/app/rules TODOKU_WEB_DIR=/app/web
# root でない利用者で動かす。/app は root のままにして、動いているアプリからは書き換えられないようにする
# （動いている間にファイルは書かない。src の __pycache__ が書けないのは Python が黙って飛ばす）
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
USER 10001:10001
EXPOSE 8080
# 鍵（ANTHROPIC_API_KEY）と合言葉（TODOKU_PASSPHRASE）は fly secrets で渡す。イメージには入れない
CMD ["/app/.venv/bin/uvicorn", "todoku.app:app", "--host", "0.0.0.0", "--port", "8080"]
