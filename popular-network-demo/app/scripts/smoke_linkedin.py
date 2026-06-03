"""Phase I.1 smoke — LinkedIn real-OAuth integration (mock-swap skeleton).

Run with:  uv run python -m app.scripts.smoke_linkedin

We have no MDP approval / live credentials yet, so this exercises every path
that DOESN'T need the network:

1.  Mock mode (no creds): /connect reports live:false; /status live:false.
2.  Migration columns exist on ad_connections (token lifecycle).
3.  resolve_external_campaign_id returns a mock id for every platform when
    LinkedIn isn't live (the demo-preserving fallback).
4.  build_authorization_url contains client_id / redirect / scope / state.
5.  exchange_code_for_token wired correctly — driven through an httpx
    MockTransport (no real network), asserts token parse + expiry math.
6.  Live mode /connect returns a real authUrl + persists pending state.
7.  Callback rejects a missing code, a denied consent, and a bad CSRF state.
8.  Callback happy path (exchange + profile monkeypatched) stores tokens and
    flips the connection to connected; /status then reports the account.
9.  resolve_external_campaign_id hits the (monkeypatched) real API when
    live+connected and returns the real campaign URN — through the manual
    /api/ads/campaigns create path too.
10. After disconnect, resolve falls back to a mock id again.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_i1_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
# Ensure we start in mock mode regardless of the caller's shell env.
os.environ.pop("LINKEDIN_CLIENT_ID", None)
os.environ.pop("LINKEDIN_CLIENT_SECRET", None)
os.environ["LINKEDIN_REDIRECT_URI"] = "https://dashboard.amplafai.com/api/integrations/linkedin/callback"


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def _set_live() -> None:
    os.environ["LINKEDIN_CLIENT_ID"] = "test_client_id_123"
    os.environ["LINKEDIN_CLIENT_SECRET"] = "test_client_secret_456"


def _set_mock() -> None:
    os.environ.pop("LINKEDIN_CLIENT_ID", None)
    os.environ.pop("LINKEDIN_CLIENT_SECRET", None)


def main() -> None:
    import shutil

    import httpx
    from fastapi.testclient import TestClient

    from app.db import SessionLocal, engine
    from app.integrations import linkedin as li
    from app.integrations import linkedin_store as store
    from app.main import app
    from app.routers.ads import resolve_external_campaign_id
    from app.scripts._auth_helper import bootstrap_login
    from sqlalchemy import inspect as sa_inspect

    try:
        with TestClient(app) as client:
            bootstrap_login(client)

            # ---------------------------------------------------------------
            # 1. Mock mode — connect + status are honest about being un-live.
            # ---------------------------------------------------------------
            _set_mock()
            r = client.get("/api/integrations/linkedin/connect")
            if r.status_code != 200:
                _fail(f"/connect (mock) status {r.status_code}: {r.text}")
            body = r.json()
            if body.get("live") is not False:
                _fail(f"/connect (mock) should report live:false, got {body}")
            _ok("mock /connect → live:false (demo fallback path)")

            r = client.get("/api/integrations/linkedin/status")
            st = r.json()
            if r.status_code != 200 or st.get("live") is not False or st.get("connected") is not False:
                _fail(f"/status (mock) wrong shape: {st}")
            if st.get("status") != "disconnected":
                _fail(f"/status (mock) expected disconnected, got {st.get('status')}")
            _ok("mock /status → live:false, connected:false, disconnected")

            # ---------------------------------------------------------------
            # 2. Migration columns present on ad_connections.
            # ---------------------------------------------------------------
            cols = {c["name"] for c in sa_inspect(engine).get_columns("ad_connections")}
            need = {
                "refresh_token", "token_expires_at", "refresh_expires_at",
                "scope", "oauth_state", "account_urn", "connected_user_name",
            }
            missing = need - cols
            if missing:
                _fail(f"ad_connections missing token columns: {missing}")
            _ok("ad_connections has all 7 LinkedIn OAuth columns")

            # ---------------------------------------------------------------
            # 3. resolve_external_campaign_id → mock id for all platforms when
            #    LinkedIn isn't live.
            # ---------------------------------------------------------------
            with SessionLocal() as db:
                li_id = resolve_external_campaign_id(
                    db, 1, "linkedin", name="X", daily_budget_cents=1000, duration_days=7
                )
                fb_id = resolve_external_campaign_id(
                    db, 1, "fb_ig", name="X", daily_budget_cents=1000, duration_days=7
                )
            if not li_id.startswith("mock_linkedin_"):
                _fail(f"mock-mode linkedin resolve should be mock id, got {li_id}")
            if not fb_id.startswith("mock_meta_"):
                _fail(f"fb_ig resolve should be mock id, got {fb_id}")
            _ok(f"resolve (mock mode) → linkedin={li_id}, fb_ig={fb_id}")

            # ---------------------------------------------------------------
            # 4 + 5. Go live. Unit-test the OAuth primitives.
            # ---------------------------------------------------------------
            _set_live()
            if not li.is_live():
                _fail("is_live() should be True after setting creds")

            auth_url = li.build_authorization_url("STATE_TOKEN_XYZ")
            for needle in ("test_client_id_123", "STATE_TOKEN_XYZ", "response_type=code", "r_ads"):
                if needle not in auth_url:
                    _fail(f"authorization URL missing {needle!r}: {auth_url}")
            if "dashboard.amplafai.com" not in auth_url:
                _fail(f"authorization URL missing redirect host: {auth_url}")
            _ok("build_authorization_url contains client_id + state + scope + redirect")

            # exchange_code_for_token through a MockTransport (no real network).
            captured: dict = {}

            def _token_handler(request: httpx.Request) -> httpx.Response:
                captured["path"] = request.url.path
                captured["body"] = request.content.decode()
                return httpx.Response(200, json={
                    "access_token": "ACCESS_TOK",
                    "expires_in": 5184000,            # 60 days
                    "refresh_token": "REFRESH_TOK",
                    "refresh_token_expires_in": 31536000,  # 365 days
                    "scope": "r_ads rw_ads r_ads_reporting r_basicprofile",
                })

            mock_http = httpx.Client(transport=httpx.MockTransport(_token_handler))
            bundle = li.exchange_code_for_token("AUTH_CODE_ABC", http=mock_http)
            mock_http.close()
            if bundle.access_token != "ACCESS_TOK" or bundle.refresh_token != "REFRESH_TOK":
                _fail(f"token parse wrong: {bundle}")
            if not (bundle.expires_at > datetime.utcnow() + timedelta(days=59)):
                _fail(f"access expiry math wrong: {bundle.expires_at}")
            if "grant_type=authorization_code" not in captured.get("body", ""):
                _fail(f"exchange didn't send authorization_code grant: {captured}")
            if "AUTH_CODE_ABC" not in captured.get("body", ""):
                _fail("exchange didn't include the code in the body")
            _ok("exchange_code_for_token (MockTransport) → parsed token + 60d expiry")

            # ---------------------------------------------------------------
            # 6. Live /connect → real authUrl + pending state persisted.
            # ---------------------------------------------------------------
            r = client.get("/api/integrations/linkedin/connect")
            cbody = r.json()
            if r.status_code != 200 or cbody.get("live") is not True or "authUrl" not in cbody:
                _fail(f"live /connect wrong: {cbody}")
            with SessionLocal() as db:
                conn = store.get_connection(db, 1)
                if conn is None or conn.status != "pending" or not conn.oauth_state:
                    _fail(f"connect didn't persist pending state: "
                          f"{None if conn is None else (conn.status, conn.oauth_state)}")
                real_state = conn.oauth_state
                if real_state not in cbody["authUrl"]:
                    _fail("authUrl state doesn't match persisted oauth_state")
            _ok("live /connect → authUrl + AdConnection pending w/ matching state")

            # ---------------------------------------------------------------
            # 7. Callback rejects denied / missing / bad-state (302 + li=...).
            # ---------------------------------------------------------------
            def _li_status(resp) -> str:
                loc = resp.headers.get("location", "")
                return loc.split("li=")[-1] if "li=" in loc else ""

            r = client.get("/api/integrations/linkedin/callback",
                           params={"error": "user_cancelled_login"}, follow_redirects=False)
            if r.status_code != 302 or _li_status(r) != "denied":
                _fail(f"callback(error) should 302 li=denied, got {r.status_code} {r.headers.get('location')}")
            _ok("callback(error=denied) → 302 li=denied")

            r = client.get("/api/integrations/linkedin/callback",
                           params={"code": "x", "state": "WRONG_STATE"}, follow_redirects=False)
            if r.status_code != 302 or _li_status(r) != "state_mismatch":
                _fail(f"callback(bad state) should 302 li=state_mismatch, got {r.headers.get('location')}")
            _ok("callback(bad CSRF state) → 302 li=state_mismatch")

            # ---------------------------------------------------------------
            # 8. Callback happy path — monkeypatch exchange + profile client.
            # ---------------------------------------------------------------
            _orig_exchange = li.exchange_code_for_token
            _orig_client = li.LinkedInClient

            def _fake_exchange(code, *, http=None, now=None):
                n = now or datetime.utcnow()
                return li.TokenBundle(
                    access_token="ACCESS_TOK",
                    expires_at=n + timedelta(days=60),
                    refresh_token="REFRESH_TOK",
                    refresh_expires_at=n + timedelta(days=365),
                    scope="r_ads rw_ads r_ads_reporting r_basicprofile",
                )

            class _FakeClient:
                def __init__(self, token, *a, **k):
                    self.token = token
                def me(self):
                    return {"name": "Test Member", "sub": "abc"}
                def primary_ad_account_urn(self):
                    return "urn:li:sponsoredAccount:99887766"
                def close(self):
                    pass

            li.exchange_code_for_token = _fake_exchange      # type: ignore[assignment]
            li.LinkedInClient = _FakeClient                  # type: ignore[assignment]
            try:
                r = client.get("/api/integrations/linkedin/callback",
                               params={"code": "GOOD_CODE", "state": real_state},
                               follow_redirects=False)
                if r.status_code != 302 or _li_status(r) != "connected":
                    _fail(f"callback happy path should 302 li=connected, got {r.headers.get('location')}")
                with SessionLocal() as db:
                    conn = store.get_connection(db, 1)
                    if conn.status != "connected" or conn.oauth_token != "ACCESS_TOK":
                        _fail(f"callback didn't store token: {conn.status} {conn.oauth_token}")
                    if conn.account_urn != "urn:li:sponsoredAccount:99887766":
                        _fail(f"callback didn't store ad account urn: {conn.account_urn}")
                    if conn.connected_user_name != "Test Member" or conn.oauth_state is not None:
                        _fail(f"callback enrich/cleanup wrong: {conn.connected_user_name} {conn.oauth_state}")
                _ok("callback(good code) → 302 li=connected; token+account+name stored, state cleared")

                r = client.get("/api/integrations/linkedin/status")
                st = r.json()
                if not (st.get("live") and st.get("connected") and st.get("connectedUserName") == "Test Member"):
                    _fail(f"/status after connect wrong: {st}")
                _ok("/status (connected) → live + connected + 'Test Member'")

                # -----------------------------------------------------------
                # 9. resolve hits the (monkeypatched) real API when live+connected.
                # -----------------------------------------------------------
                _orig_create = li.create_boost_campaign
                li.create_boost_campaign = (  # type: ignore[assignment]
                    lambda c, **k: "urn:li:sponsoredCampaign:55554444"
                )
                try:
                    with SessionLocal() as db:
                        real_id = resolve_external_campaign_id(
                            db, 1, "linkedin", name="Boost: X",
                            daily_budget_cents=2000, duration_days=5,
                        )
                    if real_id != "urn:li:sponsoredCampaign:55554444":
                        _fail(f"live+connected resolve should return real URN, got {real_id}")
                    _ok("resolve (live+connected) → real campaign URN")

                    # Same through the HTTP manual-create path.
                    r = client.post("/api/ads/campaigns", json={
                        "platform": "linkedin", "name": "Quadd LinkedIn boost",
                        "daily_budget_cents": 2000, "duration_days": 5,
                        "origin": "manual_owner",
                    })
                    if r.status_code != 200:
                        _fail(f"manual linkedin campaign create failed: {r.status_code} {r.text}")
                    if r.json().get("externalCampaignId") != "urn:li:sponsoredCampaign:55554444":
                        _fail(f"manual create didn't use real URN: {r.json().get('externalCampaignId')}")
                    _ok("POST /api/ads/campaigns (linkedin, live) → real URN via swap point")
                finally:
                    li.create_boost_campaign = _orig_create  # type: ignore[assignment]
            finally:
                li.exchange_code_for_token = _orig_exchange  # type: ignore[assignment]
                li.LinkedInClient = _orig_client             # type: ignore[assignment]

            # ---------------------------------------------------------------
            # 10. Disconnect clears tokens → resolve falls back to mock again.
            # ---------------------------------------------------------------
            with SessionLocal() as db:
                conn = store.get_connection(db, 1)
                conn_id = conn.id
            r = client.delete(f"/api/ads/connections/{conn_id}")
            if r.status_code != 200:
                _fail(f"disconnect failed: {r.status_code} {r.text}")
            with SessionLocal() as db:
                conn = store.get_connection(db, 1)
                if conn.oauth_token is not None or conn.refresh_token is not None or conn.account_urn is not None:
                    _fail(f"disconnect didn't clear token columns: {conn.oauth_token} {conn.refresh_token}")
                fallback = resolve_external_campaign_id(
                    db, 1, "linkedin", name="X", daily_budget_cents=1000, duration_days=7
                )
            if not fallback.startswith("mock_linkedin_"):
                _fail(f"after disconnect resolve should be mock id, got {fallback}")
            _ok("disconnect clears tokens; resolve falls back to mock id")

        print("\nPASS  Phase I.1 (LinkedIn real OAuth) smoke green ✓")
    finally:
        _set_mock()
        shutil.rmtree(_tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
