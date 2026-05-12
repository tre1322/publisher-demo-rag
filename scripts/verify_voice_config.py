"""Verify the W2.2 voice stack is wired up.

Run this after editing .env to confirm each provider is reachable:

    uv run python scripts/verify_voice_config.py

Checks each provider independently — if Deepgram fails, LiveKit and Cartesia
still report their own status. Exit code 0 if everything passes; non-zero
if any required check fails (BUSINESS_SESSION_SECRET = dev default counts
as a warning, not a failure, so you can use this in dev before prod).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `uv run python scripts/...` to find the `src` package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Force config to load .env early.
import src.core.config as cfg  # noqa: E402


# ── Pretty output ─────────────────────────────────────────────────────
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def header(title: str) -> None:
    print(f"\n{BOLD}-- {title} {'-' * (66 - len(title))}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}[WARN]{RESET} {msg}")


def dim(msg: str) -> None:
    print(f"  {DIM}{msg}{RESET}")


# ── Tracking ──────────────────────────────────────────────────────────
errors: list[str] = []
warnings: list[str] = []


# ── 1. Session secret ─────────────────────────────────────────────────
header("Session signing secret")
secret = os.getenv("BUSINESS_SESSION_SECRET", "")
if not secret:
    fail("BUSINESS_SESSION_SECRET is empty — voice callback HMACs will be insecure")
    errors.append("BUSINESS_SESSION_SECRET unset")
elif secret == "dev-secret-change-me":
    warn("BUSINESS_SESSION_SECRET still set to the dev default (fine for local dev)")
    warnings.append("BUSINESS_SESSION_SECRET is the dev default")
else:
    ok(f"BUSINESS_SESSION_SECRET set ({len(secret)} chars)")


# ── 2. LiveKit ────────────────────────────────────────────────────────
header("LiveKit Cloud")
if not cfg.LIVEKIT_URL:
    fail("LIVEKIT_URL not set — sign up at https://cloud.livekit.io")
    errors.append("LIVEKIT_URL")
elif not cfg.LIVEKIT_URL.startswith("wss://"):
    fail(f"LIVEKIT_URL should start with wss:// (got {cfg.LIVEKIT_URL!r})")
    errors.append("LIVEKIT_URL malformed")
else:
    ok(f"LIVEKIT_URL: {cfg.LIVEKIT_URL}")

if not cfg.LIVEKIT_API_KEY:
    fail("LIVEKIT_API_KEY not set")
    errors.append("LIVEKIT_API_KEY")
elif not cfg.LIVEKIT_API_KEY.startswith("API"):
    warn(f"LIVEKIT_API_KEY doesn't start with 'API' — usually they do (got prefix {cfg.LIVEKIT_API_KEY[:6]!r})")
else:
    ok(f"LIVEKIT_API_KEY: {cfg.LIVEKIT_API_KEY[:8]}…")

if not cfg.LIVEKIT_API_SECRET:
    fail("LIVEKIT_API_SECRET not set")
    errors.append("LIVEKIT_API_SECRET")
else:
    ok(f"LIVEKIT_API_SECRET: {cfg.LIVEKIT_API_SECRET[:4]}… ({len(cfg.LIVEKIT_API_SECRET)} chars)")

# Try to actually mint a token
if cfg.LIVEKIT_URL and cfg.LIVEKIT_API_KEY and cfg.LIVEKIT_API_SECRET:
    try:
        from src.modules.pmc.voice_provisioning import mint_participant_token

        token = mint_participant_token("test-room", "verify-user", "Verify User")
        if token and len(token) > 50:
            ok(f"Token mint works: {token[:30]}…")
        else:
            fail(f"Token mint returned suspicious value: {token!r}")
            errors.append("LiveKit token mint")
    except Exception as e:
        fail(f"Token mint raised: {type(e).__name__}: {e}")
        errors.append(f"LiveKit token mint: {e}")


# ── 3. Deepgram ───────────────────────────────────────────────────────
header("Deepgram (STT)")
if not cfg.DEEPGRAM_API_KEY:
    warn("DEEPGRAM_API_KEY not set — agent worker will fail on first call")
    warnings.append("DEEPGRAM_API_KEY")
else:
    ok(f"DEEPGRAM_API_KEY: {cfg.DEEPGRAM_API_KEY[:6]}… ({len(cfg.DEEPGRAM_API_KEY)} chars)")
    dim("(actual API reachability is verified on Day 2 when the agent connects)")


# ── 4. Cartesia ───────────────────────────────────────────────────────
header("Cartesia (TTS)")
if not cfg.CARTESIA_API_KEY:
    warn("CARTESIA_API_KEY not set — agent worker will fail on first call")
    warnings.append("CARTESIA_API_KEY")
else:
    ok(f"CARTESIA_API_KEY: {cfg.CARTESIA_API_KEY[:6]}… ({len(cfg.CARTESIA_API_KEY)} chars)")
if not cfg.CARTESIA_VOICE_ID:
    warn("CARTESIA_VOICE_ID not set — falling back to config default")
else:
    ok(f"CARTESIA_VOICE_ID: {cfg.CARTESIA_VOICE_ID}")


# ── 5. DigitalOcean Spaces (recording bucket) ─────────────────────────
header("DigitalOcean Spaces (recording)")
if not cfg.PMC_VOICE_RECORDING_ENABLED:
    dim("Recording disabled via PMC_VOICE_RECORDING_ENABLED=false — skipping Spaces checks")
else:
    spaces_vars = {
        "SPACES_ENDPOINT": cfg.SPACES_ENDPOINT,
        "SPACES_ACCESS_KEY": cfg.SPACES_ACCESS_KEY,
        "SPACES_SECRET_KEY": cfg.SPACES_SECRET_KEY,
        "SPACES_BUCKET": cfg.SPACES_BUCKET,
    }
    missing = [k for k, v in spaces_vars.items() if not v]
    if missing:
        warn(f"Spaces vars missing: {', '.join(missing)} — recording will fail until set")
        warnings.append(f"Spaces: {missing}")
    else:
        ok(f"Spaces endpoint: {cfg.SPACES_ENDPOINT}")
        ok(f"Spaces bucket: {cfg.SPACES_BUCKET}")
        # Verify the runtime path actually works: put a tiny marker file,
        # read it back, delete it. This tests the exact permissions LiveKit
        # Egress will use to upload recordings — head_bucket is too privileged
        # for DO's Limited Access scope (which only grants object-level ops).
        try:
            import boto3
            from botocore.config import Config as BotoConfig

            client = boto3.client(
                "s3",
                endpoint_url=cfg.SPACES_ENDPOINT,
                aws_access_key_id=cfg.SPACES_ACCESS_KEY,
                aws_secret_access_key=cfg.SPACES_SECRET_KEY,
                region_name=cfg.SPACES_REGION,
                config=BotoConfig(signature_version="s3v4"),
            )
            marker_key = ".amplora-verify-marker"
            marker_body = b"amplora-verify"
            client.put_object(
                Bucket=cfg.SPACES_BUCKET, Key=marker_key, Body=marker_body
            )
            got = client.get_object(Bucket=cfg.SPACES_BUCKET, Key=marker_key)
            body = got["Body"].read()
            assert body == marker_body, f"readback mismatch: {body!r}"
            client.delete_object(Bucket=cfg.SPACES_BUCKET, Key=marker_key)
            ok(
                f"put/get/delete roundtrip succeeded — credentials work for "
                f"recording uploads to {cfg.SPACES_BUCKET}"
            )
        except Exception as e:
            fail(f"Spaces put/get/delete roundtrip failed: {type(e).__name__}: {e}")
            errors.append(f"Spaces: {e}")


# ── Summary ───────────────────────────────────────────────────────────
print()
print(f"{BOLD}-- Summary {'-' * 60}{RESET}")
if not errors and not warnings:
    print(f"  {GREEN}All checks passed — ready for Day 2.{RESET}")
    sys.exit(0)
if errors:
    print(f"  {RED}{len(errors)} blocker(s):{RESET}")
    for e in errors:
        print(f"    - {e}")
if warnings:
    print(f"  {YELLOW}{len(warnings)} warning(s):{RESET}")
    for w in warnings:
        print(f"    - {w}")
print()
if errors:
    print(f"  {RED}{BOLD}Fix the blockers above before Day 2.{RESET}")
    sys.exit(1)
else:
    print(f"  {YELLOW}Warnings are not blockers — Day 2 can start, but they'll bite later.{RESET}")
    sys.exit(0)
