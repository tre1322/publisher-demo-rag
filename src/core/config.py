"""Configuration settings for the Publisher RAG Demo."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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
GRADIENT_MODEL_ACCESS_KEY = os.getenv("GRADIENT_MODEL_ACCESS_KEY", "")

# LLM Provider: "anthropic" (Claude) or "gradient" (Qwen/Llama via DigitalOcean)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")

# LLM Settings
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

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

# Server Settings
BASE_URL = os.getenv("BASE_URL", "http://localhost:7860")
