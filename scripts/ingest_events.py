#!/usr/bin/env python
"""Ingest events from files into the database."""

import argparse
import csv
import json
import logging
import re
import sys
import uuid
from pathlib import Path

from bs4 import BeautifulSoup

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.modules.events import insert_event, get_event_count
from src.metadata_extractor_events import EventMetadataExtractor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EVENTS_DIR = Path(__file__).parent.parent / "data" / "events"


def parse_txt_file(file_path: Path) -> list[dict]:
    """Parse a plain text event file.

    Args:
        file_path: Path to the text file.

    Returns:
        List containing single event dict with raw_text.
    """
    raw_text = file_path.read_text(encoding="utf-8")
    return [{"raw_text": raw_text, "source_file": file_path.name}]


def parse_html_file(file_path: Path) -> list[dict]:
    """Parse an HTML event file, extracting text content.

    Args:
        file_path: Path to the HTML file.

    Returns:
        List containing single event dict with raw_text.
    """
    html_content = file_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove script and style elements
    for element in soup(["script", "style", "head", "meta", "link"]):
        element.decompose()

    # Get text and clean up whitespace
    text = soup.get_text(separator="\n")
    # Clean up excessive whitespace while preserving paragraph breaks
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    # Collapse multiple newlines to max 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return [{"raw_text": text.strip(), "source_file": file_path.name}]


def parse_json_file(file_path: Path) -> list[dict]:
    """Parse a JSON event file.

    Args:
        file_path: Path to the JSON file.

    Returns:
        List of event dictionaries.
    """
    content = file_path.read_text(encoding="utf-8")
    data = json.loads(content)

    # Handle single object or array
    if isinstance(data, dict):
        data = [data]

    # Add source file and raw_text for each event
    for event in data:
        event["source_file"] = file_path.name
        event["raw_text"] = json.dumps(event, indent=2)

    return data


def parse_csv_file(file_path: Path) -> list[dict]:
    """Parse a CSV event file.

    Args:
        file_path: Path to the CSV file.

    Returns:
        List of event dictionaries.
    """
    events = []
    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert empty strings to None
            event = {k: (v if v else None) for k, v in row.items()}

            # Convert price to float
            if event.get("price"):
                try:
                    event["price"] = float(event["price"])
                except ValueError:
                    event["price"] = None

            event["source_file"] = file_path.name
            event["raw_text"] = json.dumps(row)
            events.append(event)

    return events


def ingest_event(
    event_data: dict,
    extractor: EventMetadataExtractor | None = None,
    publisher: str | None = None,
) -> bool:
    """Ingest a single event.

    Args:
        event_data: Event data dictionary.
        extractor: Optional metadata extractor for AI enhancement.
        publisher: Name of the publishing newspaper.

    Returns:
        True if successful.
    """
    raw_text = event_data.get("raw_text", "")
    source_file = event_data.get("source_file", "unknown")

    # Check if this is raw text that needs full extraction
    needs_full_extraction = "title" not in event_data

    if needs_full_extraction and extractor:
        logger.info(f"Extracting metadata from raw text: {source_file}")
        extracted = extractor.extract_from_raw_text(raw_text)
        # Merge extracted data with any existing fields
        for key, value in extracted.items():
            if key not in event_data or event_data[key] is None:
                event_data[key] = value
    elif extractor and not needs_full_extraction:
        # Enhance existing structured data
        enhanced = extractor.enhance_metadata(
            title=event_data.get("title", ""),
            description=event_data.get("description"),
            location=event_data.get("location"),
            address=event_data.get("address"),
            event_date=event_data.get("event_date"),
            event_time=event_data.get("event_time"),
            category=event_data.get("category"),
            price=event_data.get("price"),
        )
        # Apply enhancements for missing fields only
        for key, value in enhanced.items():
            if key not in event_data or event_data[key] is None:
                event_data[key] = value

    # Validate required fields
    if not event_data.get("title"):
        logger.error(f"Missing required title field in {source_file}")
        return False

    # Generate ID
    event_id = str(uuid.uuid4())

    # Insert into database
    insert_event(
        event_id=event_id,
        title=event_data.get("title", ""),
        description=event_data.get("description"),
        location=event_data.get("location"),
        address=event_data.get("address"),
        event_date=event_data.get("event_date"),
        event_time=event_data.get("event_time"),
        end_time=event_data.get("end_time"),
        category=event_data.get("category"),
        price=event_data.get("price"),
        url=event_data.get("url"),
        raw_text=raw_text,
        publisher=publisher,
    )

    logger.info(
        f"Ingested event: {event_data.get('title')} - {event_data.get('event_date')}"
    )
    return True


def ingest_file(
    file_path: Path,
    extractor: EventMetadataExtractor | None = None,
    publisher: str | None = None,
) -> dict:
    """Ingest events from a single file.

    Args:
        file_path: Path to the file.
        extractor: Optional metadata extractor.
        publisher: Name of the publishing newspaper.

    Returns:
        Results summary dict.
    """
    results = {"file": file_path.name, "success": 0, "failed": 0, "errors": []}

    suffix = file_path.suffix.lower()

    try:
        if suffix == ".txt":
            events = parse_txt_file(file_path)
        elif suffix == ".json":
            events = parse_json_file(file_path)
        elif suffix == ".csv":
            events = parse_csv_file(file_path)
        elif suffix in (".html", ".htm"):
            events = parse_html_file(file_path)
        else:
            results["errors"].append(f"Unsupported file type: {suffix}")
            return results
    except Exception as e:
        results["errors"].append(f"Failed to parse file: {e}")
        return results

    for event in events:
        try:
            if ingest_event(event, extractor, publisher=publisher):
                results["success"] += 1
            else:
                results["failed"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(str(e))

    return results


def ingest_all(
    directory: Path = EVENTS_DIR,
    use_metadata_extraction: bool = True,
    publisher: str | None = None,
) -> dict:
    """Ingest all event files from a directory.

    Args:
        directory: Directory containing event files.
        use_metadata_extraction: Whether to use AI metadata extraction.
        publisher: Name of the publishing newspaper.

    Returns:
        Summary of ingestion results.
    """
    results = {
        "total_files": 0,
        "total_events": 0,
        "failed_events": 0,
        "errors": [],
    }

    if not directory.exists():
        logger.warning(f"Directory does not exist: {directory}")
        return results

    # Initialize extractor if needed
    extractor = None
    if use_metadata_extraction:
        try:
            extractor = EventMetadataExtractor()
            logger.info("AI metadata extraction enabled")
        except Exception as e:
            logger.warning(f"Could not initialize metadata extractor: {e}")

    # Find all supported files
    files = (
        list(directory.glob("*.txt"))
        + list(directory.glob("*.json"))
        + list(directory.glob("*.csv"))
        + list(directory.glob("*.html"))
        + list(directory.glob("*.htm"))
    )
    results["total_files"] = len(files)

    for file_path in files:
        logger.info(f"Processing: {file_path.name}")
        file_results = ingest_file(file_path, extractor, publisher=publisher)
        results["total_events"] += file_results["success"]
        results["failed_events"] += file_results["failed"]
        results["errors"].extend(file_results["errors"])

    return results


def main() -> None:
    """Run event ingestion from command line."""
    parser = argparse.ArgumentParser(
        description="Ingest events from files into the database"
    )
    parser.add_argument(
        "--file",
        "-f",
        type=str,
        help="Ingest a specific file instead of all files in data/events/",
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Disable AI metadata extraction",
    )
    parser.add_argument(
        "--dir",
        "-d",
        type=str,
        default=str(EVENTS_DIR),
        help="Directory to ingest from (default: data/events/)",
    )
    parser.add_argument(
        "--publisher",
        "-p",
        type=str,
        help="Name of the publishing newspaper",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Publisher RAG Demo - Event Ingestion")
    print("=" * 60)

    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"Error: File not found: {file_path}")
            sys.exit(1)

        extractor = None
        if not args.no_metadata:
            try:
                extractor = EventMetadataExtractor()
            except Exception as e:
                print(f"Warning: Could not initialize metadata extractor: {e}")

        results = ingest_file(file_path, extractor, publisher=args.publisher)
        print(f"\nFile: {results['file']}")
        print(f"Events ingested: {results['success']}")
        print(f"Events failed: {results['failed']}")
    else:
        directory = Path(args.dir)
        results = ingest_all(
            directory=directory,
            use_metadata_extraction=not args.no_metadata,
            publisher=args.publisher,
        )

        print(f"\nFiles processed: {results['total_files']}")
        print(f"Events ingested: {results['total_events']}")
        print(f"Events failed: {results['failed_events']}")

    if results.get("errors"):
        print("\nErrors:")
        for error in results["errors"]:
            print(f"  - {error}")

    print(f"\nTotal events in database: {get_event_count()}")


if __name__ == "__main__":
    main()
