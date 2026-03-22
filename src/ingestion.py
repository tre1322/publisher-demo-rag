"""Document ingestion and indexing for the Publisher RAG Demo."""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import chromadb
import pdfplumber
from sentence_transformers import SentenceTransformer
from striprtf.striprtf import rtf_to_text

from src.core.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHROMA_PERSIST_DIR,
    COLLECTION_NAME,
    DOCUMENTS_DIR,
    EMBEDDING_MODEL,
    INGESTED_FILES_PATH,
)
from src.modules.articles import insert_article
from src.metadata_extractor import MetadataExtractor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DocumentIngester:
    """Handles document ingestion into ChromaDB."""

    def __init__(
        self, extract_metadata: bool = True, publisher: str | None = None
    ) -> None:
        """Initialize the document ingester.

        Args:
            extract_metadata: Whether to extract rich metadata using Claude.
            publisher: Name of the publishing newspaper.
        """
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
        self.collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self.ingested_files = self._load_ingested_files()
        self.publisher = publisher

        # Initialize metadata extractor if enabled
        self.extract_metadata_enabled = extract_metadata
        if extract_metadata:
            self.metadata_extractor = MetadataExtractor()
            logger.info("Rich metadata extraction enabled")
        else:
            self.metadata_extractor = None

    def _load_ingested_files(self) -> set[str]:
        """Load the set of already ingested files."""
        if INGESTED_FILES_PATH.exists():
            with open(INGESTED_FILES_PATH) as f:
                return set(json.load(f))
        return set()

    def _save_ingested_files(self) -> None:
        """Save the set of ingested files."""
        with open(INGESTED_FILES_PATH, "w") as f:
            json.dump(list(self.ingested_files), f, indent=2)

    def extract_text_from_pdf(self, pdf_path: Path) -> str:
        """Extract text from a PDF file.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Extracted text content.
        """
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)

    def extract_text_from_txt(self, txt_path: Path) -> str:
        """Extract text from a text file.

        Args:
            txt_path: Path to the text file.

        Returns:
            Text content.
        """
        return txt_path.read_text(encoding="utf-8")

    def extract_text_from_rtf(self, rtf_path: Path) -> str:
        """Extract text from an RTF file.

        Args:
            rtf_path: Path to the RTF file.

        Returns:
            Text content.
        """
        rtf_content = rtf_path.read_text(encoding="utf-8", errors="ignore")
        return rtf_to_text(rtf_content)

    def chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks.

        Args:
            text: Text to chunk.

        Returns:
            List of text chunks.
        """
        # Simple word-based chunking
        words = text.split()
        chunks = []

        # Approximate tokens as words (rough estimate)
        chunk_words = CHUNK_SIZE
        overlap_words = CHUNK_OVERLAP

        start = 0
        while start < len(words):
            end = start + chunk_words
            chunk = " ".join(words[start:end])
            chunks.append(chunk)

            if end >= len(words):
                break

            start = end - overlap_words

        return chunks

    def extract_metadata(self, file_path: Path, text: str) -> dict:
        """Extract metadata from document.

        Args:
            file_path: Path to the document.
            text: Document text content.

        Returns:
            Metadata dictionary.
        """
        lines = text.split("\n") if text else []

        # Extract title from first line
        title = file_path.stem.replace("_", " ").replace("-", " ").title()
        if lines and len(lines[0].strip()) < 200 and not lines[0].strip().endswith("."):
            title = lines[0].strip()

        # Default values
        author = "Unknown"
        publish_date = datetime.fromtimestamp(file_path.stat().st_mtime).strftime(
            "%Y-%m-%d"
        )
        url = ""

        # Parse structured metadata from downloaded articles
        for line in lines[1:10]:  # Check first 10 lines
            line = line.strip()
            if line.startswith("Author:"):
                author = line.replace("Author:", "").strip()
            elif line.startswith("Date:"):
                date_str = line.replace("Date:", "").strip()
                if date_str:
                    publish_date = date_str
            elif line.startswith("URL:"):
                url = line.replace("URL:", "").strip()

        return {
            "title": title,
            "publish_date": publish_date,
            "author": author,
            "source_file": file_path.name,
            "url": url,
        }

    def ingest_document(self, file_path: Path) -> int:
        """Ingest a single document into ChromaDB.

        Args:
            file_path: Path to the document.

        Returns:
            Number of chunks ingested.
        """
        if file_path.name in self.ingested_files:
            logger.info(f"Skipping already ingested: {file_path.name}")
            return 0

        # Extract text based on file type
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            text = self.extract_text_from_pdf(file_path)
        elif suffix == ".txt":
            text = self.extract_text_from_txt(file_path)
        elif suffix == ".rtf":
            text = self.extract_text_from_rtf(file_path)
        else:
            logger.warning(f"Unsupported file type: {file_path.suffix}")
            return 0

        if not text.strip():
            logger.warning(f"No text extracted from: {file_path.name}")
            return 0

        # Extract basic metadata
        metadata = self.extract_metadata(file_path, text)
        doc_id = str(uuid.uuid4())

        # Extract rich metadata using Claude if enabled
        location = None
        subjects = None
        summary = None

        if self.metadata_extractor:
            logger.info(f"Extracting rich metadata for: {file_path.name}")
            rich_metadata = self.metadata_extractor.extract(
                title=metadata["title"],
                author=metadata["author"],
                date=metadata["publish_date"],
                content=text,
            )
            location = rich_metadata.get("location")
            subjects = rich_metadata.get("subjects")
            summary = rich_metadata.get("summary")
            logger.info(f"  Location: {location}, Subjects: {subjects}")

        # Store metadata in SQLite database
        insert_article(
            doc_id=doc_id,
            title=metadata["title"],
            author=metadata["author"],
            publish_date=metadata["publish_date"],
            source_file=metadata["source_file"],
            location=location,
            subjects=subjects,
            summary=summary,
            url=metadata.get("url"),
            publisher=self.publisher,
        )

        # Chunk the text
        chunks = self.chunk_text(text)

        if not chunks:
            logger.warning(f"No chunks created for: {file_path.name}")
            return 0

        # Generate embeddings
        embeddings = self.embedding_model.encode(chunks).tolist()

        # Prepare data for ChromaDB (include location and subjects for filtering)
        ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                **metadata,
                "doc_id": doc_id,
                "chunk_index": i,
                "location": location or "Unknown",
                "subjects": ",".join(subjects) if subjects else "",
            }
            for i in range(len(chunks))
        ]

        # Add to collection
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,  # type: ignore[arg-type]
        )

        # Track ingested file
        self.ingested_files.add(file_path.name)
        self._save_ingested_files()

        logger.info(f"Ingested {len(chunks)} chunks from: {file_path.name}")
        return len(chunks)

    def ingest_all(self, directory: Path | None = None) -> dict:
        """Ingest all documents from a directory.

        Args:
            directory: Directory containing documents. Defaults to DOCUMENTS_DIR.

        Returns:
            Summary of ingestion results.
        """
        if directory is None:
            directory = DOCUMENTS_DIR

        results = {
            "total_files": 0,
            "ingested_files": 0,
            "skipped_files": 0,
            "failed_files": 0,
            "total_chunks": 0,
            "errors": [],
        }

        # Get all supported files (including subdirectories)
        files = (
            list(directory.glob("**/*.pdf"))
            + list(directory.glob("**/*.txt"))
            + list(directory.glob("**/*.rtf"))
        )
        results["total_files"] = len(files)

        for file_path in files:
            try:
                chunks = self.ingest_document(file_path)
                if chunks > 0:
                    results["ingested_files"] += 1
                    results["total_chunks"] += chunks
                else:
                    results["skipped_files"] += 1
            except Exception as e:
                logger.error(f"Failed to ingest {file_path.name}: {e}")
                results["failed_files"] += 1
                results["errors"].append(f"{file_path.name}: {str(e)}")

        return results

    def get_collection_stats(self) -> dict:
        """Get statistics about the collection.

        Returns:
            Collection statistics.
        """
        return {
            "total_chunks": self.collection.count(),
            "ingested_files": len(self.ingested_files),
        }


def main() -> None:
    """Run ingestion from command line."""
    ingester = DocumentIngester()
    results = ingester.ingest_all()

    print("\nIngestion Complete!")
    print(f"Total files found: {results['total_files']}")
    print(f"Files ingested: {results['ingested_files']}")
    print(f"Files skipped: {results['skipped_files']}")
    print(f"Files failed: {results['failed_files']}")
    print(f"Total chunks created: {results['total_chunks']}")

    if results["errors"]:
        print("\nErrors:")
        for error in results["errors"]:
            print(f"  - {error}")


if __name__ == "__main__":
    main()
