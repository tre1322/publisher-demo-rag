"""Where a business's voice brief comes from (Phase 1).

The brief used to be a JSON file baked into the Docker image
(voice-briefs/{slug}.json), so every new business needed a rebuild. It now
lives on the business row (Business.voice_brief_json). The file stays as a
read-only fallback for rows that predate the column; startup copies it into
the DB once (main._backfill_voice_briefs), after which the DB wins.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .models import Business

# Repo-relative dir of the legacy per-slug brief files.
VOICE_BRIEF_DIR = Path(__file__).resolve().parent.parent / "voice-briefs"

# Top-level keys a brief may carry (W2.1 PMC v4 shape). Used to reject
# obviously wrong uploads; every key is optional, and "_"-prefixed keys
# (e.g. "_source") are pipeline metadata and always allowed.
BRIEF_KEYS = frozenset({
    "voice", "amplify", "maintain", "mute", "audience", "value_prop",
    "customer_language", "proof_points", "constraints", "seasonal_patterns",
    "notes", "business", "generated_at", "source",
})


def brief_from_file(slug: Optional[str]) -> Optional[dict[str, Any]]:
    if not slug:
        return None
    path = VOICE_BRIEF_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_voice_brief(biz: Optional[Business]) -> Optional[dict[str, Any]]:
    """The business's brief, or None when it has none yet."""
    if biz is None:
        return None
    if isinstance(biz.voice_brief_json, dict) and biz.voice_brief_json:
        return biz.voice_brief_json
    return brief_from_file(biz.slug)


def validate_brief(data: Any) -> dict[str, Any]:
    """Raise ValueError unless `data` looks like a voice brief."""
    if not isinstance(data, dict) or not data:
        raise ValueError("A voice brief must be a JSON object.")
    unknown = sorted(k for k in data if k not in BRIEF_KEYS and not str(k).startswith("_"))
    if unknown:
        raise ValueError(f"Unrecognized brief fields: {', '.join(unknown)}")
    if not any(data.get(k) for k in ("voice", "amplify", "audience", "value_prop")):
        raise ValueError("The brief needs at least a voice, amplify list, audience, or value prop.")
    return data
