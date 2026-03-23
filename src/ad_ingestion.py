"""Ad PDF ingestion pipeline with checksum-based deduplication.

Track 1: Publishers upload individual ad PDFs. Each ad is extracted,
stored, and indexed for chatbot retrieval.
"""

import hashlib
import logging
import uuid
from pathlib import Path

import chromadb
import fitz
from sentence_transformers import SentenceTransformer

from src.core.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHROMA_PERSIST_DIR,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
)
from src.ad_processing import (
    MIN_TEXT_LENGTH,
    categorize_ad,
    enrich_ad_text,
    extract_location,
    ocr_pdf_bytes,
    ocr_pdf_file,
)
from src.modules.advertisements import get_ad_by_checksum, insert_edition_advertisement

logger = logging.getLogger(__name__)


def compute_file_checksum(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_bytes_checksum(data: bytes) -> str:
    """Compute SHA-256 checksum of bytes."""
    return hashlib.sha256(data).hexdigest()


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from a PDF using PyMuPDF."""
    try:
        doc = fitz.open(str(pdf_path))
        text_parts = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                text_parts.append(text.strip())
        doc.close()
        return "\n\n".join(text_parts)
    except Exception as e:
        logger.error(f"Failed to extract text from {pdf_path}: {e}")
        return ""


def extract_text_from_bytes(data: bytes, filename: str = "upload.pdf") -> str:
    """Extract text from PDF bytes."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        text_parts = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                text_parts.append(text.strip())
        doc.close()
        return "\n\n".join(text_parts)
    except Exception as e:
        logger.error(f"Failed to extract text from {filename}: {e}")
        return ""


def infer_advertiser_name(text: str, filename: str) -> str:
    """Try to infer the advertiser name from text or filename."""
    # First non-empty line is often the business name
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) < 100:
            return line
    # Fall back to filename
    return Path(filename).stem.replace("_", " ").replace("-", " ").title()


class AdIngester:
    """Ingests individual ad PDFs into the database and vector index."""

    def __init__(self) -> None:
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
        self.collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"AdIngester initialized: collection '{COLLECTION_NAME}' "
            f"with {self.collection.count()} existing chunks"
        )

    def chunk_text(self, text: str, advertiser: str = "") -> list[str]:
        """Chunk text into overlapping windows, prepending advertiser context."""
        # Prepend advertiser context so each chunk is self-describing
        if advertiser:
            prefix = f"{advertiser} advertisement: "
        else:
            prefix = ""

        words = text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = start + CHUNK_SIZE
            chunk = prefix + " ".join(words[start:end])
            chunks.append(chunk)
            if end >= len(words):
                break
            start = end - CHUNK_OVERLAP
        return chunks

    def ingest_ad_pdf(
        self,
        pdf_path: Path,
        organization_id: int | None = None,
        publication_id: int | None = None,
        publisher: str | None = None,
    ) -> dict:
        """Ingest a single ad PDF file.

        Returns:
            Dict with result info including ad_id, or error.
        """
        result = {
            "filename": pdf_path.name,
            "ad_id": None,
            "error": None,
            "duplicate": False,
        }

        # Compute checksum
        checksum = compute_file_checksum(pdf_path)

        # Check for duplicate
        existing = get_ad_by_checksum(checksum)
        if existing:
            result["error"] = "Duplicate ad (checksum match)"
            result["duplicate"] = True
            result["ad_id"] = existing["ad_id"]
            logger.info(f"Duplicate ad rejected: {pdf_path.name}")
            return result

        # Extract text, with OCR fallback for image-based PDFs
        text = extract_text_from_pdf(pdf_path)
        ocr_text = ""
        used_ocr = False

        if len(text.strip()) < MIN_TEXT_LENGTH:
            logger.info(
                f"Text extraction short ({len(text.strip())} chars) for {pdf_path.name}, "
                "attempting OCR fallback"
            )
            ocr_text = ocr_pdf_file(str(pdf_path))
            used_ocr = bool(ocr_text)

        best_text = ocr_text or text
        if not best_text.strip():
            result["error"] = "No text extracted from PDF (even after OCR)"
            logger.warning(f"No text from ad PDF: {pdf_path.name}")
            return result

        advertiser = infer_advertiser_name(best_text, pdf_path.name)

        # Categorize, locate, and enrich
        ad_category = categorize_ad(best_text, advertiser)
        location = extract_location(best_text)
        embedding_text = enrich_ad_text(
            advertiser=advertiser,
            raw_text=text,
            ocr_text=ocr_text,
            category=ad_category,
            location=location,
        )

        logger.info(
            f"Ad processed: advertiser='{advertiser}', category={ad_category}, "
            f"location='{location}', ocr={used_ocr}"
        )

        ad_id = str(uuid.uuid4())
        insert_edition_advertisement(
            ad_id=ad_id,
            advertiser_name=advertiser,
            extracted_text=text,
            organization_id=organization_id,
            publication_id=publication_id,
            publisher=publisher,
            checksum=checksum,
            source_filename=pdf_path.name,
            ocr_text=ocr_text or None,
            embedding_text=embedding_text,
            ad_category=ad_category,
            location=location,
        )

        result["ad_id"] = ad_id
        result["ocr_used"] = used_ocr
        result["ad_category"] = ad_category

        # Use enriched text for chunking/embedding
        chunks = self.chunk_text(embedding_text, advertiser=advertiser)
        if chunks:
            try:
                logger.info(
                    f"Indexing ad '{advertiser}' ({pdf_path.name}): "
                    f"{len(chunks)} chunks"
                )
                embeddings = self.embedding_model.encode(chunks).tolist()
                ids = [f"{ad_id}_{i}" for i in range(len(chunks))]
                metadatas = [
                    {
                        "doc_id": ad_id,
                        "title": advertiser[:200],
                        "publish_date": "",
                        "author": advertiser,
                        "source_file": pdf_path.name,
                        "chunk_index": i,
                        "location": location,
                        "subjects": ad_category,
                        "content_type": "advertisement",
                    }
                    for i in range(len(chunks))
                ]
                self.collection.add(
                    ids=ids,
                    embeddings=embeddings,
                    documents=chunks,
                    metadatas=metadatas,
                )
                logger.info(
                    f"Indexing complete for ad '{advertiser}' ({pdf_path.name})"
                )
            except Exception as e:
                logger.error(
                    f"Vector indexing failed for ad '{advertiser}' "
                    f"({pdf_path.name}): {e}. DB record was saved."
                )
                result["warning"] = f"Ad saved but vector indexing failed: {e}"

        logger.info(f"Ad ingested: {advertiser} ({pdf_path.name})")
        return result

    def ingest_ad_bytes(
        self,
        data: bytes,
        filename: str,
        organization_id: int | None = None,
        publication_id: int | None = None,
        publisher: str | None = None,
    ) -> dict:
        """Ingest an ad from raw PDF bytes (for web uploads).

        Returns:
            Dict with result info.
        """
        result = {
            "filename": filename,
            "ad_id": None,
            "error": None,
            "duplicate": False,
        }

        checksum = compute_bytes_checksum(data)

        existing = get_ad_by_checksum(checksum)
        if existing:
            result["error"] = "Duplicate ad (checksum match)"
            result["duplicate"] = True
            result["ad_id"] = existing["ad_id"]
            return result

        # Extract text, with OCR fallback for image-based PDFs
        text = extract_text_from_bytes(data, filename)
        ocr_text = ""
        used_ocr = False

        if len(text.strip()) < MIN_TEXT_LENGTH:
            logger.info(
                f"Text extraction short ({len(text.strip())} chars) for {filename}, "
                "attempting OCR fallback"
            )
            ocr_text = ocr_pdf_bytes(data, filename)
            used_ocr = bool(ocr_text)
            if used_ocr:
                logger.info(f"OCR fallback produced {len(ocr_text)} chars for {filename}")

        # Use best available text for advertiser inference
        best_text = ocr_text or text
        if not best_text.strip():
            result["error"] = "No text extracted from PDF (even after OCR)"
            return result

        advertiser = infer_advertiser_name(best_text, filename)

        # Categorize, locate, and enrich
        ad_category = categorize_ad(best_text, advertiser)
        location = extract_location(best_text)
        embedding_text = enrich_ad_text(
            advertiser=advertiser,
            raw_text=text,
            ocr_text=ocr_text,
            category=ad_category,
            location=location,
        )

        logger.info(
            f"Ad processed: advertiser='{advertiser}', category={ad_category}, "
            f"location='{location}', ocr={used_ocr}, "
            f"embedding_text_len={len(embedding_text)}"
        )

        ad_id = str(uuid.uuid4())
        insert_edition_advertisement(
            ad_id=ad_id,
            advertiser_name=advertiser,
            extracted_text=text,
            organization_id=organization_id,
            publication_id=publication_id,
            publisher=publisher,
            checksum=checksum,
            source_filename=filename,
            ocr_text=ocr_text or None,
            embedding_text=embedding_text,
            ad_category=ad_category,
            location=location,
        )

        result["ad_id"] = ad_id
        result["ocr_used"] = used_ocr
        result["ad_category"] = ad_category

        # Use enriched text for chunking/embedding
        chunks = self.chunk_text(embedding_text, advertiser=advertiser)
        if chunks:
            try:
                logger.info(
                    f"Indexing uploaded ad '{advertiser}' ({filename}): "
                    f"{len(chunks)} chunks"
                )
                embeddings = self.embedding_model.encode(chunks).tolist()
                ids = [f"{ad_id}_{i}" for i in range(len(chunks))]
                metadatas = [
                    {
                        "doc_id": ad_id,
                        "title": advertiser[:200],
                        "publish_date": "",
                        "author": advertiser,
                        "source_file": filename,
                        "chunk_index": i,
                        "location": location,
                        "subjects": ad_category,
                        "content_type": "advertisement",
                    }
                    for i in range(len(chunks))
                ]
                self.collection.add(
                    ids=ids,
                    embeddings=embeddings,
                    documents=chunks,
                    metadatas=metadatas,
                )
                logger.info(
                    f"Indexing complete for uploaded ad '{advertiser}' ({filename})"
                )
            except Exception as e:
                logger.error(
                    f"Vector indexing failed for uploaded ad '{advertiser}' "
                    f"({filename}): {e}. DB record was saved."
                )
                result["warning"] = f"Ad saved but vector indexing failed: {e}"

        logger.info(f"Ad ingested from upload: {advertiser} ({filename})")
        return result
