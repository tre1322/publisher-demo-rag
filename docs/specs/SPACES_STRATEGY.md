# Alternative Strategy: Use DigitalOcean Spaces for Pre-populated Databases

## Overview

Instead of prebaking data into the Docker image, download pre-ingested databases from DigitalOcean Spaces on container startup.

## Benefits

- **Smaller Docker images** - No data baked in (~500MB vs ~2GB)
- **Faster builds** - No ingestion during Docker build
- **Flexible updates** - Can update data without rebuilding image
- **Fast startup** - ~5-10 seconds (just download from Spaces)
- **Separation of concerns** - Data separated from code
- **Full metadata** - Can ingest locally with Claude API for rich metadata

## Architecture

```
┌─────────────────────────────────────────────┐
│  Development Machine                        │
│                                              │
│  1. Download sample articles                │
│  2. Ingest with full metadata (Claude API)  │
│  3. Upload to DO Spaces                      │
│     - data/chroma_db/                        │
│     - data/articles.db                       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │ DigitalOcean Spaces │
         │   (S3-compatible)   │
         │                     │
         │  publisher-rag-data │
         │   ├── chroma_db/    │
         │   └── articles.db   │
         └──────────┬──────────┘
                    │
                    ▼
┌───────────────────────────────────────────────┐
│  DigitalOcean App Platform Container          │
│                                                │
│  1. Start container                           │
│  2. Download databases from Spaces (if empty) │
│  3. Load sample ads/events                    │
│  4. Start Gradio chatbot                      │
└───────────────────────────────────────────────┘
```

## Implementation Plan

### 1. Create Upload Script: `scripts/upload_to_spaces.py`

```python
#!/usr/bin/env python
"""Upload pre-ingested databases to DigitalOcean Spaces."""

import boto3
import os
from pathlib import Path

def upload_to_spaces():
    """Upload data to DigitalOcean Spaces."""

    # Configure S3 client for DO Spaces
    s3 = boto3.client(
        's3',
        region_name=os.getenv('SPACES_REGION', 'nyc3'),
        endpoint_url=f"https://{os.getenv('SPACES_REGION', 'nyc3')}.digitaloceanspaces.com",
        aws_access_key_id=os.getenv('SPACES_KEY'),
        aws_secret_access_key=os.getenv('SPACES_SECRET')
    )

    bucket = os.getenv('SPACES_BUCKET', 'publisher-rag-data')

    # Upload ChromaDB directory
    chroma_dir = Path('data/chroma_db')
    for file in chroma_dir.rglob('*'):
        if file.is_file():
            key = f"chroma_db/{file.relative_to(chroma_dir)}"
            print(f"Uploading {key}...")
            s3.upload_file(str(file), bucket, key)

    # Upload SQLite database
    db_file = Path('data/articles.db')
    if db_file.exists():
        print(f"Uploading articles.db...")
        s3.upload_file(str(db_file), bucket, 'articles.db')

    print("Upload complete!")

if __name__ == "__main__":
    upload_to_spaces()
```

**Usage:**
```bash
# Set credentials
export SPACES_KEY=your_access_key
export SPACES_SECRET=your_secret_key
export SPACES_BUCKET=publisher-rag-data
export SPACES_REGION=nyc3

# Ingest locally with full metadata
uv run python scripts/download_samples.py
uv run python scripts/ingest.py  # Uses Claude API for rich metadata

# Upload to Spaces
uv run python scripts/upload_to_spaces.py
```

### 2. Modify `scripts/init.sh`

Add database download step before loading ads/events:

```bash
#!/bin/bash
set -e

echo "======================================"
echo "Publisher RAG Demo - Initialization"
echo "======================================"

# Check for required environment variable
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY environment variable is not set"
    exit 1
fi

# Download databases from Spaces if they don't exist
if [ ! -d "data/chroma_db" ] || [ ! -f "data/articles.db" ]; then
    echo ""
    echo "Downloading pre-ingested databases from Spaces..."

    if [ -n "$SPACES_BUCKET" ] && [ -n "$SPACES_KEY" ]; then
        # Configure s3cmd
        cat > ~/.s3cfg << EOF
[default]
access_key = $SPACES_KEY
secret_key = $SPACES_SECRET
host_base = ${SPACES_REGION}.digitaloceanspaces.com
host_bucket = %(bucket)s.${SPACES_REGION}.digitaloceanspaces.com
use_https = True
EOF

        # Download databases
        s3cmd sync --no-preserve s3://$SPACES_BUCKET/chroma_db/ data/chroma_db/
        s3cmd get s3://$SPACES_BUCKET/articles.db data/articles.db

        echo "✓ Databases downloaded from Spaces"
    else
        echo "⚠ Warning: Spaces credentials not set, using empty databases"
        mkdir -p data/chroma_db
    fi
else
    echo ""
    echo "Databases found: ✓"
fi

echo ""
echo "[1/2] Loading sample advertisements..."
# ... rest of init.sh
```

### 3. Modify `Dockerfile`

Remove prebaking steps, add s3cmd:

```dockerfile
# Install system dependencies and tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl s3cmd && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# ... rest of Dockerfile ...

# Remove the prebaking RUN step entirely
# Just create empty directories
RUN mkdir -p data/documents data/chroma_db && \
    chmod +x scripts/init.sh
```

### 4. Update `.do/app.yaml`

Add Spaces environment variables:

```yaml
envs:
  - key: ANTHROPIC_API_KEY
    scope: RUN_TIME
    type: SECRET

  # DigitalOcean Spaces configuration
  - key: SPACES_BUCKET
    scope: RUN_TIME
    value: "publisher-rag-data"

  - key: SPACES_REGION
    scope: RUN_TIME
    value: "nyc3"

  - key: SPACES_KEY
    scope: RUN_TIME
    type: SECRET

  - key: SPACES_SECRET
    scope: RUN_TIME
    type: SECRET

  # ... rest of env vars ...
```

### 5. Update `README.md`

Add section on Spaces setup:

```markdown
## DigitalOcean Spaces Setup (Optional)

To use pre-populated databases from Spaces:

1. **Create a Space** in DigitalOcean
   - Name: `publisher-rag-data`
   - Region: Same as your app (e.g., `nyc3`)

2. **Generate Spaces Keys**
   - Spaces → Manage Keys → Generate New Key
   - Save Access Key and Secret Key

3. **Upload Data** (one-time, from local machine)
   ```bash
   # Ingest locally with full metadata
   uv run python scripts/download_samples.py
   uv run python scripts/ingest.py

   # Upload to Spaces
   export SPACES_KEY=your_access_key
   export SPACES_SECRET=your_secret_key
   uv run python scripts/upload_to_spaces.py
   ```

4. **Configure App Platform**
   - Add `SPACES_KEY` and `SPACES_SECRET` as secrets
   - Container will download databases on startup

**Benefits:**
- Databases include rich metadata (location, subjects, summaries)
- Update data without rebuilding Docker image
- Faster builds (no ingestion step)
```

## Setup Instructions

### Step 1: Create DigitalOcean Space

```bash
# Via doctl CLI
doctl spaces create publisher-rag-data --region nyc3

# Set CORS (if needed for direct browser access)
doctl spaces cors put publisher-rag-data --config cors.json
```

### Step 2: Generate Access Keys

In DigitalOcean Console:
- Spaces → Manage Keys → Generate New Key
- Save the Access Key ID and Secret Key

### Step 3: Ingest and Upload Data Locally

```bash
# Install dependencies
uv add boto3

# Set environment variables
export ANTHROPIC_API_KEY=your_anthropic_key
export SPACES_KEY=your_spaces_access_key
export SPACES_SECRET=your_spaces_secret_key
export SPACES_BUCKET=publisher-rag-data
export SPACES_REGION=nyc3

# Download and ingest with full metadata
uv run python scripts/download_samples.py
uv run python scripts/ingest.py  # Uses Claude for rich metadata

# Upload to Spaces
uv run python scripts/upload_to_spaces.py
```

### Step 4: Configure App Platform

Add to `.do/app.yaml` or via console:
- `SPACES_BUCKET=publisher-rag-data`
- `SPACES_REGION=nyc3`
- `SPACES_KEY` (secret)
- `SPACES_SECRET` (secret)

### Step 5: Deploy

Container will:
1. Start up
2. Download databases from Spaces (if not present)
3. Load ads/events
4. Start chatbot

## Comparison: Prebaking vs Spaces

| Aspect | Prebaking (Current) | Spaces Strategy |
|--------|---------------------|-----------------|
| **Docker Build Time** | 5-10 minutes | 2-3 minutes |
| **Image Size** | ~2GB | ~500MB |
| **Startup Time** | 10-15 seconds | 5-10 seconds |
| **Metadata Quality** | Basic only | Full (location, subjects, summary) |
| **Data Updates** | Rebuild image | Just re-upload |
| **API Calls** | None (build-time) | None (local dev) |
| **Complexity** | Simple | Moderate (requires Spaces setup) |

## Cost Considerations

**DigitalOcean Spaces Pricing:**
- Storage: $5/month for 250GB
- Transfer: 1TB outbound included, then $0.01/GB
- Expected usage for this demo: ~$5/month total

**Estimated Costs:**
- App Platform (basic-s): $5/month
- Spaces storage: $5/month
- **Total: ~$10/month**

## Migration Path

If you want to switch from prebaking to Spaces:

1. Run locally: download → ingest → upload to Spaces
2. Update Dockerfile (remove prebaking steps)
3. Update init.sh (add download from Spaces)
4. Update .do/app.yaml (add Spaces env vars)
5. Deploy updated code

The container will automatically download from Spaces on first startup.
