"""Newspaper edition ingestion pipeline.

Orchestrates: parse PDF → reconstruct articles → separate ads →
store in DB with regions → chunk reconstructed articles → index in ChromaDB.
"""

import hashlib
import json
import logging
import uuid
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from src.core.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHROMA_PERSIST_DIR,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
)
from src.modules.advertisements import insert_edition_advertisement
from src.modules.articles import insert_edition_article
from src.modules.editions import (
    get_edition_by_checksum,
    insert_edition,
    insert_page_region,
    update_edition_status,
)
from src.newspaper_parser import NewspaperParser

logger = logging.getLogger(__name__)


def compute_file_checksum(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class EditionIngester:
    """Ingests newspaper edition PDFs into the database and vector index."""

    def __init__(
        self,
        publisher: str | None = None,
        publication_name: str | None = None,
        organization_id: int | None = None,
        publication_id: int | None = None,
    ) -> None:
        self.publisher = publisher
        self.publication_name = publication_name or publisher
        self.organization_id = organization_id
        self.publication_id = publication_id
        self.parser = NewspaperParser()

        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
        self.collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def chunk_text(self, text: str) -> list[str]:
        words = text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = start + CHUNK_SIZE
            chunk = " ".join(words[start:end])
            chunks.append(chunk)
            if end >= len(words):
                break
            start = end - CHUNK_OVERLAP
        return chunks

    def ingest_edition(
        self,
        pdf_path: Path,
        edition_date: str | None = None,
    ) -> dict:
        """Ingest a single newspaper edition PDF.

        Returns:
            Dict with ingestion results.
        """
        pdf_path = pdf_path.resolve()
        result = {
            "pdf": pdf_path.name,
            "edition_id": None,
            "articles": 0,
            "ads": 0,
            "chunks_indexed": 0,
            "pages": 0,
            "warnings": [],
            "error": None,
        }

        # Checksum-based duplicate detection
        checksum = compute_file_checksum(pdf_path)
        existing = get_edition_by_checksum(checksum, self.publication_id)
        if existing:
            if existing["processing_status"] == "completed":
                logger.info(f"Duplicate edition rejected: {pdf_path.name}")
                result["error"] = "Duplicate edition (checksum match)"
                result["edition_id"] = existing["id"]
                return result
            # Allow re-processing of failed editions
            logger.info(f"Re-processing failed edition: {pdf_path.name}")

        # Create edition record
        edition_id = insert_edition(
            source_filename=pdf_path.name,
            publication_id=self.publication_id,
            edition_date=edition_date,
            checksum=checksum,
        )
        result["edition_id"] = edition_id
        update_edition_status(edition_id, "processing")

        try:
            # Parse the PDF
            parse_result = self.parser.parse(pdf_path)
            result["pages"] = parse_result.page_count
            result["warnings"] = parse_result.warnings

            for w in parse_result.warnings:
                logger.warning(f"  Parser warning: {w}")

            # Store articles
            total_chunks = 0
            for article in parse_result.articles:
                doc_id = str(uuid.uuid4())
                full_text = article.full_text
                cleaned_text = article.cleaned_text

                if not cleaned_text:
                    continue

                # Store article in SQLite
                insert_edition_article(
                    doc_id=doc_id,
                    title=article.headline,
                    edition_id=edition_id,
                    source_file=pdf_path.name,
                    full_text=full_text,
                    cleaned_text=cleaned_text,
                    author=article.byline or None,
                    publish_date=edition_date,
                    section=article.section or None,
                    start_page=article.start_page,
                    continuation_pages=article.continuation_pages or None,
                    publisher=self.publisher,
                    organization_id=self.organization_id,
                    publication_id=self.publication_id,
                    needs_review=True,
                )

                # Store page regions for this article
                for region in article.regions:
                    insert_page_region(
                        edition_id=edition_id,
                        article_id=doc_id,
                        page_number=region.page_num + 1,
                        region_type=region.region_type,
                        bbox_json=json.dumps(region.bbox),
                        raw_text=region.raw_text[:2000],
                        role="article",
                        metadata_json=json.dumps({
                            "headline": region.headline,
                            "byline": region.byline,
                            "forward_jump": region.forward_jump_target,
                            "backward_jump": region.backward_jump_source,
                        }),
                    )

                # Chunk and index in ChromaDB
                chunks = self.chunk_text(cleaned_text)
                if chunks:
                    embeddings = self.embedding_model.encode(chunks).tolist()
                    ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
                    metadatas = [
                        {
                            "doc_id": doc_id,
                            "title": article.headline[:200],
                            "publish_date": edition_date or "",
                            "author": article.byline or "Unknown",
                            "source_file": pdf_path.name,
                            "chunk_index": i,
                            "location": "",
                            "subjects": "",
                            "edition_id": str(edition_id),
                            "content_type": "article",
                        }
                        for i in range(len(chunks))
                    ]
                    self.collection.add(
                        ids=ids,
                        embeddings=embeddings,
                        documents=chunks,
                        metadatas=metadatas,
                    )
                    total_chunks += len(chunks)

                result["articles"] += 1

            # Store advertisements
            for ad in parse_result.advertisements:
                ad_id = str(uuid.uuid4())

                insert_edition_advertisement(
                    ad_id=ad_id,
                    advertiser_name=ad.advertiser_name,
                    extracted_text=ad.text,
                    edition_id=edition_id,
                    page=ad.page_num,
                    publisher=self.publisher,
                    organization_id=self.organization_id,
                    publication_id=self.publication_id,
                )

                # Store ad region
                if ad.region:
                    insert_page_region(
                        edition_id=edition_id,
                        page_number=ad.page_num,
                        region_type="advertisement",
                        bbox_json=json.dumps(ad.region.bbox),
                        raw_text=ad.text[:2000],
                        role="advertisement",
                    )

                result["ads"] += 1

            result["chunks_indexed"] = total_chunks

            update_edition_status(
                edition_id,
                status="completed",
                article_count=result["articles"],
                ad_count=result["ads"],
                page_count=parse_result.page_count,
            )

            logger.info(
                f"Edition ingested: {result['articles']} articles, "
                f"{result['ads']} ads, {total_chunks} chunks indexed"
            )

        except Exception as e:
            logger.error(f"Edition ingestion failed: {e}")
            update_edition_status(edition_id, "failed", error=str(e))
            result["error"] = str(e)

        return result

    def ingest_bulk(
        self,
        pdf_paths: list[Path],
        edition_date: str | None = None,
    ) -> list[dict]:
        """Ingest multiple newspaper PDFs."""
        results = []
        for i, pdf_path in enumerate(pdf_paths, 1):
            logger.info(f"Processing {i}/{len(pdf_paths)}: {pdf_path.name}")
            try:
                result = self.ingest_edition(pdf_path, edition_date)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to process {pdf_path.name}: {e}")
                results.append({
                    "pdf": pdf_path.name,
                    "error": str(e),
                    "articles": 0,
                    "ads": 0,
                    "chunks_indexed": 0,
                })
        return results
