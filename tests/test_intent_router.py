"""Tests for intent router and content orchestrator."""

import pytest

from src.intent_router import (
    AD_BUSINESS,
    ARTICLE_NEWS,
    MIXED_DISCOVERY,
    classify_intent,
)


class TestIntentClassification:
    """Tests for classify_intent()."""

    # ── Ad/business intent ──────────────────────────────────────────

    def test_specific_advertiser_query(self):
        assert classify_intent("What is Princess Theater advertising?") == AD_BUSINESS

    def test_where_can_i_buy(self):
        assert classify_intent("Where can I get flowers in Windom?") == AD_BUSINESS

    def test_deals_query(self):
        assert classify_intent("What deals are available?") == AD_BUSINESS

    def test_homes_for_sale(self):
        assert classify_intent("Are there homes for sale in Windom?") == AD_BUSINESS

    def test_who_sells(self):
        assert classify_intent("Who sells auto parts around here?") == AD_BUSINESS

    def test_restaurant_query(self):
        assert classify_intent("Any good restaurants in town?") == AD_BUSINESS

    def test_what_ads(self):
        assert classify_intent("What advertisements are available?") == AD_BUSINESS

    def test_business_promoting(self):
        assert classify_intent("What is Windom Area Health promoting?") == AD_BUSINESS

    def test_greenhouse_query(self):
        assert classify_intent("Is there a greenhouse nearby?") == AD_BUSINESS

    # ── News/article intent ─────────────────────────────────────────

    def test_city_council_news(self):
        assert classify_intent("What did the city council approve?") == ARTICLE_NEWS

    def test_sports_query(self):
        assert classify_intent("How did the game go last night?") == ARTICLE_NEWS

    def test_crime_report(self):
        assert classify_intent("Were there any arrests this week?") == ARTICLE_NEWS

    def test_weather_query(self):
        assert classify_intent("What's the weather forecast?") == ARTICLE_NEWS

    def test_news_keyword(self):
        assert classify_intent("What's in the news today?") == ARTICLE_NEWS

    def test_editorial(self):
        assert classify_intent("Any editorials about education?") == ARTICLE_NEWS

    def test_school_board(self):
        assert classify_intent("What happened at the school board meeting?") == ARTICLE_NEWS

    # ── Mixed/local discovery intent ────────────────────────────────

    def test_whats_happening(self):
        assert classify_intent("What's happening this weekend?") == MIXED_DISCOVERY

    def test_things_to_do(self):
        assert classify_intent("What are things to do in Windom?") == MIXED_DISCOVERY

    def test_local_events(self):
        assert classify_intent("Any local events coming up?") == MIXED_DISCOVERY

    # ── Ambiguous defaults to mixed ─────────────────────────────────

    def test_vague_query(self):
        assert classify_intent("Tell me something interesting") == MIXED_DISCOVERY

    def test_hello(self):
        assert classify_intent("Hello") == MIXED_DISCOVERY


class TestCollectionSeparation:
    """Tests that collections are correctly named and separate."""

    def test_collection_names_are_distinct(self):
        from src.core.config import ADS_COLLECTION, ARTICLES_COLLECTION, COLLECTION_NAME

        assert ARTICLES_COLLECTION == "articles"
        assert ADS_COLLECTION == "advertisements"
        assert COLLECTION_NAME == "publisher_main"
        assert ARTICLES_COLLECTION != ADS_COLLECTION
        assert ARTICLES_COLLECTION != COLLECTION_NAME


class TestOrchestratorRouting:
    """Tests that orchestrator routes intent to correct search domains."""

    def test_ad_intent_searches_ads_first(self):
        """For ad intent, ads should appear before articles in results."""
        # This is a structural test — verify the orchestrator calls the right methods
        from src.content_orchestrator import ContentOrchestrator
        from unittest.mock import MagicMock, patch

        orch = ContentOrchestrator.__new__(ContentOrchestrator)
        orch.tools = MagicMock()

        # Mock search methods
        ad_result = [{"text": "ad", "score": 1.5, "search_type": "advertisement", "metadata": {}}]
        article_result = [{"text": "article", "score": 0.8, "search_type": "semantic", "metadata": {}}]
        event_result = []

        orch.tools.search_advertisements.return_value = ad_result
        orch.tools.hybrid_search.return_value = article_result
        orch.tools.search_events.return_value = event_result

        with patch("src.content_orchestrator.classify_intent", return_value="advertisement_business"):
            results = orch.search("What is Princess Theater advertising?")

        assert len(results) == 2
        assert results[0]["search_type"] == "advertisement"  # Ads ranked first (score 1.5 > 0.8)

    def test_news_intent_searches_articles_first(self):
        from src.content_orchestrator import ContentOrchestrator
        from unittest.mock import MagicMock, patch

        orch = ContentOrchestrator.__new__(ContentOrchestrator)
        orch.tools = MagicMock()

        article_result = [{"text": "news", "score": 0.9, "search_type": "semantic", "metadata": {}}]
        ad_result = [{"text": "ad", "score": 0.5, "search_type": "advertisement", "metadata": {}}]

        orch.tools.hybrid_search.return_value = article_result
        orch.tools.search_advertisements.return_value = ad_result
        orch.tools.search_events.return_value = []

        with patch("src.content_orchestrator.classify_intent", return_value="article_news"):
            results = orch.search("What did the city council decide?")

        assert len(results) == 2
        assert results[0]["search_type"] == "semantic"  # Articles first
