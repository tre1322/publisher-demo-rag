# Use Python 3.13 slim image
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Set environment variables to reduce disk usage
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTORCH_ENABLE_MPS_FALLBACK=1

# Install system dependencies, s3cmd, and uv in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl s3cmd && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

ENV PATH="/root/.local/bin:$PATH"

# Copy dependency files first (for better layer caching)
COPY pyproject.toml ./

# Install all dependencies with pip using CPU index for PyTorch
# This avoids triton and CUDA packages that uv sync was pulling in
RUN uv venv && \
    . .venv/bin/activate && \
    pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      --extra-index-url https://pypi.org/simple \
      torch sentence-transformers transformers && \
    pip install --no-cache-dir \
      anthropic bcrypt beautifulsoup4 boto3 chromadb fastapi feedparser gradio \
      httpx itsdangerous llama-index llama-index-embeddings-huggingface \
      llama-index-llms-anthropic openai pandas pdfplumber Pillow pymupdf \
      python-dotenv striprtf uvicorn

# Copy application files
COPY README.md .
COPY src/ src/
COPY scripts/ scripts/
COPY static/ static/
COPY .env.example .env.example

# Copy pre-extracted quadd database to a STAGING location
# NOTE: Railway mounts a persistent volume at data/ which hides baked files.
# We stage it here, then init.sh copies it into data/ at runtime.
COPY data/quadd_articles.db /app/staged/quadd_articles.db
RUN ls -la /app/staged/quadd_articles.db && echo "quadd DB staged OK"

# Create data directories and fix line endings for shell scripts
RUN mkdir -p data/documents data/ads data/events data/editions && \
    touch data/ingested_files.json && \
    sed -i 's/\r$//' scripts/*.sh && \
    chmod +x scripts/init.sh scripts/*.sh && \
    find /root/.local -type f -name "*.pyc" -delete && \
    find /root/.local -type d -name "__pycache__" -delete

# Expose Gradio ports (7860 = chatbot, 7861 = admin dashboard)
EXPOSE 7860 7861

# Set Gradio environment variables
ENV GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860

# Run initialization script (ingests data, then starts chatbot)
CMD ["./scripts/init.sh"]
