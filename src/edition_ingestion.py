"""Newspaper edition ingestion pipeline.

Orchestrates: parse PDF → reconstruct articles → separate ads →
store in DB → chunk reconstructed articles → index in ChromaDB.
"""

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
    get_edition_by_pdf_path,
    insert_edition,
    update_edition_status,
)
from src.newspaper_parser import NewspaperParser

logger = logging.getLogger(__name__)


class EditionIngester:
    """Ingests newspaper edition PDFs into the database and vector index."""

    def __init__(self, publisher: str, publication_name: str | None = None) -> None:
        self.publisher = publisher
        self.publication_name = publication_name or publisher
        self.parser = NewspaperParser()

        # Reuse existing ChromaDB collection and embedding model
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
        self.collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks (reuses existing logic)."""
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

        Args:
            pdf_path: Path to the newspaper PDF.
            edition_date: Publication date (YYYY-MM-DD), optional.

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

        # Check for duplicate
        existing = get_edition_by_pdf_path(str(pdf_path))
        if existing:
            if existing["processing_status"] == "completed":
                logger.info(f"Skipping already ingested edition: {pdf_path.name}")
                result["error"] = "Already ingested"
                result["edition_id"] = existing["id"]
                return result
            else:
                # Allow re-processing of failed editions
                logger.info(f"Re-processing previously failed edition: {pdf_path.name}")

        # Create edition record
        edition_id = insert_edition(
            publisher=self.publisher,
            source_pdf_path=str(pdf_path),
            publication_name=self.publication_name,
            edition_date=edition_date,
        )
        result["edition_id"] = edition_id
        update_edition_status(edition_id, "processing")

        try:
            # Parse the PDF
            parse_result = self.parser.parse(pdf_path)
            result["pages"] = parse_result.page_count
            result["warnings"] = parse_result.warnings

            if parse_result.warnings:
                for w in parse_result.warnings:
                    logger.warning(f"  Parser warning: {w}")

            # Store articles
            total_chunks = 0
            for article in parse_result.articles:
                doc_id = str(uuid.uuid4())
                cleaned_text = article.cleaned_text

                if not cleaned_text:
                    continue

                # Store article in SQLite
                insert_edition_article(
                    doc_id=doc_id,
                    title=article.headline,
                    edition_id=edition_id,
                    source_file=pdf_path.name,
                    full_text=cleaned_text,
                    author=article.byline or None,
                    publish_date=edition_date,
                    section=article.section or None,
                    start_page=article.start_page,
                    continuation_pages=article.continuation_pages or None,
                    publisher=self.publisher,
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
                )
                result["ads"] += 1

            result["chunks_indexed"] = total_chunks

            # Update edition status
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
        """Ingest multiple newspaper PDFs.

        Args:
            pdf_paths: List of PDF file paths.
            edition_date: Shared edition date (optional).

        Returns:
            List of per-file result dicts.
        """
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
