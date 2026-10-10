# Pitch Ledger backend: one image for the API, the live service and the Match Lab feed
# (Container Apps app and jobs run it with different commands).
#
# The six demo matches in scenarios/matches.yaml are generated at build time (the
# generator is deterministic, so they match the seeds in the repo); the API serves them
# from /app/data and the feed job streams them.
#
#   docker build -t pitch-ledger .
#   docker run -p 8000:8000 -e PITCH_LEDGER_API_AUTH=dev pitch-ledger

FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY scenarios ./scenarios
RUN uv sync --locked --no-dev --no-editable \
    && .venv/bin/pitch-ledger generate-all --out data \
    && rm -f data/*/*.parquet

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv ./.venv
COPY --from=build --chown=app:app /app/scenarios ./scenarios
COPY --from=build --chown=app:app /app/data ./data
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PITCH_LEDGER_DATA_DIR=/app/data
USER app
EXPOSE 8000
CMD ["pitch-ledger", "api", "--host", "0.0.0.0", "--port", "8000"]
