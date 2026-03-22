#!/usr/bin/env python
"""Upload pre-ingested databases to DigitalOcean Spaces."""

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import CHROMA_PERSIST_DIR


def get_s3_client():
    """Create S3 client configured for DigitalOcean Spaces.

    Returns:
        boto3 S3 client.
    """
    region = os.getenv("SPACES_REGION", "nyc3")
    endpoint_url = f"https://{region}.digitaloceanspaces.com"

    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint_url,
        aws_access_key_id=os.getenv("SPACES_KEY"),
        aws_secret_access_key=os.getenv("SPACES_SECRET"),
    )


def upload_directory(s3_client, bucket: str, local_dir: Path, s3_prefix: str) -> int:
    """Upload a directory to S3/Spaces.

    Args:
        s3_client: Boto3 S3 client.
        bucket: Bucket name.
        local_dir: Local directory to upload.
        s3_prefix: Prefix for S3 keys.

    Returns:
        Number of files uploaded.
    """
    uploaded = 0

    if not local_dir.exists():
        print(f"⚠ Warning: Directory not found: {local_dir}")
        return 0

    print(f"\nUploading {local_dir} to s3://{bucket}/{s3_prefix}")
    print("-" * 60)

    for file_path in local_dir.rglob("*"):
        if file_path.is_file():
            # Calculate relative path for S3 key
            relative_path = file_path.relative_to(local_dir)
            s3_key = f"{s3_prefix}/{relative_path}".replace("\\", "/")

            try:
                print(f"  Uploading: {relative_path}")
                s3_client.upload_file(str(file_path), bucket, s3_key)
                uploaded += 1
            except ClientError as e:
                print(f"  ✗ Error uploading {relative_path}: {e}")

    return uploaded


def upload_file(s3_client, bucket: str, local_file: Path, s3_key: str) -> bool:
    """Upload a single file to S3/Spaces.

    Args:
        s3_client: Boto3 S3 client.
        bucket: Bucket name.
        local_file: Local file to upload.
        s3_key: S3 object key.

    Returns:
        True if successful, False otherwise.
    """
    if not local_file.exists():
        print(f"⚠ Warning: File not found: {local_file}")
        return False

    try:
        print(f"\nUploading {local_file.name} to s3://{bucket}/{s3_key}")
        s3_client.upload_file(str(local_file), bucket, s3_key)
        print(f"  ✓ Uploaded successfully")
        return True
    except ClientError as e:
        print(f"  ✗ Error uploading: {e}")
        return False


def main() -> None:
    """Upload databases to DigitalOcean Spaces."""
    print("=" * 60)
    print("Publisher RAG Demo - Upload to DigitalOcean Spaces")
    print("=" * 60)

    # Check environment variables
    required_vars = ["SPACES_KEY", "SPACES_SECRET"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        print("\n✗ Error: Missing required environment variables:")
        for var in missing_vars:
            print(f"  - {var}")
        print("\nPlease set the following environment variables:")
        print("  export SPACES_KEY=your_access_key")
        print("  export SPACES_SECRET=your_secret_key")
        print("  export SPACES_BUCKET=publisher-rag-data  # optional")
        print("  export SPACES_REGION=nyc3  # optional")
        sys.exit(1)

    bucket = os.getenv("SPACES_BUCKET", "publisher-rag-data")
    region = os.getenv("SPACES_REGION", "nyc3")

    print(f"\nConfiguration:")
    print(f"  Bucket: {bucket}")
    print(f"  Region: {region}")
    print(f"  Endpoint: https://{region}.digitaloceanspaces.com")

    # Create S3 client
    try:
        s3 = get_s3_client()
    except Exception as e:
        print(f"\n✗ Error creating S3 client: {e}")
        sys.exit(1)

    # Check if bucket exists
    print(f"\nChecking if bucket exists...")
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"  ✓ Bucket '{bucket}' found")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "404":
            print(f"  ✗ Bucket '{bucket}' not found")
            print(f"\nCreate the bucket first:")
            print(f"  doctl spaces create {bucket} --region {region}")
            sys.exit(1)
        else:
            print(f"  ✗ Error accessing bucket: {e}")
            sys.exit(1)

    # Upload ChromaDB directory
    chroma_dir = Path(CHROMA_PERSIST_DIR)
    chroma_files = upload_directory(s3, bucket, chroma_dir, "chroma_db")

    # Upload SQLite database
    db_file = Path("data/articles.db")
    db_uploaded = upload_file(s3, bucket, db_file, "articles.db")

    # Upload ingested files tracking
    ingested_files = Path("data/ingested_files.json")
    ingested_uploaded = upload_file(s3, bucket, ingested_files, "ingested_files.json")

    # Summary
    print("\n" + "=" * 60)
    print("Upload Summary")
    print("=" * 60)
    print(f"ChromaDB files uploaded: {chroma_files}")
    print(f"SQLite database: {'✓' if db_uploaded else '✗'}")
    print(f"Ingested files tracking: {'✓' if ingested_uploaded else '✗'}")
    print("\n✓ Upload complete!")
    print(f"\nFiles available at:")
    print(f"  https://{bucket}.{region}.digitaloceanspaces.com/")


if __name__ == "__main__":
    main()
