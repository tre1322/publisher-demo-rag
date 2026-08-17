# popular-network-demo production image
# Mirrors the amplafai stack pattern: uv + python-slim, source baked in,
# SQLite + ingestion DB live on a mounted volume at /app/data so they
# survive `docker compose up -d --build` redeploys.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    HOST=0.0.0.0 \
    PORT=8080

# Install uv (same runtime as amplafai-web)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Layer 1: deps (re-builds only when lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Layer 2: source. HTML roots + static + app package.
COPY app/ ./app/
COPY static/ ./static/
COPY dashboard.html login.html invite.html widget-test.html ./
# Voice briefs are load-bearing: bootstrap.voiceBrief + the agent system prompt
# both read <repo-root>/voice-briefs/<slug>.json and FAIL SILENTLY to None/""
# when absent. Omitting this line shipped a voiceless agent to prod for weeks.
COPY voice-briefs/ ./voice-briefs/

# Ensure the SQLite volume mount-point exists on first boot before
# the named volume is populated.
RUN mkdir -p /app/data

EXPOSE 8080

# uvicorn binds 0.0.0.0:8080 inside the container; Caddy reverse-proxies
# from dashboard.amplafai.com → dashboard:8080 over the Docker network.
CMD ["uv", "run", "--no-dev", "python", "-m", "app.main"]
