"""CLI: ingest articles from a publisher RSS feed.

Usage:
    uv run python scripts/ingest_rss.py \
        --publisher "Cottonwood County Citizen" \
        --rss-url https://windomnews.com/feed \
        --since 2026-04-01

Options:
    --publisher   Publisher display name (must match DB)
    --rss-url     Full RSS/Atom feed URL
    --since       ISO date — only ingest articles on or after this date
    --edition-date  Override publish date for all articles (YYYY-MM-DD)
    --dry-run     Fetch + extract but do NOT write to DB
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest articles from an RSS feed")
    parser.add_argument("--publisher", required=True, help="Publisher display name")
    parser.add_argument("--rss-url", required=True, help="RSS/Atom feed URL")
    parser.add_argument("--since", default=None, help="ISO date filter (YYYY-MM-DD)")
    parser.add_argument(
        "--edition-date",
        default=None,
        help="Override publish date for all articles (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and extract but do not write to DB",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max articles to process from the feed (default 200)",
    )
    args = parser.parse_args()

    from src.modules.ingestion.rss_ingestor import RSSIngestor

    print(f"Fetching RSS: {args.rss_url}")
    print(f"Publisher:   {args.publisher}")
    if args.since:
        print(f"Since:       {args.since}")
    if args.dry_run:
        print("DRY RUN — no DB writes")

    ingestor = RSSIngestor(args.rss_url)
    articles = ingestor.fetch(since=args.since, limit=args.limit)

    print(f"\nExtracted {len(articles)} articles from feed.")

    if not articles:
        print("Nothing to ingest.")
        return

    if args.dry_run:
        for i, a in enumerate(articles[:5], 1):
            print(
                f"  [{i}] {a['publish_date']} | {a['headline'][:70]} "
                f"({len(a['body_text'])} chars)"
            )
        if len(articles) > 5:
            print(f"  ... and {len(articles) - 5} more")
        return

    # Override publish date if requested
    if args.edition_date:
        for a in articles:
            a["publish_date"] = args.edition_date

    # Write to DB via shared write layer
    from src.modules.extraction.shared_write_layer import write_articles

    result = write_articles(
        articles=articles,
        publisher_name=args.publisher,
        edition_date=args.edition_date or (articles[0]["publish_date"] if articles else None),
        source_filename=f"rss:{args.rss_url}",
        mark_current=False,  # RSS articles are treated as historical unless caller says otherwise
    )

    print(f"\nIngestion complete:")
    print(f"  articles_written:      {result.get('articles_written', 0)}")
    print(f"  content_items_written: {result.get('content_items_written', 0)}")
    print(f"  chunks_indexed:        {result.get('chunks_indexed', 0)}")


if __name__ == "__main__":
    main()
