"""External-platform integrations.

Phase I.1 lands the first REAL ad-platform integration: LinkedIn. Everything
else (Meta / Google / TikTok) stays mocked in app/routers/ads.py until its own
phase. The `linkedin` subpackage is self-contained and dormant until
LINKEDIN_CLIENT_ID + LINKEDIN_CLIENT_SECRET are present in the environment —
see app/integrations/linkedin/config.py:is_live().
"""
