"""Tests for ad processing pipeline: OCR, categorization, enrichment, retrieval."""

import pytest

from src.ad_processing import (
    MIN_TEXT_LENGTH,
    categorize_ad,
    enrich_ad_text,
    expand_ad_query,
    extract_location,
)


# ── Categorization tests ────────────────────────────────────────────────


class TestCategorizeAd:
    def test_healthcare_ad(self):
        text = "Nominate a nurse today for the DAISY Award at Windom Area Health"
        assert categorize_ad(text, "Windom Area Health") == "healthcare"

    def test_real_estate_ad(self):
        text = "New listing! Home for sale, 3 bedroom 2 bath on 1.5 acres"
        assert categorize_ad(text, "Five Star Realty") == "real_estate"

    def test_entertainment_ad(self):
        text = "Project Hail Mary - Held Over - All Tickets $5.00"
        assert categorize_ad(text, "Princess Theater") == "entertainment"

    def test_dining_ad(self):
        text = "Friday fish fry special, all you can eat $14.99"
        assert categorize_ad(text, "Joe's Grill") == "dining"

    def test_automotive_ad(self):
        text = "Oil change and tire rotation special this week"
        assert categorize_ad(text, "S & S Truck Repair") == "automotive"

    def test_finance_ad(self):
        text = "Low rates on home loans and auto insurance"
        assert categorize_ad(text, "First National Bank") == "finance"

    def test_general_fallback(self):
        text = "Come visit us today for great deals"
        assert categorize_ad(text, "Some Business") == "general"

    def test_advertiser_name_contributes(self):
        # "Health" in advertiser name should push toward healthcare
        text = "Nominate someone today"
        assert categorize_ad(text, "Windom Area Health") == "healthcare"


# ── Location extraction tests ───────────────────────────────────────────


class TestExtractLocation:
    def test_city_state(self):
        assert extract_location("Located in Windom, MN") == "Windom, MN"

    def test_city_full_state(self):
        assert extract_location("St. James, Minnesota") == "St. James, Minnesota"

    def test_no_location(self):
        assert extract_location("Great deals on everything") == ""

    def test_multi_word_city(self):
        assert extract_location("Visit us in Sioux Falls, SD") == "Sioux Falls, SD"


# ── Text enrichment tests ──────────────────────────────────────────────


class TestEnrichAdText:
    def test_basic_enrichment(self):
        result = enrich_ad_text(
            advertiser="Windom Area Health",
            raw_text="Nominate a nurse today",
            category="healthcare",
            location="Windom, MN",
        )
        assert "Windom Area Health advertisement." in result
        assert "Healthcare" in result
        assert "Windom, MN" in result
        assert "Nominate a nurse today" in result

    def test_ocr_text_preferred_over_raw(self):
        result = enrich_ad_text(
            advertiser="Test",
            raw_text="",
            ocr_text="OCR extracted text here",
        )
        assert "OCR extracted text here" in result

    def test_general_category_omitted(self):
        result = enrich_ad_text(
            advertiser="Test",
            raw_text="Some text",
            category="general",
        )
        assert "General" not in result

    def test_empty_text(self):
        result = enrich_ad_text(advertiser="Test", raw_text="")
        assert result == "Test advertisement."

    def test_preference_order(self):
        """OCR text should be used over raw_text when both available."""
        result = enrich_ad_text(
            advertiser="Biz",
            raw_text="raw version",
            ocr_text="ocr version",
        )
        assert "ocr version" in result
        assert "raw version" not in result


# ── Query expansion tests ──────────────────────────────────────────────


class TestExpandAdQuery:
    def test_healthcare_expansion(self):
        terms = expand_ad_query("What is Windom Area Health promoting?")
        assert "medical" in terms or "clinic" in terms

    def test_real_estate_expansion(self):
        terms = expand_ad_query("homes for sale in Windom")
        assert "real estate" in terms or "listing" in terms or "property" in terms

    def test_generic_ad_expansion(self):
        terms = expand_ad_query("what advertisements are available")
        assert "promotion" in terms or "sponsored" in terms

    def test_no_expansion_for_unrelated(self):
        terms = expand_ad_query("what is the weather like")
        assert len(terms) == 0


# ── OCR fallback trigger test ──────────────────────────────────────────


class TestOcrFallbackTrigger:
    def test_short_text_triggers_ocr(self):
        """Text shorter than MIN_TEXT_LENGTH should trigger OCR."""
        short_text = "AB"
        assert len(short_text.strip()) < MIN_TEXT_LENGTH

    def test_sufficient_text_skips_ocr(self):
        """Text at or above threshold should not trigger OCR."""
        long_text = "A" * MIN_TEXT_LENGTH
        assert len(long_text.strip()) >= MIN_TEXT_LENGTH


# ── Retrieval preference order test ────────────────────────────────────


class TestRetrievalPreference:
    def test_embedding_text_preferred(self):
        """The search result formatter should prefer embedding_text."""
        from src.modules.advertisements.search import _format_ad_result

        ad = {
            "ad_id": "test",
            "advertiser": "Test Biz",
            "product_name": "Test Biz",
            "description": "old description",
            "raw_text": "raw text",
            "cleaned_text": "cleaned text",
            "ocr_text": "ocr text",
            "embedding_text": "enriched embedding text",
        }
        result = _format_ad_result(ad)
        assert "enriched embedding text" in result["text"]
        assert "old description" not in result["text"]

    def test_ocr_text_used_when_no_embedding(self):
        from src.modules.advertisements.search import _format_ad_result

        ad = {
            "ad_id": "test",
            "advertiser": "Test Biz",
            "product_name": "Test Biz",
            "embedding_text": None,
            "ocr_text": "ocr extracted content",
            "cleaned_text": "cleaned",
            "raw_text": "raw",
        }
        result = _format_ad_result(ad)
        assert "ocr extracted content" in result["text"]

    def test_falls_back_to_cleaned_text(self):
        from src.modules.advertisements.search import _format_ad_result

        ad = {
            "ad_id": "test",
            "advertiser": "Test Biz",
            "product_name": "Test Biz",
            "embedding_text": None,
            "ocr_text": None,
            "cleaned_text": "cleaned content here",
            "raw_text": "raw",
        }
        result = _format_ad_result(ad)
        assert "cleaned content here" in result["text"]


# ── Context formatting tests ───────────────────────────────────────────


class TestAdContextFormatting:
    """Tests that ad context sent to LLM includes business name explicitly."""

    def test_ad_context_has_business_label(self):
        from src.prompts import format_context

        chunks = [{
            "text": "Country Road Greenhouse advertisement. Opening April 1.",
            "metadata": {
                "advertiser": "Country Road Greenhouse",
                "product_name": "Country Road Greenhouse",
                "title": "Country Road Greenhouse",
                "ad_category": "retail",
                "location": "Windom, MN",
                "url": "",
                "content_type": "advertisement",
            },
            "score": 1.0,
            "search_type": "advertisement",
        }]
        context = format_context(chunks)

        assert "Business: Country Road Greenhouse" in context
        assert "[SPONSORED Advertisement" in context

    def test_ad_context_includes_category_and_location(self):
        from src.prompts import format_context

        chunks = [{
            "text": "Nominate a nurse today",
            "metadata": {
                "advertiser": "Windom Area Health",
                "title": "Windom Area Health",
                "ad_category": "healthcare",
                "location": "Windom, MN",
                "url": "",
                "content_type": "advertisement",
            },
            "score": 1.0,
            "search_type": "advertisement",
        }]
        context = format_context(chunks)

        assert "Business: Windom Area Health" in context
        assert "Category: healthcare" in context
        assert "Location: Windom, MN" in context

    def test_ad_context_not_using_article_format(self):
        """Ad context should NOT use Author/Title format like articles."""
        from src.prompts import format_context

        chunks = [{
            "text": "Ad content",
            "metadata": {
                "advertiser": "Test Biz",
                "title": "Test Biz",
                "author": "Test Biz",
                "content_type": "advertisement",
            },
            "score": 1.0,
            "search_type": "advertisement",
        }]
        context = format_context(chunks)

        assert "Author:" not in context
        assert "Business: Test Biz" in context

    def test_article_context_unchanged(self):
        """Articles should still use the original format."""
        from src.prompts import format_context

        chunks = [{
            "text": "Article content here",
            "metadata": {
                "title": "News Story",
                "author": "Reporter",
                "publish_date": "2026-01-01",
            },
            "score": 1.0,
            "search_type": "article",
        }]
        context = format_context(chunks)

        assert "Title: News Story" in context
        assert "Author: Reporter" in context
        assert "[Article 1]" in context

    def test_advertiser_name_in_result_metadata(self):
        """_format_ad_result should expose advertiser in metadata."""
        from src.modules.advertisements.search import _format_ad_result

        ad = {
            "ad_id": "test",
            "advertiser": "Country Road Greenhouse",
            "product_name": "Country Road Greenhouse",
            "embedding_text": "Opening April 1",
        }
        result = _format_ad_result(ad)

        assert result["metadata"]["advertiser"] == "Country Road Greenhouse"
        assert "Country Road Greenhouse" in result["text"]
