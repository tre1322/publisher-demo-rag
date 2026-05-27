"""Article ingestion modules — CMS-agnostic three-tier stack.

Tier 1: RSS + full-text fetch (automated, zero ongoing publisher work)
Tier 2: URL import (per-article or bulk, semi-automatic)
Tier 3: Paste form (universal fallback for print-only content)
"""

from src.modules.ingestion.rss_ingestor import RSSIngestor
from src.modules.ingestion.url_ingestor import URLIngestor

__all__ = ["RSSIngestor", "URLIngestor"]
