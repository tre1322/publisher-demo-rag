# Amplafai production image — FastAPI web app + LiveKit voice worker.
# One image, two start commands:
#   web    (default): uv run python src/chatbot.py            (binds $PORT)
#   worker (override): uv run python -m src.modules.pmc.voice_agent start
#
# No RAG/ingestion/vision stack, no init.sh, no ChromaDB, no quadd DB.
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv + minimal system libs (ca-certificates for outbound TLS to Stripe /
# Anthropic / LiveKit / Deepgram / Cartesia).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
ENV PATH="/root/.local/bin:$PATH"

# Dependency layer (cache-friendly): resolve from the committed lockfile,
# excluding the dev group (pyright/pytest/ruff).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code. scripts/ is included so the W1/W2/voice smoke +
# verify scripts can be run inside the container for verification.
COPY src/ src/
COPY scripts/ scripts/
COPY README.md ./

# SQLite + recording staging live here. On Railway a persistent volume is
# mounted at /app/data so orgs/invites/billing/PMC survive deploys.
RUN mkdir -p data

# Documentation only — Railway injects $PORT and the app reads it
# (src/chatbot.py defaults to 8080 when $PORT is unset).
EXPOSE 8080

CMD ["uv", "run", "--no-dev", "python", "src/chatbot.py"]
