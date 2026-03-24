"""Tests for Phase 0: publisher schema, seeding, edition uploads, tenant paths."""

import hashlib
import sqlite3
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest


def _unique_pdf(label: str = "") -> bytes:
    """Generate unique fake PDF bytes to avoid checksum collisions across test runs."""
    return f"%PDF-1.4 {label} {uuid.uuid4()}".encode()


class TestCleanBoot:
    """Tests for clean startup on empty DB."""

    def test_init_all_tables_creates_publishers(self):
        """init_all_tables should create the publishers table."""
        from src.core.database import get_connection, init_all_tables

        init_all_tables()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='publishers'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_init_all_tables_creates_content_items(self):
        """init_all_tables should create the content_items table."""
        from src.core.database import get_connection, init_all_tables

        init_all_tables()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='content_items'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_publishers_table_has_required_columns(self):
        """Publishers table should have all required columns."""
        from src.core.database import get_connection, init_all_tables

        init_all_tables()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(publishers)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "name" in columns
        assert "slug" in columns
        assert "market" in columns
        assert "state" in columns
        assert "active" in columns
        assert "created_at" in columns

    def test_content_items_table_has_required_columns(self):
        """Content items table should have all required columns."""
        from src.core.database import get_connection, init_all_tables

        init_all_tables()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(content_items)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "edition_id" in columns
        assert "publisher_id" in columns
        assert "content_type" in columns
        assert "status" in columns


class TestPublisherSeeding:
    """Tests for idempotent publisher seeding."""

    def test_seed_creates_two_publishers(self):
        """Seeding should create Cottonwood County Citizen and Pipestone Star."""
        from src.modules.publishers import seed_publishers, get_all_publishers_db

        seed_publishers()
        publishers = get_all_publishers_db()
        names = {p["name"] for p in publishers}

        assert "Cottonwood County Citizen" in names
        assert "Pipestone Star" in names

    def test_seed_is_idempotent(self):
        """Running seed twice should not duplicate publishers."""
        from src.modules.publishers import seed_publishers, get_all_publishers_db

        seed_publishers()
        count_1 = len(get_all_publishers_db())

        seed_publishers()
        count_2 = len(get_all_publishers_db())

        assert count_1 == count_2

    def test_publisher_has_correct_slugs(self):
        """Publishers should have correct auto-generated slugs."""
        from src.modules.publishers import seed_publishers, get_publisher_by_slug

        seed_publishers()

        ccc = get_publisher_by_slug("cottonwood-county-citizen")
        assert ccc is not None
        assert ccc["name"] == "Cottonwood County Citizen"

        ps = get_publisher_by_slug("pipestone-star")
        assert ps is not None
        assert ps["name"] == "Pipestone Star"

    def test_publisher_has_market_and_state(self):
        """Seeded publishers should have market and state."""
        from src.modules.publishers import seed_publishers, get_publisher_by_slug

        seed_publishers()

        ccc = get_publisher_by_slug("cottonwood-county-citizen")
        assert ccc["market"] == "Windom, MN"
        assert ccc["state"] == "MN"


class TestEditionUpload:
    """Tests for edition upload record creation."""

    def test_upload_creates_edition_record(self):
        """Uploading an edition should create an edition record in the DB."""
        from src.modules.publishers import insert_publisher
        from src.modules.publishers.uploads import upload_edition

        pub_id = insert_publisher("Test Publisher", market="Test, MN")
        fake_pdf = _unique_pdf("upload-test")

        result = upload_edition(
            publisher_id=pub_id,
            data=fake_pdf,
            filename="test_edition.pdf",
            edition_date="2026-03-24",
        )

        assert result["error"] is None
        assert result["edition_id"] is not None
        assert result["upload_status"] == "uploaded"
        assert result["extraction_status"] == "not_started"
        assert result["homepage_status"] == "not_started"

    def test_upload_stores_file(self):
        """Uploaded file should be stored in the tenant directory."""
        from src.modules.publishers import insert_publisher
        from src.modules.publishers.uploads import upload_edition

        pub_id = insert_publisher("File Test Publisher", market="Test, MN")
        fake_pdf = _unique_pdf("file-storage")

        result = upload_edition(
            publisher_id=pub_id,
            data=fake_pdf,
            filename="store_test.pdf",
        )

        assert result["file_path"] is not None
        assert Path(result["file_path"]).exists()

        # Cleanup
        Path(result["file_path"]).unlink(missing_ok=True)

    def test_upload_sets_checksum(self):
        """Edition record should have a checksum."""
        from src.modules.publishers import insert_publisher
        from src.modules.publishers.uploads import upload_edition, compute_checksum
        from src.modules.editions import get_edition

        pub_id = insert_publisher("Checksum Publisher", market="Test, MN")
        fake_pdf = _unique_pdf("checksum")

        result = upload_edition(
            publisher_id=pub_id,
            data=fake_pdf,
            filename="checksum_test.pdf",
        )

        edition = get_edition(result["edition_id"])
        assert edition is not None
        assert edition["checksum"] == compute_checksum(fake_pdf)

    def test_invalid_publisher_returns_error(self):
        """Uploading to a non-existent publisher should return an error."""
        from src.modules.publishers.uploads import upload_edition

        result = upload_edition(
            publisher_id=99999,
            data=b"fake pdf",
            filename="bad_publisher.pdf",
        )

        assert result["error"] is not None
        assert "not found" in result["error"]


class TestTenantPaths:
    """Tests for tenant-safe upload paths."""

    def test_upload_dir_uses_publisher_slug(self):
        """Upload directory should be based on publisher slug."""
        from src.modules.publishers.uploads import get_publisher_upload_dir

        upload_dir = get_publisher_upload_dir("cottonwood-county-citizen")
        assert "cottonwood-county-citizen" in str(upload_dir)
        assert upload_dir.exists()

    def test_different_publishers_get_different_dirs(self):
        """Each publisher should have its own upload directory."""
        from src.modules.publishers.uploads import get_publisher_upload_dir

        dir1 = get_publisher_upload_dir("cottonwood-county-citizen")
        dir2 = get_publisher_upload_dir("pipestone-star")

        assert dir1 != dir2
        assert "cottonwood-county-citizen" in str(dir1)
        assert "pipestone-star" in str(dir2)

    def test_upload_dir_is_under_publisher_editions(self):
        """Upload directory should be under data/publisher_editions/."""
        from src.modules.publishers.uploads import get_publisher_upload_dir, UPLOADS_BASE

        upload_dir = get_publisher_upload_dir("test-publisher")
        assert str(UPLOADS_BASE) in str(upload_dir)


class TestDuplicateHandling:
    """Tests for duplicate upload detection."""

    def test_duplicate_upload_rejected(self):
        """Uploading the same file twice should be rejected as duplicate."""
        from src.modules.publishers import insert_publisher
        from src.modules.publishers.uploads import upload_edition

        pub_id = insert_publisher("Dedup Publisher", market="Test, MN")
        fake_pdf = _unique_pdf("dedup")

        result1 = upload_edition(
            publisher_id=pub_id,
            data=fake_pdf,
            filename="dup_test.pdf",
        )
        assert result1["error"] is None
        assert result1["edition_id"] is not None

        result2 = upload_edition(
            publisher_id=pub_id,
            data=fake_pdf,
            filename="dup_test.pdf",
        )
        assert result2["duplicate"] is True
        assert result2["error"] is not None
        assert "Duplicate" in result2["error"]

    def test_different_content_not_duplicate(self):
        """Different files should not be flagged as duplicates."""
        from src.modules.publishers import insert_publisher
        from src.modules.publishers.uploads import upload_edition

        pub_id = insert_publisher("No Dedup Publisher", market="Test, MN")

        result1 = upload_edition(
            publisher_id=pub_id,
            data=_unique_pdf("content-A"),
            filename="file_a.pdf",
        )
        result2 = upload_edition(
            publisher_id=pub_id,
            data=_unique_pdf("content-B"),
            filename="file_b.pdf",
        )

        assert result1["error"] is None
        assert result2["error"] is None
        assert result1["edition_id"] != result2["edition_id"]

    def test_legacy_incomplete_row_repaired_on_duplicate(self):
        """A duplicate matching an incomplete legacy row should repair it."""
        from src.core.database import get_connection
        from src.modules.publishers import insert_publisher
        from src.modules.publishers.uploads import upload_edition, compute_checksum

        pub_id = insert_publisher("Repair Publisher", market="Test, MN")
        fake_pdf = _unique_pdf("legacy-repair")
        checksum = compute_checksum(fake_pdf)

        # Simulate a legacy incomplete row (no publisher_id, no pdf_path)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO editions
            (source_filename, checksum, upload_status)
            VALUES (?, ?, 'pending')""",
            ("legacy_file.pdf", checksum),
        )
        legacy_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Upload the same file — should repair the legacy row
        result = upload_edition(
            publisher_id=pub_id,
            data=fake_pdf,
            filename="legacy_file.pdf",
        )

        assert result["duplicate"] is True
        assert result["edition_id"] == legacy_id
        assert result["upload_status"] == "uploaded"
        assert result.get("error") is None  # No error — repaired, not rejected

        # Verify the DB row is now complete
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM editions WHERE id = ?", (legacy_id,))
        row = dict(cursor.fetchone())
        conn.close()

        assert row["publisher_id"] == pub_id
        assert row["pdf_path"] is not None
        assert row["upload_status"] == "uploaded"
