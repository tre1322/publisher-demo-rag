"""Ad PDF ingestion pipeline with checksum-based deduplication.

Track 1: Publishers upload individual ad PDFs. Each ad is extracted,
stored, and indexed for chatbot retrieval.
"""

import hashlib
import io
import logging
import re
import uuid
from pathlib import Path

import fitz
from PIL import Image
from sentence_transformers import SentenceTransformer

from src.core.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_MODEL,
)
from src.core.vector_store import get_ads_collection
from src.ad_processing import (
    MIN_TEXT_LENGTH,
    categorize_ad,
    enrich_ad_text,
    extract_business_name_from_image,
    extract_business_name_from_image_bytes,
    extract_location,
    is_image_file,
    ocr_image_bytes,
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


ADS_DIR = Path("data/ads")


def _save_ad_files(ad_id: str, data: bytes, filename: str) -> tuple[str, str, str]:
    """Save original ad file + generate web-optimized JPEG.

    Returns:
        Tuple of (file_path, web_image_path, file_type).
    """
    ADS_DIR.mkdir(parents=True, exist_ok=True)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
    file_type = ext

    # Save original
    original_path = ADS_DIR / f"{ad_id}.{ext}"
    original_path.write_bytes(data)

    # Generate web-optimized JPEG
    web_path = ADS_DIR / f"{ad_id}_web.jpg"
    max_width = 1200

    try:
        if ext == "pdf":
            # Render PDF page 1 to image
            doc = fitz.open(stream=data, filetype="pdf")
            page = doc[0]
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            doc.close()
        else:
            # Open image directly
            img = Image.open(io.BytesIO(data)).convert("RGB")

        # Resize to max width, maintain aspect ratio
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

        img.save(str(web_path), "JPEG", quality=85)
        logger.info(f"Saved ad files: {original_path} + {web_path} ({img.width}x{img.height})")

    except Exception as e:
        logger.error(f"Failed to generate web image for {filename}: {e}")
        web_path = None

    return (
        str(original_path),
        str(web_path) if web_path and web_path.exists() else "",
        file_type,
    )


def _extract_business_info(text: str, filename: str) -> dict:
    """Extract basic business info (name, address, phone) from ad text.

    Used to auto-populate the business directory from uploaded ads.

    Returns:
        Dict with name, address, city, state, phone (any may be empty).
    """
    info = {"name": "", "address": "", "city": "", "state": "", "phone": ""}

    # Phone: 507-XXX-XXXX or (507) XXX-XXXX patterns
    phone_match = re.search(r"[\(]?(\d{3})[\)\-\.\s]+(\d{3})[\-\.\s]+(\d{4})", text)
    if phone_match:
        info["phone"] = f"{phone_match.group(1)}-{phone_match.group(2)}-{phone_match.group(3)}"

    # Address: look for street number + street name (stop at newline)
    addr_match = re.search(
        r"(\d+\s+(?:\d+\w*\s+)?(?:St\.?|Ave\.?|Rd\.?|Dr\.?|Blvd\.?|Hwy\.?|Street|Avenue|Road|Drive|Highway|Lane|Ln\.?|Way|Circle|Ct\.?)[^\n]*)",
        text, re.IGNORECASE,
    )
    if addr_match:
        info["address"] = addr_match.group(1).strip().rstrip(",. ")

    # City + State: "City, MN" or "MOUNTAIN LAKE" near a state abbreviation
    city_match = re.search(
        r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s*,?\s*(MN|Minnesota|SD|South Dakota|IA|Iowa)\b",
        text,
    )
    if city_match:
        info["city"] = city_match.group(1).strip()
        state = city_match.group(2)
        info["state"] = {"Minnesota": "MN", "South Dakota": "SD", "Iowa": "IA"}.get(state, state)
    else:
        # Fallback: check for known MN cities in the text
        mn_cities = ["windom", "pipestone", "mountain lake", "mt. lake", "slayton",
                     "jackson", "worthington", "marshall", "redwood falls", "lamberton",
                     "westbrook", "storden", "jeffers", "bingham lake", "butterfield"]
        text_lower = text.lower()
        for city in mn_cities:
            if city in text_lower:
                info["city"] = city.title()
                info["state"] = "MN"
                break

    # Business name: infer from filename if clean
    fname_clean = filename.rsplit(".", 1)[0] if "." in filename else filename
    # Remove common suffixes: size codes (2x5, 3x10, 3x3½), HW (help wanted), RN, etc.
    fname_clean = re.sub(r"\s*\d+x\d+[½¼¾]?\s*$", "", fname_clean).strip()
    fname_clean = re.sub(r"\s+(HW|hw|RN|rn|FP|fp|BW|bw)\s*$", "", fname_clean).strip()
    # Remove trailing fractions
    fname_clean = re.sub(r"\s*[½¼¾]\s*$", "", fname_clean).strip()
    # Remove job title suffixes — the business name is what comes before the position
    job_titles = (
        r"\s+(Executive Director|Director|Manager|Coordinator|Supervisor|"
        r"Technician|Specialist|Assistant|Clerk|Driver|Operator|"
        r"Food Service Manager|Maintenance|Custodian|Teacher|"
        r"Nurse|CNA|LPN|RN|Aide|Cook|Housekeeper)\s*$"
    )
    fname_clean = re.sub(job_titles, "", fname_clean, flags=re.IGNORECASE).strip()
    if fname_clean and len(fname_clean) > 3 and not fname_clean[0].isdigit():
        info["name"] = fname_clean

    return info


def _upsert_directory_entry(
    name: str,
    biz_info: dict,
    publisher: str | None = None,
    ad_category: str | None = None,
) -> None:
    """Create or update a business directory entry from ad data.

    If the business already exists (by slug), just updates last_advertised_at.
    If new, creates the entry with extracted info and sets enrichment_status='pending'.
    """
    if not name or name in ("Unknown", ""):
        return

    from src.core.database import get_connection

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        return

    conn = get_connection()
    cursor = conn.cursor()

    # Check if exists
    cursor.execute("SELECT id FROM organizations WHERE slug = ?", (slug,))
    existing = cursor.fetchone()

    if existing:
        # Update last_advertised_at
        cursor.execute(
            "UPDATE organizations SET last_advertised_at = datetime('now'), updated_at = datetime('now') WHERE slug = ?",
            (slug,),
        )
    else:
        # Create new directory entry
        cursor.execute(
            """INSERT INTO organizations (name, slug, address, city, state, phone, category, publisher,
               enrichment_status, last_advertised_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'), datetime('now'), datetime('now'))""",
            (
                name,
                slug,
                biz_info.get("address", ""),
                biz_info.get("city", ""),
                biz_info.get("state", ""),
                biz_info.get("phone", ""),
                ad_category or "",
                publisher or "",
            ),
        )
        logger.info(f"New business directory entry: {name} (slug={slug})")

    conn.commit()
    conn.close()


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


def _looks_like_business_name(line: str) -> bool:
    """Check if a line looks like a business name (not a date, price, phone, etc.)."""
    import re

    line_lower = line.lower().strip()
    # Skip dates, times, phone numbers, prices, short fragments
    if re.match(r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", line_lower):
        return False
    if re.match(r"^(january|february|march|april|may|june|july|august|september|october|november|december)", line_lower):
        return False
    if re.match(r"^\d{1,2}[/\-\.]\d{1,2}", line_lower):  # Date patterns
        return False
    if re.match(r"^\$?\d+[\.,]", line_lower):  # Prices
        return False
    if re.match(r"^\(?\d{3}\)?[\s\-\.]?\d{3}", line_lower):  # Phone numbers
        return False
    if len(line) < 3:  # Too short
        return False
    if not any(c.isalpha() for c in line):  # Must contain letters
        return False
    return True


_GENERIC_FILENAMES = {
    "upload", "ad", "ads", "scan", "image", "document", "doc", "file",
    "untitled", "unknown", "new", "temp", "tmp",
}


def _clean_filename_as_name(filename: str) -> str:
    """Extract a business name from a filename by removing dimensions/noise."""
    import re

    stem = Path(filename).stem
    # Remove dimension patterns like "3x6", "4x5", "half page", trailing "ad", etc.
    stem = re.sub(r"\s*\d+x\d+\s*", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s*(half|quarter|full)\s*page\s*", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s+ad$", "", stem, flags=re.IGNORECASE)
    stem = stem.replace("_", " ").replace("-", " ").strip()
    # Collapse multiple spaces
    stem = re.sub(r"\s+", " ", stem)
    return stem.title() if stem else "Unknown"


def _is_generic_filename(filename: str) -> bool:
    """Check if a filename is generic/uninformative (not a business name)."""
    import re

    stem = Path(filename).stem.lower()
    # Remove dimensions and noise first
    stem = re.sub(r"\d+x\d+", "", stem)
    stem = re.sub(r"[_\-\.\s]+", " ", stem).strip()

    # Check against known generic names
    if stem in _GENERIC_FILENAMES:
        return True
    # Purely numeric filenames
    if re.match(r"^\d+$", stem):
        return True
    # Very short (1-2 chars) or looks like "ad_001", "page5"
    if len(stem) <= 2:
        return True
    if re.match(r"^(ad|page|img|scan|doc)\s*\d+$", stem):
        return True
    return False


def infer_advertiser_name(
    text: str, filename: str, pdf_bytes: bytes | None = None
) -> str:
    """Infer the advertiser/business name from filename, text, or ad image.

    Strategy:
    1. If filename contains a clear business name → use it (free, instant)
    2. If filename is generic → use Claude Vision to read the business name
       from the ad image (logos, headers, branding)
    3. Scan extracted text for a line that looks like a business name
    4. Fall back to cleaned filename
    """
    # Try filename first — uploaders often name files after the business
    cleaned_filename = _clean_filename_as_name(filename)
    if not _is_generic_filename(filename) and len(cleaned_filename) >= 3:
        logger.info(f"Advertiser name from filename: '{cleaned_filename}'")
        return cleaned_filename

    # Filename is generic — try Claude Vision to read the business name
    if pdf_bytes:
        vision_name = extract_business_name_from_image(pdf_bytes, filename)
        if vision_name:
            logger.info(f"Advertiser name from Vision API: '{vision_name}'")
            return vision_name

    # Scan text for a line that looks like a business name
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) < 100 and _looks_like_business_name(line):
            logger.info(f"Advertiser name from text scan: '{line}'")
            return line

    logger.info(f"Advertiser name fallback to filename: '{cleaned_filename}'")
    return cleaned_filename


class AdIngester:
    """Ingests individual ad PDFs into the database and vector index."""

    def __init__(self) -> None:
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        self.collection = get_ads_collection()
        logger.info(
            f"AdIngester initialized: ads collection "
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

        # Read PDF bytes for potential Vision-based name extraction
        try:
            _pdf_bytes = pdf_path.read_bytes()
        except Exception:
            _pdf_bytes = None
        advertiser = infer_advertiser_name(best_text, pdf_path.name, pdf_bytes=_pdf_bytes)

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
        ad_type: str | None = None,
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

        advertiser = infer_advertiser_name(best_text, filename, pdf_bytes=data)

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

        # Save original file + generate web-optimized image
        file_path, web_image_path, file_type = _save_ad_files(ad_id, data, filename)

        # Extract business info for directory
        biz_info = _extract_business_info(ocr_text or text, filename)

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
            ad_type=ad_type,
            file_path=file_path,
            web_image_path=web_image_path,
            file_type=file_type,
        )

        # Auto-add to business directory if we got a name
        _upsert_directory_entry(
            name=advertiser,
            biz_info=biz_info,
            publisher=publisher,
            ad_category=ad_category,
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
                        "publisher": publisher or "",
                        "url": f"/ad/{ad_id}",
                        "ad_type": ad_type or "",
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

    def ingest_ad_image_bytes(
        self,
        data: bytes,
        filename: str,
        organization_id: int | None = None,
        publication_id: int | None = None,
        publisher: str | None = None,
        ad_type: str | None = None,
    ) -> dict:
        """Ingest an ad from a raw image file (PNG, JPG, etc.) via Vision API.

        Uses Claude Vision to extract all text from the ad image,
        then follows the same enrichment and indexing pipeline as PDFs.

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

        # Extract text via Claude Vision (images have no extractable text layer)
        logger.info(f"Extracting text from image ad: {filename}")
        ocr_text = ocr_image_bytes(data, filename)

        if not ocr_text.strip():
            result["error"] = "No text extracted from image ad"
            return result

        # Infer advertiser name — try filename first, then Vision name extraction
        advertiser = infer_advertiser_name(ocr_text, filename)
        # If filename was generic and text scan didn't find a good name,
        # try dedicated Vision name extraction
        if advertiser in ("Unknown", "") or advertiser == _clean_filename_as_name(filename):
            vision_name = extract_business_name_from_image_bytes(data, filename)
            if vision_name:
                advertiser = vision_name

        # Categorize, locate, and enrich
        ad_category = categorize_ad(ocr_text, advertiser)
        location = extract_location(ocr_text)
        embedding_text = enrich_ad_text(
            advertiser=advertiser,
            raw_text="",
            ocr_text=ocr_text,
            category=ad_category,
            location=location,
        )

        logger.info(
            f"Image ad processed: advertiser='{advertiser}', "
            f"category={ad_category}, location='{location}', "
            f"ocr_text_len={len(ocr_text)}"
        )

        ad_id = str(uuid.uuid4())

        # Save original file + generate web-optimized image
        file_path, web_image_path, file_type = _save_ad_files(ad_id, data, filename)

        # Extract business info for directory
        biz_info = _extract_business_info(ocr_text, filename)

        insert_edition_advertisement(
            ad_id=ad_id,
            advertiser_name=advertiser,
            extracted_text="",
            organization_id=organization_id,
            publication_id=publication_id,
            publisher=publisher,
            checksum=checksum,
            source_filename=filename,
            ocr_text=ocr_text,
            embedding_text=embedding_text,
            ad_category=ad_category,
            location=location,
            ad_type=ad_type,
            file_path=file_path,
            web_image_path=web_image_path,
            file_type=file_type,
        )

        # Auto-add to business directory if we got a name
        _upsert_directory_entry(
            name=advertiser,
            biz_info=biz_info,
            publisher=publisher,
            ad_category=ad_category,
        )

        result["ad_id"] = ad_id
        result["ocr_used"] = True
        result["ad_category"] = ad_category

        # Index enriched text into Chroma
        chunks = self.chunk_text(embedding_text, advertiser=advertiser)
        if chunks:
            try:
                logger.info(
                    f"Indexing image ad '{advertiser}' ({filename}): "
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
                        "publisher": publisher or "",
                        "url": f"/ad/{ad_id}",
                        "ad_type": ad_type or "",
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
                    f"Indexing complete for image ad '{advertiser}' ({filename})"
                )
            except Exception as e:
                logger.error(
                    f"Vector indexing failed for image ad '{advertiser}' "
                    f"({filename}): {e}. DB record was saved."
                )
                result["warning"] = f"Ad saved but vector indexing failed: {e}"

        logger.info(f"Image ad ingested: {advertiser} ({filename})")
        return result
