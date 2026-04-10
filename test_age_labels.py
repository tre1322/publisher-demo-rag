"""Test age labeling in article context."""

from datetime import datetime, timedelta
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.prompts import _calculate_age_label, format_context


def test_age_label_calculation():
    """Test the age label helper function."""
    print("=" * 70)
    print("AGE LABEL CALCULATION TESTS")
    print("=" * 70)

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    two_weeks_ago = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    two_months_ago = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    one_year_ago = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

    test_cases = [
        (today, "Published today"),
        (yesterday, "Published yesterday"),
        (three_days_ago, "Published 3 days ago"),
        (two_weeks_ago, "Published 2 weeks ago"),
        (two_months_ago, "Published 2 months ago"),
        (one_year_ago, "Published 1 year ago"),
        ("Unknown date", ""),
        ("", ""),
    ]

    for date, expected in test_cases:
        result = _calculate_age_label(date)
        status = "✓" if result == expected else "✗"
        print(f"\n{status} Date: {date}")
        print(f"  Expected: '{expected}'")
        print(f"  Got:      '{result}'")
        if result != expected:
            print(f"  MISMATCH!")

    print("\n" + "=" * 70)
    print("CONTEXT FORMATTING TEST")
    print("=" * 70)

    # Test format_context with age labels
    chunks = [
        {
            "text": "The library renovation project was approved with a budget of $500,000.",
            "metadata": {
                "title": "Library Renovation Approved",
                "publish_date": two_weeks_ago,
                "author": "Jane Smith",
                "url": "https://example.com/article1",
            },
            "search_type": "article",
        },
        {
            "text": "The spring fundraiser exceeded expectations, raising $50,000.",
            "metadata": {
                "title": "Fundraiser Success",
                "publish_date": three_days_ago,
                "author": "Bob Jones",
                "url": "https://example.com/article2",
            },
            "search_type": "article",
        },
    ]

    context = format_context(chunks)

    print("\nFormatted Context:")
    print("-" * 70)
    print(context)
    print("-" * 70)

    # Verify age labels are present
    if "Published 2 weeks ago" in context:
        print("\n✓ Age label for 2-week-old article found")
    else:
        print("\n✗ Age label for 2-week-old article NOT found")

    if "Published 3 days ago" in context:
        print("✓ Age label for 3-day-old article found")
    else:
        print("✗ Age label for 3-day-old article NOT found")

    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print("""
What the LLM will now see:

[Article 1 - Published 2 weeks ago]
Title: Library Renovation Approved
Date: 2026-03-27
...

This tells the LLM:
1. The article is 2 weeks old
2. The LLM should mention this age when citing it
3. For time-sensitive info, it can note this might be outdated

Combined with the system prompt rules, the LLM will now say:
"An article from 2 weeks ago reported that the library renovation..."

Instead of:
"The library renovation was approved..." (no age context)
""")


if __name__ == "__main__":
    test_age_label_calculation()
