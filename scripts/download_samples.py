#!/usr/bin/env python
"""Download sample news articles from public RSS feeds."""

import sys
import time
from datetime import datetime
from pathlib import Path

import feedparser

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import DOCUMENTS_DIR

# Public RSS feeds from various news sources
RSS_FEEDS = [
    ("https://feeds.npr.org/1001/rss.xml", "NPR News"),
    ("https://feeds.bbci.co.uk/news/rss.xml", "BBC News"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "NY Times"),
    ("https://feeds.reuters.com/reuters/topNews", "Reuters"),
    ("https://www.theguardian.com/world/rss", "The Guardian"),
    ("https://feeds.washingtonpost.com/rss/national", "Washington Post"),
    ("https://rss.cnn.com/rss/edition.rss", "CNN"),
    ("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica"),
    ("https://www.wired.com/feed/rss", "Wired"),
    ("https://feeds.feedburner.com/TechCrunch", "TechCrunch"),
]


def clean_text(text: str) -> str:
    """Clean HTML and special characters from text."""
    import re

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Fix HTML entities
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")
    # Remove extra whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitize_filename(title: str) -> str:
    """Convert title to a safe filename."""
    import re

    # Remove special characters
    filename = re.sub(r"[^\w\s-]", "", title)
    # Replace spaces with underscores
    filename = re.sub(r"\s+", "_", filename)
    # Truncate to reasonable length
    return filename[:80]


def download_articles(target_count: int = 50) -> None:
    """Download articles from RSS feeds.

    Args:
        target_count: Target number of articles to download.
    """
    print(f"Downloading {target_count} sample news articles...")
    print(f"Saving to: {DOCUMENTS_DIR}")
    print("=" * 50)

    downloaded = 0
    seen_titles = set()

    for feed_url, source_name in RSS_FEEDS:
        if downloaded >= target_count:
            break

        print(f"\nFetching from {source_name}...")

        try:
            feed = feedparser.parse(feed_url)

            if feed.bozo:
                print(f"  Warning: Feed parsing issue for {source_name}")

            for entry in feed.entries:
                if downloaded >= target_count:
                    break

                # Get title
                title = clean_text(entry.get("title", "Untitled"))

                # Skip duplicates
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                # Get content (try multiple fields)
                content = ""
                if "content" in entry:
                    content = entry.content[0].get("value", "")
                elif "summary" in entry:
                    content = entry.summary
                elif "description" in entry:
                    content = entry.description

                content = clean_text(content)

                # Skip if no meaningful content
                if len(content) < 100:
                    continue

                # Get metadata
                author = entry.get("author", source_name)
                published = entry.get("published", "")
                link = entry.get("link", "")

                # Parse date
                if published:
                    try:
                        # Try to parse the date
                        if (
                            hasattr(entry, "published_parsed")
                            and entry.published_parsed
                        ):
                            pub_date = datetime(*entry.published_parsed[:6])
                            date_str = pub_date.strftime("%Y-%m-%d")
                        else:
                            date_str = datetime.now().strftime("%Y-%m-%d")
                    except Exception:
                        date_str = datetime.now().strftime("%Y-%m-%d")
                else:
                    date_str = datetime.now().strftime("%Y-%m-%d")

                # Create filename
                filename = f"{date_str}_{sanitize_filename(title)}.txt"
                filepath = DOCUMENTS_DIR / filename

                # Write article
                article_content = f"""{title}

Author: {author}
Date: {date_str}
Source: {source_name}
URL: {link}

{content}
"""

                filepath.write_text(article_content, encoding="utf-8")
                downloaded += 1
                print(f"  [{downloaded}/{target_count}] {title[:60]}...")

        except Exception as e:
            print(f"  Error fetching {source_name}: {e}")
            continue

        # Small delay between feeds
        time.sleep(0.5)

    print("\n" + "=" * 50)
    print(f"Downloaded {downloaded} articles to {DOCUMENTS_DIR}")

    if downloaded < target_count:
        print(f"\nNote: Only {downloaded} articles available from feeds.")
        print("You may want to add more documents manually.")


def main() -> None:
    """Run the download script."""
    import argparse

    parser = argparse.ArgumentParser(description="Download sample news articles")
    parser.add_argument(
        "--count",
        "-n",
        type=int,
        default=50,
        help="Number of articles to download (default: 50)",
    )

    args = parser.parse_args()
    download_articles(args.count)


if __name__ == "__main__":
    main()
