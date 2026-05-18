"""Configuration settings for the Publisher RAG Demo."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# override=True: in dev, .env is the source of truth. Without override, an
# empty ANTHROPIC_API_KEY (or similar) leaked from the shell would silently
# win over the file value and the LLM call would fail with an opaque
# "Could not resolve authentication method" deep in the Anthropic client.
# In prod (Railway), .env doesn't exist, so override does nothing.
load_dotenv(override=True)

# Logging configuration with timestamps
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
)

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DOCUMENTS_DIR = DATA_DIR / "documents"
# Resolve to absolute path so reindex scripts and runtime always agree
CHROMA_PERSIST_DIR = Path(os.getenv("CHROMA_PERSIST_DIR", str(DATA_DIR / "chroma_db"))).resolve()
INGESTED_FILES_PATH = DATA_DIR / "ingested_files.json"

# Ensure directories exist
DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)

# Startup logging for deploy debugging
_config_logger = logging.getLogger(__name__)
_config_logger.info(f"DATA_DIR: {DATA_DIR}")
_config_logger.info(f"CHROMA_PERSIST_DIR: {CHROMA_PERSIST_DIR}")

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GRADIENT_MODEL_ACCESS_KEY = os.getenv("GRADIENT_MODEL_ACCESS_KEY", "")

# LLM Provider: "anthropic" (Claude) or "gradient" (Qwen/Llama via DigitalOcean)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")

# LLM Settings
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.0"))  # v2 Phase 1d: greedy decoding for factual grounding

# Gradient/DigitalOcean Serverless Inference Settings
GRADIENT_BASE_URL = os.getenv("GRADIENT_BASE_URL", "https://inference.do-ai.run/v1")
GRADIENT_MODEL = os.getenv("GRADIENT_MODEL", "qwen3-32b")

# Embedding Settings
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# Chunking Settings
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1024"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))

# Retrieval Settings
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "5"))
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.3"))
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "8000"))

# ChromaDB Settings
COLLECTION_NAME = "publisher_main"  # Legacy — kept for backward-compat reads
ARTICLES_COLLECTION = "articles"
ADS_COLLECTION = "advertisements"

# Query Transformation Settings
ENABLE_QUERY_TRANSFORMATION = (
    os.getenv("ENABLE_QUERY_TRANSFORMATION", "true").lower() == "true"
)
MAX_TRANSFORMED_QUERIES = int(os.getenv("MAX_TRANSFORMED_QUERIES", "3"))

# Vision Pipeline Settings
# Provider: "openai" (GPT-5.4, recommended) or "anthropic" (Claude Sonnet)
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "openai")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-5.4")
VISION_COST_PER_PAGE = float(os.getenv("VISION_COST_PER_PAGE", "0.04"))
VISION_DPI = int(os.getenv("VISION_DPI", "200"))
VISION_PAGE_DELAY = float(os.getenv("VISION_PAGE_DELAY", "1.0"))

# Business Directory Enrichment
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

# Server Settings
BASE_URL = os.getenv("BASE_URL", "http://localhost:7860")

# ── W2.2 voice interview ──────────────────────────────────────────────
# LiveKit Cloud — owner browser connects here for WebRTC. Agent worker
# registers with this URL too. Get from the LiveKit Cloud dashboard.
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

# Voice provider keys. STT via Deepgram Nova-3, TTS via Cartesia Sonic-2.
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
# Cartesia voice id. Pick during Day 2 testing; this is the warm-personal
# baseline. Swap by setting CARTESIA_VOICE_ID in .env.
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")

# Interview pacing. The agent's pacing rule fires at >=0.75 of this.
PMC_INTERVIEW_TARGET_SECONDS = int(os.getenv("PMC_INTERVIEW_TARGET_SECONDS", "1800"))

# Pause cap: if the owner pauses the interview and doesn't resume within
# this many seconds, the agent auto-ends the call. Prevents a forgotten
# pause from holding the LiveKit room (and the agent worker slot) open.
# Owner sees "interview interrupted — you can restart anytime" on the
# /business/pmc/ page after auto-end.
PMC_INTERVIEW_PAUSE_CAP_SECONDS = int(
    os.getenv("PMC_INTERVIEW_PAUSE_CAP_SECONDS", "600")
)

# Agent dispatch name. The /voice/start route dispatches by this name; the
# worker process registers with the same name. Keep aligned.
PMC_AGENT_NAME = os.getenv("PMC_AGENT_NAME", "amplafai-pmc-interviewer")

# Where the agent worker POSTs the transcript when the call ends. In dev,
# the worker runs on the same machine as the FastAPI app so localhost works.
# In production (Railway), set this to the internal/private URL of the app
# service so the callback doesn't egress to the public internet.
PMC_VOICE_CALLBACK_BASE_URL = os.getenv(
    "PMC_VOICE_CALLBACK_BASE_URL", "http://localhost:8080"
)

# Recording — LiveKit Egress writes to DigitalOcean Spaces (S3-compatible).
# 30-day lifecycle rule should be set on the bucket.
PMC_VOICE_RECORDING_ENABLED = (
    os.getenv("PMC_VOICE_RECORDING_ENABLED", "true").lower() == "true"
)
SPACES_ENDPOINT = os.getenv("SPACES_ENDPOINT", "")  # e.g. https://nyc3.digitaloceanspaces.com
SPACES_ACCESS_KEY = os.getenv("SPACES_ACCESS_KEY", "")
SPACES_SECRET_KEY = os.getenv("SPACES_SECRET_KEY", "")
SPACES_BUCKET = os.getenv("SPACES_BUCKET", "amplora-pmc-recordings")
SPACES_REGION = os.getenv("SPACES_REGION", "us-east-1")
