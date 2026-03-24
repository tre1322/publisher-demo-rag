"""Tenant-aware file storage and edition upload for publishers."""

import hashlib
import logging
from pathlib import Path

from src.core.config import DATA_DIR
from src.core.database import get_connection
from src.modules.editions import get_edition_by_checksum, insert_edition
from src.modules.publishers.database import get_publisher

logger = logging.getLogger(__name__)

# Base directory for all publisher edition uploads
UPLOADS_BASE = DATA_DIR / "publisher_editions"


def _repair_edition(
    edition_id: int,
    publisher_id: int | None = None,
    pdf_path: str | None = None,
    upload_status: str | None = None,
) -> None:
    """Backfill missing fields on a legacy edition row."""
    conn = get_connection()
    cursor = conn.cursor()
    updates = []
    params: list = []

    if publisher_id is not None:
        updates.append("publisher_id = ?")
        params.append(publisher_id)
    if pdf_path is not None:
        updates.append("pdf_path = ?")
        params.append(pdf_path)
    if upload_status is not None:
        updates.append("upload_status = ?")
        params.append(upload_status)

    if updates:
        params.append(edition_id)
        sql = f"UPDATE editions SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(sql, params)
        conn.commit()
        logger.info(f"Edition {edition_id} repaired: {', '.join(updates)}")
    conn.close()


def get_publisher_upload_dir(publisher_slug: str) -> Path:
    """Get the tenant-aware upload directory for a publisher.

    Creates the directory if it doesn't exist.

    Args:
        publisher_slug: Publisher's URL-safe slug.

    Returns:
        Path to the publisher's upload directory.
    """
    upload_dir = UPLOADS_BASE / publisher_slug
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def compute_checksum(data: bytes) -> str:
    """Compute SHA-256 checksum of bytes."""
    return hashlib.sha256(data).hexdigest()


def upload_edition(
    publisher_id: int,
    data: bytes,
    filename: str,
    edition_date: str | None = None,
    issue_label: str | None = None,
) -> dict:
    """Upload an edition PDF for a publisher.

    Stores the file in the tenant-aware path, creates an edition record,
    and sets initial statuses. Does NOT perform extraction (Phase 0).

    Args:
        publisher_id: Publisher ID.
        data: Raw PDF bytes.
        filename: Original filename.
        edition_date: Edition date (YYYY-MM-DD).
        issue_label: Optional issue label (e.g., "Vol. 12 No. 3").

    Returns:
        Dict with upload result info.
    """
    result = {
        "filename": filename,
        "edition_id": None,
        "publisher_id": publisher_id,
        "error": None,
        "duplicate": False,
        "file_path": None,
        "upload_status": "pending",
        "extraction_status": "not_started",
        "homepage_status": "not_started",
    }

    # Get publisher info for tenant path
    publisher = get_publisher(publisher_id)
    if not publisher:
        result["error"] = f"Publisher ID {publisher_id} not found"
        logger.error(f"Edition upload failed: {result['error']}")
        return result

    publisher_slug = publisher["slug"]

    # Compute checksum for dedup
    checksum = compute_checksum(data)
    logger.info(
        f"Edition upload: publisher='{publisher['name']}', "
        f"file='{filename}', checksum={checksum[:12]}..."
    )

    # Check for duplicate
    existing = get_edition_by_checksum(checksum)
    if existing:
        edition_id = existing["id"]
        is_incomplete = (
            not existing.get("publisher_id")
            or not existing.get("pdf_path")
            or existing.get("upload_status") == "pending"
        )

        if is_incomplete:
            # Repair the legacy row: store file and backfill missing fields
            logger.info(
                f"Repairing incomplete legacy edition {edition_id} "
                f"for publisher '{publisher['name']}'"
            )
            upload_dir = get_publisher_upload_dir(publisher_slug)
            dest_path = upload_dir / filename
            if dest_path.exists():
                stem, suffix = dest_path.stem, dest_path.suffix
                counter = 1
                while dest_path.exists():
                    dest_path = upload_dir / f"{stem}_{counter}{suffix}"
                    counter += 1
            try:
                dest_path.write_bytes(data)
            except Exception as e:
                logger.error(f"Failed to store file during repair: {e}")
                dest_path = None

            _repair_edition(
                edition_id=edition_id,
                publisher_id=publisher_id,
                pdf_path=str(dest_path) if dest_path else None,
                upload_status="uploaded",
            )
            result["edition_id"] = edition_id
            result["duplicate"] = True
            result["upload_status"] = "uploaded"
            result["file_path"] = str(dest_path) if dest_path else None
            logger.info(f"Legacy edition {edition_id} repaired")
            return result

        # Already complete duplicate — just return it
        result["error"] = "Duplicate edition (checksum match)"
        result["duplicate"] = True
        result["edition_id"] = edition_id
        logger.info(
            f"Edition upload duplicate: '{filename}' matches "
            f"edition {edition_id}"
        )
        return result

    # Store file in tenant-aware path
    upload_dir = get_publisher_upload_dir(publisher_slug)
    dest_path = upload_dir / filename

    # Avoid overwriting — append counter if file exists
    if dest_path.exists():
        stem = dest_path.stem
        suffix = dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = upload_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    try:
        dest_path.write_bytes(data)
        logger.info(f"Edition file stored: {dest_path}")
    except Exception as e:
        result["error"] = f"Failed to store file: {e}"
        logger.error(f"Edition file storage failed: {e}")
        return result

    result["file_path"] = str(dest_path)

    # Create edition record
    try:
        edition_id = insert_edition(
            source_filename=filename,
            publication_id=None,  # Will be linked in later phases
            publisher_id=publisher_id,
            edition_date=edition_date,
            issue_label=issue_label,
            checksum=checksum,
            pdf_path=str(dest_path),
            upload_status="uploaded",
            extraction_status="not_started",
            homepage_batch_status="not_started",
        )
        result["edition_id"] = edition_id
        result["upload_status"] = "uploaded"
        logger.info(
            f"Edition record created: id={edition_id}, "
            f"publisher='{publisher['name']}', file='{filename}', "
            f"pdf_path='{dest_path}'"
        )

    except Exception as e:
        result["error"] = f"Failed to create edition record: {e}"
        logger.error(f"Edition record creation failed: {e}")
        # Clean up stored file
        try:
            dest_path.unlink()
        except Exception:
            pass
        return result

    return result
