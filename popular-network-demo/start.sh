#!/usr/bin/env bash
# Popular Network — Marketing Dashboard
# Boots FastAPI + serves dashboard.html at http://localhost:8765
set -euo pipefail
cd "$(dirname "$0")"
uv run python -m app.main
