"""External-platform integrations.

Phase I.1 lands the first REAL ad-platform integration: LinkedIn. Everything
else (Google / TikTok) stays mocked in app/routers/ads.py until its own
phase. Phase 5c adds `meta` (Amplafai's system-user token; staff link each
client's ad account) — dormant until META_ACCESS_TOKEN + META_APP_SECRET. The `linkedin` subpackage is self-contained and dormant until
LINKEDIN_CLIENT_ID + LINKEDIN_CLIENT_SECRET are present in the environment —
see app/integrations/linkedin/config.py:is_live().
"""
