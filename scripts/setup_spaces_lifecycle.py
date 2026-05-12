"""Set the 30-day auto-delete lifecycle rule on the Amplora recordings bucket.

DigitalOcean Spaces doesn't expose lifecycle rules in their web dashboard;
they have to be set via the S3-compatible API. This script does that once.

Run after creating the bucket + access keys:

    uv run python scripts/setup_spaces_lifecycle.py

Idempotent: if the rule already exists, the call replaces it with the
same payload — no harm in re-running.

Trevor's 2026-05-11 decision: record audio, 30-day retention, owner sees
disclosure on /interview. This script is what enforces the 30 days
automatically so old recordings don't accumulate and inflate the bill.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `uv run python scripts/...` to find the `src` package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import src.core.config as cfg  # noqa: E402


def main() -> int:
    missing = []
    if not cfg.SPACES_ENDPOINT:
        missing.append("SPACES_ENDPOINT")
    if not cfg.SPACES_ACCESS_KEY:
        missing.append("SPACES_ACCESS_KEY")
    if not cfg.SPACES_SECRET_KEY:
        missing.append("SPACES_SECRET_KEY")
    if not cfg.SPACES_BUCKET:
        missing.append("SPACES_BUCKET")
    if missing:
        print(f"[FAIL] missing env vars: {', '.join(missing)}")
        print("       Fill them in .env, then re-run.")
        return 1

    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        print("[FAIL] boto3 not installed (run `uv sync`).")
        return 1

    client = boto3.client(
        "s3",
        endpoint_url=cfg.SPACES_ENDPOINT,
        aws_access_key_id=cfg.SPACES_ACCESS_KEY,
        aws_secret_access_key=cfg.SPACES_SECRET_KEY,
        region_name=cfg.SPACES_REGION,
        config=BotoConfig(signature_version="s3v4"),
    )

    # Sanity: confirm bucket reachable.
    try:
        client.head_bucket(Bucket=cfg.SPACES_BUCKET)
        print(f"[OK]   bucket reachable: {cfg.SPACES_BUCKET}")
    except Exception as e:
        print(f"[FAIL] cannot reach bucket {cfg.SPACES_BUCKET}: {e}")
        return 1

    # Apply the lifecycle rule.
    # The Filter={Prefix: ''} form is required by the S3 API for "match
    # everything in the bucket." DO Spaces follows the S3 spec for this.
    rule = {
        "Rules": [
            {
                "ID": "amplora-auto-delete-30d",
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "Expiration": {"Days": 30},
            }
        ]
    }

    try:
        client.put_bucket_lifecycle_configuration(
            Bucket=cfg.SPACES_BUCKET,
            LifecycleConfiguration=rule,
        )
        print(f"[OK]   30-day auto-delete rule applied to {cfg.SPACES_BUCKET}")
    except Exception as e:
        print(f"[FAIL] put_bucket_lifecycle_configuration failed: {e}")
        return 1

    # Read it back to confirm.
    try:
        got = client.get_bucket_lifecycle_configuration(Bucket=cfg.SPACES_BUCKET)
        rules = got.get("Rules", [])
        if not rules:
            print("[WARN] readback returned no rules — DO may have lagged. Retry in a minute.")
            return 1
        for r in rules:
            print(
                f"[OK]   readback: id={r.get('ID')!r} "
                f"status={r.get('Status')!r} "
                f"expiration_days={r.get('Expiration', {}).get('Days')}"
            )
    except Exception as e:
        print(f"[WARN] readback failed (but rule likely applied): {e}")

    print()
    print("Recordings older than 30 days will be deleted automatically.")
    print("This is a single-shot setup — you do not need to run this again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
