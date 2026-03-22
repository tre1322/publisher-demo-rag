# Publisher RAG Demo - Deployment Guide

Complete step-by-step guide for deploying to DigitalOcean App Platform with Spaces-based database storage.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Local Setup & Data Preparation](#local-setup--data-preparation)
4. [DigitalOcean Spaces Setup](#digitalocean-spaces-setup)
5. [App Platform Deployment](#app-platform-deployment)
6. [Verification & Testing](#verification--testing)
7. [Troubleshooting](#troubleshooting)
8. [Cost Breakdown](#cost-breakdown)

---

## Overview

This deployment strategy uses **DigitalOcean Spaces** (S3-compatible object storage) to store pre-ingested databases, which are downloaded when the container starts. This approach:

- **Separates data from code** - Update databases without rebuilding Docker image
- **Enables full metadata** - Ingest locally with Claude API for rich metadata (location, subjects, summaries)
- **Faster builds** - No ingestion during Docker build (~2-3 minutes vs 5-10 minutes)
- **Smaller images** - ~500MB vs ~2GB with prebaked data
- **Quick startup** - ~10-15 seconds (download from Spaces + load ads/events)

### Architecture Flow

```
┌──────────────────────────────────────────────┐
│  Your Local Machine                          │
│                                               │
│  1. Download 50 sample articles              │
│  2. Ingest with Claude API (rich metadata)   │
│  3. Upload to DigitalOcean Spaces            │
│     - ChromaDB embeddings                    │
│     - SQLite database                        │
└────────────────┬─────────────────────────────┘
                 │
                 │ boto3 upload
                 ▼
      ┌─────────────────────────┐
      │ DigitalOcean Spaces     │
      │  (Object Storage)       │
      │                         │
      │  publisher-rag-data/    │
      │  ├── chroma_db/         │
      │  ├── articles.db        │
      │  └── ingested_files...  │
      └────────────┬────────────┘
                   │
                   │ s3cmd download
                   ▼
┌──────────────────────────────────────────────┐
│  DigitalOcean App Platform                   │
│  (Serverless Container)                      │
│                                               │
│  1. Container starts                         │
│  2. Downloads databases from Spaces          │
│  3. Loads sample ads & events                │
│  4. Starts Gradio chatbot (port 7860)        │
└──────────────────────────────────────────────┘
```

---

## Prerequisites

### Required Accounts

1. **Anthropic API Key** - For Claude LLM
   - Sign up at https://console.anthropic.com
   - Create API key (billing must be enabled)

2. **DigitalOcean Account** - For Spaces and App Platform
   - Sign up at https://www.digitalocean.com
   - Add payment method

3. **GitHub Account** - For repository and auto-deploy
   - Fork or push this repo to your GitHub account

### Required Tools

```bash
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install doctl (DigitalOcean CLI) - Optional but recommended
# macOS
brew install doctl

# Linux
cd ~
wget https://github.com/digitalocean/doctl/releases/download/v1.104.0/doctl-1.104.0-linux-amd64.tar.gz
tar xf ~/doctl-1.104.0-linux-amd64.tar.gz
sudo mv ~/doctl /usr/local/bin
```

---

## Local Setup & Data Preparation

### Step 1: Clone and Setup Project

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/publisher-rag-demo.git
cd publisher-rag-demo

# Install dependencies
uv sync

# Create .env file
cp .env.example .env
```

### Step 2: Configure Environment Variables

Edit `.env` and add your Anthropic API key:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-api03-...

# Optional (defaults shown)
LLM_MODEL=claude-sonnet-4-20250514
LLM_TEMPERATURE=0.3
EMBEDDING_MODEL=all-MiniLM-L6-v2
CHUNK_SIZE=1024
CHUNK_OVERLAP=200
RETRIEVAL_TOP_K=5
SIMILARITY_THRESHOLD=0.7
```

### Step 3: Download and Ingest Sample Data

```bash
# 1. Download 50 sample news articles from RSS feeds
uv run python scripts/download_samples.py

# Expected output:
# Downloading 50 sample news articles...
# [1/50] Article title...
# ...
# Downloaded 50 articles to data/documents

# 2. Initialize database tables
uv run python scripts/init_db.py

# Expected output:
# Initializing database tables...
# ✓ Database tables initialized successfully

# 3. Ingest documents with FULL metadata extraction (uses Claude API)
uv run python scripts/ingest.py

# Expected output:
# Publisher RAG Demo - Document Ingestion
# Found 50 documents in data/documents
# Starting ingestion...
# Extracting rich metadata for: Article_1.txt
#   Location: Washington, Subjects: politics, technology
# ...
# Ingestion Complete!
# Total files found:    50
# Files ingested:       50
# Total chunks created: ~250-300
```

**Important Notes:**
- Step 3 makes Claude API calls for each article (~50 calls)
- Extraction includes: location, subjects, summary
- Takes ~2-3 minutes total
- This is a **one-time cost** - results are uploaded to Spaces

### Step 4: Verify Local Data

```bash
# Check ingestion statistics
uv run python scripts/ingest.py --stats

# Expected output:
# Total chunks in collection: 250
# Files tracked as ingested: 50

# Verify database exists
ls -lh data/
# Should see:
#   articles.db         (~100KB - SQLite database)
#   chroma_db/          (~50MB - ChromaDB embeddings)
#   documents/          (~2MB - source articles)
#   ingested_files.json (~2KB - tracking)
```

### Step 5: Test Locally (Optional but Recommended)

```bash
# Load sample ads and events
uv run python scripts/load_sample_ads.py
uv run python scripts/load_sample_events.py

# Start chatbot locally
uv run python src/chatbot.py

# Open browser to http://localhost:7860
# Test queries:
# - "What's happening in technology?"
# - "Any deals on electronics?"
# - "Events this weekend?"
```

---

## DigitalOcean Spaces Setup

### Step 1: Create a Space

**Via DigitalOcean Console:**

1. Go to https://cloud.digitalocean.com/spaces
2. Click **Create Space**
3. Configure:
   - **Region**: `New York 3` (or same as your app)
   - **Space Name**: `publisher-rag-data`
   - **Enable CDN**: No (not needed)
   - **File Listing**: Disabled (recommended for security)
4. Click **Create Space**

**Via doctl CLI:**

```bash
# Authenticate with DigitalOcean
doctl auth init

# Create Space
doctl spaces create publisher-rag-data --region nyc3

# Expected output:
# Space created: publisher-rag-data (nyc3)
```

### Step 2: Generate Spaces Access Keys

**Via DigitalOcean Console:**

1. Go to **Spaces** → **Manage Keys** (or https://cloud.digitalocean.com/account/api/spaces)
2. Click **Generate New Key**
3. Name: `publisher-rag-upload`
4. Click **Generate Key**
5. **IMPORTANT**: Copy both the **Access Key** and **Secret Key** immediately
   - Access Key: `DO00ABCD...`
   - Secret Key: `XYZ123...`
6. Save these securely - you cannot retrieve the secret later

**Via doctl CLI:**

```bash
# Generate key pair
doctl spaces keys create publisher-rag-upload

# Expected output:
# Access Key: DO00ABCD...
# Secret Key: XYZ123...
```

### Step 3: Upload Databases to Spaces

Set environment variables with your Spaces credentials:

```bash
# Set Spaces credentials
export SPACES_KEY=DO00ABCD...        # Your Access Key
export SPACES_SECRET=XYZ123...       # Your Secret Key
export SPACES_BUCKET=publisher-rag-data
export SPACES_REGION=nyc3

# Run upload script
uv run python scripts/upload_to_spaces.py
```

**Expected Output:**

```
============================================================
Publisher RAG Demo - Upload to DigitalOcean Spaces
============================================================

Configuration:
  Bucket: publisher-rag-data
  Region: nyc3
  Endpoint: https://nyc3.digitaloceanspaces.com

Checking if bucket exists...
  ✓ Bucket 'publisher-rag-data' found

Uploading data/chroma_db to s3://publisher-rag-data/chroma_db
------------------------------------------------------------
  Uploading: chroma.sqlite3
  Uploading: 00000000-0000-0000-0000-000000000000/...
  ...

Uploading articles.db to s3://publisher-rag-data/articles.db
  ✓ Uploaded successfully

Uploading ingested_files.json to s3://publisher-rag-data/ingested_files.json
  ✓ Uploaded successfully

============================================================
Upload Summary
============================================================
ChromaDB files uploaded: 15
SQLite database: ✓
Ingested files tracking: ✓

✓ Upload complete!

Files available at:
  https://publisher-rag-data.nyc3.digitaloceanspaces.com/
```

### Step 4: Verify Upload

**Via DigitalOcean Console:**

1. Go to **Spaces** → `publisher-rag-data`
2. You should see:
   - `chroma_db/` folder
   - `articles.db` file
   - `ingested_files.json` file

**Via doctl CLI:**

```bash
# List files in Space
doctl spaces ls publisher-rag-data --recursive

# Expected output:
# chroma_db/chroma.sqlite3
# chroma_db/00000000-0000-0000-0000-000000000000/...
# articles.db
# ingested_files.json
```

---

## App Platform Deployment

### Step 1: Push Code to GitHub

```bash
# Initialize git (if not already)
git init
git add .
git commit -m "Initial commit with Spaces support"

# Add remote and push
git remote add origin https://github.com/YOUR_USERNAME/publisher-rag-demo.git
git branch -M main
git push -u origin main
```

### Step 2: Create App in DigitalOcean

**Via DigitalOcean Console:**

1. Go to https://cloud.digitalocean.com/apps
2. Click **Create App**
3. **Source**: Select **GitHub**
4. **Authorize DigitalOcean** to access your GitHub
5. **Repository**: Select `YOUR_USERNAME/publisher-rag-demo`
6. **Branch**: `main`
7. **Autodeploy**: Enable (deploys on git push)
8. Click **Next**

9. **Resources** - App Platform should auto-detect:
   - **Type**: Dockerfile
   - **Dockerfile Path**: `Dockerfile`
   - **HTTP Port**: 7860
   - If not detected, select **Edit** and configure manually

10. Click **Next**

11. **Environment Variables** - Add the following:

```yaml
# Required Secrets (click "Encrypt" checkbox)
ANTHROPIC_API_KEY = sk-ant-api03-...
SPACES_KEY = DO00ABCD...
SPACES_SECRET = XYZ123...

# Required Non-Secret
SPACES_BUCKET = publisher-rag-data
SPACES_REGION = nyc3

# Optional Configuration
LLM_MODEL = claude-sonnet-4-20250514
LLM_TEMPERATURE = 0.3
EMBEDDING_MODEL = all-MiniLM-L6-v2
SIMILARITY_THRESHOLD = 0.7
PYTHONUNBUFFERED = 1
```

12. Click **Next**

13. **App Info**:
    - **Name**: `publisher-rag-demo`
    - **Region**: `New York 3` (same as Spaces)

14. **Review**:
    - **Plan**: Basic (512MB RAM, 1 vCPU) - ~$5/month
    - Upgrade to Professional (1GB RAM) if needed

15. Click **Create Resources**

**Via doctl CLI (Recommended):**

```bash
# Update .do/app.yaml with your GitHub repo
# Then create app
doctl apps create --spec .do/app.yaml

# Expected output:
# Notice: App created
# ID: 12345678-abcd-...
# Name: publisher-rag-demo
# Status: PENDING

# Get app ID for later
export APP_ID=12345678-abcd-...
```

### Step 3: Configure Secrets in Console

Even if using doctl, you must set secrets via the console:

1. Go to **Apps** → `publisher-rag-demo` → **Settings**
2. Click **Environment Variables**
3. Add encrypted secrets:
   - `ANTHROPIC_API_KEY` (click Encrypt)
   - `SPACES_KEY` (click Encrypt)
   - `SPACES_SECRET` (click Encrypt)
4. Save changes

### Step 4: Monitor Deployment

**Via Console:**

1. Go to **Apps** → `publisher-rag-demo`
2. Watch the **Activity** tab for build progress
3. Build takes ~3-5 minutes

**Via doctl CLI:**

```bash
# Watch logs in real-time
doctl apps logs $APP_ID --type build --follow

# Expected output:
# [Build] Building from Dockerfile...
# [Build] Step 1/12 : FROM python:3.13-slim
# ...
# [Build] Successfully built abc123def456
# [Build] Build complete!

# Check deployment status
doctl apps get $APP_ID

# When Status shows "DEPLOYED", get the URL
doctl apps get $APP_ID --format URL
```

### Step 5: Wait for Container Startup

After build completes, container initialization runs:

```
[1/4] Checking for pre-ingested databases...
Databases not found locally. Attempting to download from Spaces...
Downloading databases from s3://publisher-rag-data...
  ✓ ChromaDB downloaded
  ✓ SQLite database downloaded
  ✓ Ingested files tracking downloaded
✓ Database download complete

[2/4] Initializing database tables...
✓ Database tables initialized

[3/4] Loading sample advertisements...
✓ Sample ads loaded

[4/4] Loading sample events...
✓ Sample events loaded

Initialization complete!
Starting Gradio chatbot...
Running on public URL: https://abc123.ondigitalocean.app
```

**Timeline:**
- Build: 3-5 minutes
- Startup: 10-15 seconds
- Total: ~5 minutes from push to live

---

## Verification & Testing

### Step 1: Access the Application

Your app URL format:
```
https://YOUR_APP_NAME-xxxxx.ondigitalocean.app
```

Find it via:
- **Console**: Apps → `publisher-rag-demo` → **Live App** button
- **CLI**: `doctl apps get $APP_ID --format URL`

### Step 2: Test Queries

Try these example queries to verify all features:

**News Queries (ChromaDB + SQLite):**
```
"What's happening in technology?"
"Recent political news"
"Tell me about climate change"
```

**Advertisement Queries (SQLite):**
```
"Any deals?"
"Electronics under $200"
"What's on sale?"
```

**Event Queries (SQLite):**
```
"Events this weekend"
"Free concerts"
"What's happening downtown?"
```

**Conversation Context:**
```
User: "What's happening in technology?"
Bot: [Response about tech news]
User: "Tell me more about that"
Bot: [Expanded response using context]
```

### Step 3: Verify Data Quality

Check that articles have rich metadata:

```
Query: "News about Washington"
Response should include:
- Articles tagged with location: "Washington"
- Relevant subjects extracted by Claude
- Accurate summaries
```

---

## Troubleshooting

### Build Failures

**Error: "Failed to download pytorch"**

Solution: This should not happen with CPU-only index. If it does:
```dockerfile
# In Dockerfile, verify:
--index-url https://download.pytorch.org/whl/cpu
```

**Error: "s3cmd: command not found"**

Solution: Ensure Dockerfile includes:
```dockerfile
apt-get install -y --no-install-recommends curl s3cmd
```

### Startup Failures

**Error: "ANTHROPIC_API_KEY environment variable is not set"**

Solution:
1. Go to App Settings → Environment Variables
2. Verify `ANTHROPIC_API_KEY` is set and encrypted
3. Restart deployment

**Error: "Failed to download ChromaDB" / "Failed to download SQLite database"**

Solution:
1. Verify Spaces credentials:
   ```bash
   doctl apps get $APP_ID
   # Check SPACES_KEY, SPACES_SECRET are set
   ```
2. Verify bucket exists and has files:
   ```bash
   doctl spaces ls publisher-rag-data
   ```
3. Check bucket region matches `SPACES_REGION`:
   ```bash
   # If bucket is in sfo3 but env says nyc3, update:
   SPACES_REGION=sfo3
   ```

**Warning: "Spaces credentials not configured"**

This means the app is running with **empty databases**. Set:
- `SPACES_BUCKET`
- `SPACES_KEY`
- `SPACES_SECRET`

### Health Check Failures

**Error: "Failed health checks after X attempts"**

Solutions:
1. **Increase timeout** in `.do/app.yaml`:
   ```yaml
   initial_delay_seconds: 90  # Increase from 60
   ```

2. **Check logs** for actual error:
   ```bash
   doctl apps logs $APP_ID --type run --tail 100
   ```

3. **Verify port** in `.do/app.yaml`:
   ```yaml
   http_port: 7860
   ```

### Runtime Errors

**Error: "No such table: articles"**

Solution: Database wasn't initialized. Check init.sh ran:
```bash
doctl apps logs $APP_ID --type run | grep "Initializing database"
```

**Error: "Collection not found: publisher_main"**

Solution: ChromaDB download failed or incomplete:
```bash
# Re-upload to Spaces
uv run python scripts/upload_to_spaces.py

# Restart app
doctl apps create-deployment $APP_ID
```

### Data Updates

**Question: "How do I update articles without rebuilding?"**

Answer:
```bash
# 1. Ingest new articles locally
uv run python scripts/download_samples.py --count 100
uv run python scripts/ingest.py

# 2. Re-upload to Spaces
uv run python scripts/upload_to_spaces.py

# 3. Restart app (container will download fresh data)
doctl apps create-deployment $APP_ID --force-rebuild false
```

---

## Cost Breakdown

### DigitalOcean Pricing

**App Platform:**
- Basic (512MB RAM): $5/month
- Professional (1GB RAM): $12/month
- Billed per second of usage

**Spaces:**
- Storage: $5/month for 250GB
- Transfer: 1TB outbound included, then $0.01/GB
- Expected usage: ~50MB storage + minimal transfer = **$5/month**

**Total Monthly Cost:**
- Basic deployment: **~$10/month** ($5 app + $5 spaces)
- Professional: **~$17/month** ($12 app + $5 spaces)

### Anthropic API Costs

**One-time ingestion cost** (local):
- 50 articles × ~1000 tokens/article = 50K tokens input
- Metadata extraction: ~10K tokens output
- **Cost: ~$0.50** (one-time)

**Runtime cost** (per query):
- Average query: 2K context + 500 tokens response = 2.5K tokens
- **Cost: ~$0.01 per query**

**Monthly estimate** (100 queries/month):
- Ingestion: $0 (already done)
- Queries: ~$1/month
- **Total API: ~$1/month**

### Grand Total

**~$11-18/month** (infrastructure + API)

---

## Next Steps

### Production Enhancements

1. **Add More Documents**
   ```bash
   # Place PDFs/TXTs in data/documents/
   uv run python scripts/ingest.py
   uv run python scripts/upload_to_spaces.py
   ```

2. **Enable HTTPS** (automatic on App Platform)

3. **Add Authentication** (optional)
   ```python
   # In src/chatbot.py:
   demo.launch(auth=("admin", "password"))
   ```

4. **Monitor Usage**
   ```bash
   doctl apps list-metrics $APP_ID
   ```

5. **Set Up Alerts**
   - Apps → Settings → Alerts
   - Configure CPU/RAM thresholds

### Scaling

**Horizontal Scaling:**
```yaml
# In .do/app.yaml:
instance_count: 3  # Scale to 3 replicas
```

**Vertical Scaling:**
- Apps → Settings → Resources
- Upgrade to Professional (1GB RAM)
- Or higher tiers if needed

---

## Support

**Documentation:**
- Publisher RAG Demo: See [README.md](../README.md)
- DigitalOcean App Platform: https://docs.digitalocean.com/products/app-platform/
- DigitalOcean Spaces: https://docs.digitalocean.com/products/spaces/

**Issues:**
- GitHub Issues: https://github.com/YOUR_USERNAME/publisher-rag-demo/issues

**Community:**
- DigitalOcean Community: https://www.digitalocean.com/community/
