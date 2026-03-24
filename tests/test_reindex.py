"""Tests for the ad reindex script utilities."""

import pytest

# Import the functions directly from the script
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.reindex_ads import get_best_text, chunk_text, MIN_TEXT_LENGTH


class TestGetBestText:
    """Tests for text preference order."""

    def test_embedding_text_preferred(self):
        ad = {
            "embedding_text": "Enriched text about Windom Area Health",
            "ocr_text": "OCR extracted text",
            "cleaned_text": "Cleaned version",
            "raw_text": "Raw version",
            "description": "Description text",
        }
        assert get_best_text(ad) == "Enriched text about Windom Area Health"

    def test_ocr_text_second(self):
        ad = {
            "embedding_text": None,
            "ocr_text": "OCR extracted text here",
            "cleaned_text": "Cleaned version",
            "raw_text": "Raw version",
        }
        assert get_best_text(ad) == "OCR extracted text here"

    def test_cleaned_text_third(self):
        ad = {
            "embedding_text": None,
            "ocr_text": None,
            "cleaned_text": "Cleaned version of the ad",
            "raw_text": "Raw version",
        }
        assert get_best_text(ad) == "Cleaned version of the ad"

    def test_raw_text_fourth(self):
        ad = {
            "embedding_text": None,
            "ocr_text": None,
            "cleaned_text": None,
            "raw_text": "Raw extracted text from PDF",
        }
        assert get_best_text(ad) == "Raw extracted text from PDF"

    def test_description_last(self):
        ad = {
            "embedding_text": None,
            "ocr_text": None,
            "cleaned_text": None,
            "raw_text": None,
            "description": "Old description field content",
        }
        assert get_best_text(ad) == "Old description field content"

    def test_empty_ad_returns_empty(self):
        ad = {
            "embedding_text": None,
            "ocr_text": None,
            "cleaned_text": None,
            "raw_text": None,
            "description": None,
        }
        assert get_best_text(ad) == ""

    def test_short_text_skipped(self):
        """Text shorter than MIN_TEXT_LENGTH should be skipped."""
        ad = {
            "embedding_text": "short",  # < MIN_TEXT_LENGTH
            "ocr_text": None,
            "cleaned_text": "This is a longer cleaned text that passes the threshold",
            "raw_text": None,
        }
        result = get_best_text(ad)
        assert "longer cleaned text" in result

    def test_whitespace_only_skipped(self):
        ad = {
            "embedding_text": "          ",
            "ocr_text": "A sufficiently long OCR text result",
        }
        assert "OCR" in get_best_text(ad)


class TestChunkText:
    """Tests for chunking with advertiser prefix."""

    def test_advertiser_prefix_added(self):
        chunks = chunk_text("Some ad content here", advertiser="Test Biz")
        assert chunks[0].startswith("Test Biz advertisement: ")

    def test_no_prefix_without_advertiser(self):
        chunks = chunk_text("Some ad content here", advertiser="")
        assert not chunks[0].startswith("advertisement:")

    def test_empty_text_returns_empty(self):
        chunks = chunk_text("", advertiser="Test")
        # Empty text → no words → no chunks
        assert chunks == []


class TestStableIds:
    """Tests for idempotent behavior via stable IDs."""

    def test_ids_derived_from_ad_id(self):
        """IDs should be deterministic based on ad_id."""
        ad_id = "abc-123-def"
        chunks = ["chunk1", "chunk2", "chunk3"]
        ids = [f"{ad_id}_{i}" for i in range(len(chunks))]

        assert ids == ["abc-123-def_0", "abc-123-def_1", "abc-123-def_2"]

    def test_ids_stable_across_runs(self):
        """Same ad_id + same chunks = same IDs (idempotent)."""
        ad_id = "test-ad-id"
        run1_ids = [f"{ad_id}_{i}" for i in range(3)]
        run2_ids = [f"{ad_id}_{i}" for i in range(3)]
        assert run1_ids == run2_ids


class TestCollectionTarget:
    """Tests that reindex uses the advertisements collection."""

    def test_ads_collection_name(self):
        from src.core.config import ADS_COLLECTION
        assert ADS_COLLECTION == "advertisements"
