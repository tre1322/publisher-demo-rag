"""Phase 5c smoke — Meta campaigns through the Marketing API.

Run with:  uv run python -m app.scripts.smoke_meta

Meta's Graph API is simulated at the HTTP level (httpx.MockTransport), so the
real client, request signing and form payloads are exercised end to end.

  A. The client: signing, versions, errors sorted into owner-safe messages, paging
  B. Payloads: everything created PAUSED, lifetime budget, the area, the post as the ad
  C. Amplafai links a client's ad account in the admin console (and what it refuses)
  D. Through the app: create → Ready to turn on → turn on → pause → restart → cancel
  E. When Meta can't do it on its own, the campaign goes to Amplafai's hand queue
  F. Spend read from Meta's insights; 100% of a cap pauses through the API
  G. Dormant without the server's keys; the demo never touches Meta
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_meta_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"
os.environ["POPULAR_NOTIFY_LOOP"] = "0"
os.environ["POPULAR_AD_SYNC_LOOP"] = "0"
os.environ["ENVIRONMENT"] = "development"

TOKEN = "EAAB-system-user-test-token"
SECRET = "test-app-secret-123"
OWNER = ("ana@hardware.example.com", "ana-correct-horse-battery")
PAGE = "555000111"
OUTBOX: list = []


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def _err(code, message="error", status=400, subcode=None, user_msg=None):
    import httpx

    e = {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "AbCdEf"}
    if subcode:
        e["error_subcode"] = subcode
    if user_msg:
        e["error_user_msg"] = user_msg
    return httpx.Response(status, json={"error": e})


class FakeGraph:
    """Just enough of graph.facebook.com for the Marketing API calls."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.requests: list[dict] = []
        self.bad_proof = 0
        self.accounts = {
            "act_111": {"name": "Main St Hardware Ads", "currency": "USD", "account_status": 1,
                        "timezone_name": "America/Chicago", "spend_cap": "60000"},
            "act_222": {"name": "Maple Leaf Ads", "currency": "CAD", "account_status": 1, "timezone_name": "America/Toronto"},
            "act_333": {"name": "Old Ads", "currency": "USD", "account_status": 2, "timezone_name": "America/Chicago"},
        }
        self.pages = {PAGE: {"name": "Main St Hardware"}}
        self.objects: dict[str, dict] = {}     # id → {kind, ...fields}
        self.seq = 0
        self.fail: dict[str, object] = {}      # step → httpx.Response to return once
        self.insights: list[dict] = []
        self.page_size = 0

    def _new(self, kind, **fields):
        self.seq += 1
        oid = {"campaign": "1202", "adset": "1203", "creative": "1204", "ad": "1205"}[kind] + f"{self.seq:011d}"
        self.objects[oid] = {"kind": kind, **fields}
        return oid

    def children(self, cid, kind):
        if kind == "ad":
            sets = {k for k, v in self.objects.items() if v["kind"] == "adset" and v["campaign_id"] == cid}
            return [k for k, v in self.objects.items() if v["kind"] == "ad" and v["adset_id"] in sets]
        return [k for k, v in self.objects.items() if v["kind"] == kind and v.get("campaign_id") == cid]

    def handler(self, request):

        path = request.url.path
        version, _, rest = path.lstrip("/").partition("/")
        if request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        else:
            form = {k: v for k, v in request.url.params.items()}
        self.requests.append({"method": request.method, "version": version, "path": rest, "form": form})
        token = form.get("access_token")
        if form.get("appsecret_proof") != hmac.new(SECRET.encode(), (token or "").encode(), hashlib.sha256).hexdigest():
            self.bad_proof += 1
        for step, resp in list(self.fail.items()):
            if self._matches(step, request.method, rest, form):
                del self.fail[step]
                return resp
        if token != TOKEN:
            return _err(190, "Invalid OAuth access token.", 401)
        parts = rest.split("/")
        if request.method == "GET":
            return self._get(parts, form)
        return self._post(parts, form)

    def _matches(self, step, method, rest, form):
        if step in ("campaigns", "adsets", "adcreatives", "ads", "insights", "search"):
            return rest.endswith("/" + step) or rest == step
        if step in ("PAUSED", "ACTIVE", "ARCHIVED"):
            return method == "POST" and form.get("status") == step and self.objects.get(rest, {}).get("kind") == "campaign"
        return False

    def _get(self, parts, form):
        import httpx

        if parts == ["search"]:
            if form.get("type") != "adgeolocation":
                return _err(100, "bad search type")
            towns = {"windom": [
                {"key": "2420001", "name": "Windom", "type": "city", "region": "Kansas", "country_code": "US"},
                {"key": "2490299", "name": "Windom", "type": "city", "region": "Minnesota", "country_code": "US"}]}
            return httpx.Response(200, json={"data": towns.get(form.get("q", "").lower(), [])})
        oid = parts[0]
        if len(parts) == 1:
            if oid in self.accounts:
                return httpx.Response(200, json={"id": oid, **self.accounts[oid]})
            if oid in self.pages:
                return httpx.Response(200, json={"id": oid, **self.pages[oid]})
            return _err(100, f"Unsupported get request. Object with ID '{oid}' does not exist, cannot be loaded due "
                             "to missing permissions, or does not support this operation.", subcode=33)
        edge = parts[1]
        if edge in ("adsets", "ads"):
            ids = self.children(oid, edge[:-1] if edge == "ads" else "adset")
            return httpx.Response(200, json={"data": [{"id": i, "status": self.objects[i]["status"],
                                                       **({"end_time": self.objects[i].get("end_time")} if edge == "adsets" else {})}
                                                      for i in ids]})
        if edge == "insights":
            want = set(json.loads(form["filtering"])[0]["value"]) if "filtering" in form else {oid}
            span = json.loads(form["time_range"])
            rows = [r for r in self.insights if r["campaign_id"] in want and span["since"] <= r["date_start"] <= span["until"]]
            if self.page_size:
                start = int(form.get("after") or 0)
                chunk = rows[start:start + self.page_size]
                more = start + self.page_size < len(rows)
                body = {"data": chunk}
                if more:
                    body["paging"] = {"cursors": {"after": str(start + self.page_size)}, "next": "https://graph.facebook.com/next"}
                return httpx.Response(200, json=body)
            return httpx.Response(200, json={"data": rows})
        return _err(100, "unknown edge")

    def _post(self, parts, form):
        import httpx

        if len(parts) == 2 and parts[0].startswith("act_"):
            edge = parts[1]
            if parts[0] not in self.accounts:
                return _err(200, "Ad account owner has not granted ads_management permission")
            if edge == "campaigns":
                return httpx.Response(200, json={"id": self._new("campaign", **form)})
            if edge == "adsets":
                if form.get("campaign_id") not in self.objects:
                    return _err(100, "Invalid campaign id")
                return httpx.Response(200, json={"id": self._new("adset", **form)})
            if edge == "adcreatives":
                return httpx.Response(200, json={"id": self._new("creative", **form)})
            if edge == "ads":
                if json.loads(form["creative"])["creative_id"] not in self.objects:
                    return _err(100, "Invalid creative")
                return httpx.Response(200, json={"id": self._new("ad", **form)})
        oid = parts[0]
        if len(parts) == 1 and oid in self.objects:
            self.objects[oid].update({k: v for k, v in form.items() if k not in ("access_token", "appsecret_proof")})
            return httpx.Response(200, json={"success": True})
        return _err(100, "unknown object")

    def posts(self, kind=None, status=None):
        return [r for r in self.requests if r["method"] == "POST"
                and (kind is None or r["path"].endswith("/" + kind) or self.objects.get(r["path"], {}).get("kind") == kind)
                and (status is None or r["form"].get("status") == status)]


G = FakeGraph()


def main() -> None:
    import shutil

    import httpx
    from fastapi.testclient import TestClient

    import app.managed_ads as ma
    from app.integrations.meta import client as meta_client
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    for k in ("POSTMARK_API_KEY", "META_ACCESS_TOKEN", "META_APP_SECRET", "META_GRAPH_VERSION", "STRIPE_SECRET_KEY"):
        os.environ.pop(k, None)
    from cryptography.fernet import Fernet

    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    meta_client.TRANSPORT = httpx.MockTransport(G.handler)
    with patch.object(ma, "dispatch", OUTBOX.extend), \
         TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner:
        bootstrap_login(admin)
        _client_checks()
        _payload_checks()
        _run(admin, owner)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 5c Meta smoke green ✓")


def _live(on=True):
    if on:
        os.environ["META_ACCESS_TOKEN"], os.environ["META_APP_SECRET"] = TOKEN, SECRET
    else:
        os.environ.pop("META_ACCESS_TOKEN", None)
        os.environ.pop("META_APP_SECRET", None)


def _client_checks() -> None:
    from app.integrations import meta

    print("A. The client")
    _live(False)
    check("A1 dormant without the keys", meta.is_live() is False)
    try:
        meta.MetaClient()
        check("A1b no client without the keys", False)
    except meta.MetaNotConfigured:
        check("A1b no client without the keys", True)
    os.environ["META_ACCESS_TOKEN"] = TOKEN
    check("A1c the token alone isn't enough (the app secret signs every call)", meta.is_live() is False)
    _live()
    check("A2 live with both", meta.is_live())

    G.reset()
    with meta.MetaClient() as c:
        acct = meta.read_account(c, "act_111")
    r = G.requests[-1]
    check("A3 reads the account", acct["name"] == "Main St Hardware Ads" and acct["currency"] == "USD" and acct["status"] == 1, acct)
    check("A3b default Graph version", r["version"] == meta.DEFAULT_VERSION, r["version"])
    check("A3c every call signed with appsecret_proof", G.bad_proof == 0 and r["form"].get("appsecret_proof"))
    os.environ["META_GRAPH_VERSION"] = "v27.0"
    with meta.MetaClient() as c:
        meta.read_page(c, PAGE)
    check("A3d version can be moved without a code change", G.requests[-1]["version"] == "v27.0")
    os.environ.pop("META_GRAPH_VERSION")

    cases = [(190, None, "auth", "renewed"), (17, None, "rate", "few minutes"), (80004, None, "rate", "few minutes"),
             (200, None, "permission", "partner"), (100, "The budget is too low for this ad set.", "other", "budget is too low")]
    for code, user_msg, kind, words in cases:
        G.fail["search"] = _err(code, "raw technical message", user_msg=user_msg)
        try:
            with meta.MetaClient() as c:
                meta.find_city(c, "Windom, MN")
            check(f"A4 error {code} raised", False)
        except meta.MetaAPIError as e:
            check(f"A4 error {code} → {kind}, owner-safe message", e.kind == kind and words in str(e), (e.kind, str(e)))

    import httpx

    def boom(request):
        raise httpx.ConnectError("down")

    c = meta.MetaClient(http=httpx.Client(transport=httpx.MockTransport(boom)))
    try:
        meta.read_page(c, PAGE)
        check("A5 network failure raised", False)
    except meta.MetaError as e:
        check("A5 network failure → 'Couldn't reach Meta'", "Couldn't reach Meta" in str(e), str(e))

    G.reset()
    G.insights = [{"campaign_id": "9", "date_start": f"2026-10-0{d}", "spend": "1.00", "impressions": "10", "clicks": "1"}
                  for d in range(1, 6)]
    G.page_size = 2
    with meta.MetaClient() as c:
        rows = meta.daily_results(c, "act_111", ["9"], date(2026, 10, 1), date(2026, 10, 7))
    check("A6 follows the paging cursor to the last page", len(rows) == 5 and len([r for r in G.requests if "insights" in r["path"]]) == 3,
          len(rows))
    G.reset()
    G.insights = [{"campaign_id": c, "date_start": "2026-10-01", "spend": "2.50", "impressions": "10", "clicks": "1"}
                  for c in ("7", "8")]
    G.fail["insights"] = _err(100, "(#100) Filtering field campaign.id is invalid")
    with meta.MetaClient() as c:
        rows = meta.daily_results(c, "act_111", ["7", "8"], date(2026, 10, 1), date(2026, 10, 2))
    paths = [r["path"] for r in G.requests if "insights" in r["path"]]
    check("A6b if Meta refuses the filter, it asks campaign by campaign", sorted(r["campaign_id"] for r in rows) == ["7", "8"]
          and paths[1:] == ["7/insights", "8/insights"], (rows, paths))

    print("   money and places")
    check("A7 spend '12.345' → 1235 cents (Decimal, invoice rounding)", meta.money_to_cents("12.345") == 1235)
    check("A7b spend '0.1'+'0.2' style values stay exact", meta.money_to_cents("0.30") == 30 and meta.money_to_cents(None) == 0)
    check("A8 'Windom, MN' → Windom, Minnesota", meta.split_location("Windom, MN") == ("Windom", "Minnesota"))
    check("A8b ZIP after the state is ignored", meta.split_location("Windom, Minnesota 56101") == ("Windom", "Minnesota"))
    G.reset()
    with meta.MetaClient() as c:
        city = meta.find_city(c, "Windom, MN")
        none = meta.find_city(c, "Nowhereville, MN")
    check("A9 picks the Minnesota Windom, not the Kansas one", city and city["key"] == "2490299", city)
    check("A9b an unknown town → None", none is None)
    check("A10 radius from the audience hint", meta.radius_from_hint("Homeowners within 20 miles", 15) == 20)
    check("A10b kept inside Meta's 10–50 miles", meta.radius_from_hint("within 5 mi", 15) == 10
          and meta.radius_from_hint("within 80 miles", 15) == 50 and meta.radius_from_hint(None, 15) == 15)


def _payload_checks() -> None:
    from app.integrations import meta

    print("B. Payloads")
    _live()
    G.reset()
    now = datetime(2026, 10, 5, 15, 0, 0)
    spec = meta.targeting({"key": "2490299", "name": "Windom", "region": "Minnesota", "radiusMiles": 15},
                          audience="Homeowners within 20 miles", instagram=False)
    with meta.MetaClient() as c:
        cid = meta.create_paused(c, "act_111", name="Fall paint sale", lifetime_budget_cents=14000, days=7,
                                 targeting_spec=spec, page_id=PAGE, story_id=f"{PAGE}_777", now=now)
    camp = G.objects[cid]
    adset = next(v for v in G.objects.values() if v["kind"] == "adset")
    creative = next(v for v in G.objects.values() if v["kind"] == "creative")
    ad = next(v for v in G.objects.values() if v["kind"] == "ad")
    check("B1 campaign created PAUSED", camp["status"] == "PAUSED", camp)
    check("B1b objective and no special ad category", camp["objective"] == "OUTCOME_AWARENESS"
          and json.loads(camp["special_ad_categories"]) == [], camp)
    check("B1c budget lives on the ad set, not shared (v24+ requires the flag)", camp.get("is_adset_budget_sharing_enabled") == "0", camp)
    check("B2 ad set PAUSED", adset["status"] == "PAUSED")
    check("B2b lifetime budget = the campaign's total, in cents", adset["lifetime_budget"] == "14000" and "daily_budget" not in adset)
    start = datetime.strptime(adset["start_time"], "%Y-%m-%dT%H:%M:%S+0000")
    end = datetime.strptime(adset["end_time"], "%Y-%m-%dT%H:%M:%S+0000")
    check("B2c has an end time (days after the start)", end - start == timedelta(days=7), (start, end))
    t = json.loads(adset["targeting"])
    city = t["geo_locations"]["cities"][0]
    check("B2d the town, with the hint's 20-mile radius", city == {"key": "2490299", "radius": 20, "distance_unit": "mile"}, city)
    check("B2e Facebook only without an Instagram account", t["publisher_platforms"] == ["facebook"])
    check("B2f Meta doesn't widen the area on its own", t["targeting_automation"] == {"advantage_audience": 0})
    check("B3 the ad is the owner's own post", creative["object_story_id"] == f"{PAGE}_777")
    check("B3b ad PAUSED, pointing at that creative", ad["status"] == "PAUSED" and json.loads(ad["creative"])["creative_id"] in G.objects)

    spec_ig = meta.targeting({"zip": "56101"}, audience=None, instagram=True)
    check("B4 ZIP targeting", spec_ig["geo_locations"] == {"zips": [{"key": "US:56101"}]})
    check("B4b Instagram too when linked", spec_ig["publisher_platforms"] == ["facebook", "instagram"])

    G.fail["adsets"] = _err(100, "Invalid parameter", user_msg="Your budget is too low.")
    before = {k for k, v in G.objects.items() if v["kind"] == "campaign"}
    try:
        with meta.MetaClient() as c:
            meta.create_paused(c, "act_111", name="Too small", lifetime_budget_cents=50, days=7,
                               targeting_spec=spec, page_id=PAGE, story_id=f"{PAGE}_778", now=now)
        check("B5 a failed ad set raises", False)
    except meta.MetaAPIError as e:
        new = [k for k, v in G.objects.items() if v["kind"] == "campaign" and k not in before]
        check("B5 a failed ad set raises with Meta's own words", "budget is too low" in str(e), str(e))
        check("B5b and the half-built campaign is deleted", len(new) == 1 and G.objects[new[0]]["status"] == "DELETED",
              [G.objects[k]["status"] for k in new])

    G.requests.clear()
    with meta.MetaClient() as c:
        meta.activate(c, cid, ends_at=now + timedelta(days=7), now=now)
    posts = [r for r in G.requests if r["method"] == "POST"]
    check("B6 turn on: ad set gets the real end time", G.objects[next(iter(G.children(cid, "adset")))]["end_time"]
          == "2026-10-12T15:00:00+0000")
    check("B6b ad set, ad and campaign ACTIVE", all(G.objects[i]["status"] == "ACTIVE" for i in
                                                     [cid, *G.children(cid, "adset"), *G.children(cid, "ad")]))
    check("B6c the campaign goes ACTIVE last", posts[-1]["path"] == cid and posts[-1]["form"]["status"] == "ACTIVE",
          [p["path"] for p in posts])
    try:
        with meta.MetaClient() as c:
            meta.activate(c, cid, ends_at=now - timedelta(hours=2), now=now)
        check("B7 can't restart past its end", False)
    except meta.MetaError as e:
        check("B7 can't restart past its end", "end date has passed" in str(e), str(e))
    bare = G._new("campaign", status="PAUSED")
    try:
        with meta.MetaClient() as c:
            meta.activate(c, bare, ends_at=now + timedelta(days=3), now=now)
        check("B8 no ad → won't turn on", False)
    except meta.MetaError as e:
        check("B8 no ad → won't turn on, says why", "no ad on Meta yet" in str(e) and G.objects[bare]["status"] == "PAUSED", str(e))


def _claim(client, invite, password):
    token = invite["claimUrl"].split("token=", 1)[1]
    r = client.post("/api/auth/invites/claim", json={"token": token, "password": password, "display_name": "x",
                                                         "accept_terms": True})
    check(f"claim {invite['email']}", r.status_code == 200, r.text)


def _run(admin, owner) -> None:  # noqa: C901 — linear smoke
    from app import ad_platforms, ad_sync
    from app.db import SessionLocal, current_tenant_id
    from app.models import AdActionLog, AdConnection, AdOpsRequest, Business, Post

    print("C. Linking a client's ad account")
    G.reset()
    r = admin.post("/api/admin/businesses", json={"name": "Main St Hardware", "owner": "Ana Ruiz", "owner_email": OWNER[0],
                                                  "location": "Windom, MN", "tier": 3, "billing_mode": "outside"})
    biz_id = r.json()["business"]["id"]
    _claim(owner, r.json()["invite"], OWNER[1])
    url = f"/api/admin/businesses/{biz_id}/ads/meta"
    body = {"ad_account_id": "act_111", "page_id": PAGE}

    _live(False)
    row = next(b for b in admin.get("/api/admin/businesses").json()["businesses"] if b["id"] == biz_id)
    check("C1 the card shows Meta isn't switched on", row["adsMeta"] == {"live": False, "link": None}, row["adsMeta"])
    r = admin.post(url, json=body)
    check("C1b linking refused until the server has the keys", r.status_code == 409 and "META_ACCESS_TOKEN" in r.text, r.text)
    _live()
    r = owner.post(url, json=body)
    check("C2 owners can't use the admin link", r.status_code == 403, r.status_code)
    r = admin.post("/api/admin/businesses/1/ads/meta", json=body)
    check("C3 the demo account can't be linked", r.status_code == 409 and "Demo" in r.text, r.text)
    r = admin.post(url, json={**body, "ad_account_id": "act_222"})
    check("C4 a non-USD account is refused", r.status_code == 409 and "CAD" in r.text, r.text)
    r = admin.post(url, json={**body, "ad_account_id": "act_333"})
    check("C5 a disabled account is refused", r.status_code == 409 and "disabled" in r.text, r.text)
    r = admin.post(url, json={**body, "ad_account_id": "act_999"})
    check("C6 no partner access → says to check the partner setup", r.status_code == 409 and "partner" in r.text, r.text)
    r = admin.post(url, json={**body, "ad_account_id": "12ab"})
    check("C7 a mistyped ID is caught before calling Meta", r.status_code == 422, r.text)
    with SessionLocal() as db:
        b = db.get(Business, biz_id)
        b.location = "Nowhereville, MN"
        db.commit()
    r = admin.post(url, json=body)
    check("C8 a town Meta doesn't know → asks for the ZIP", r.status_code == 422 and "ZIP" in r.text, r.text)
    with SessionLocal() as db:
        db.get(Business, biz_id).location = "Windom, MN"
        db.commit()
    calls_before = len(G.requests)
    r = admin.post(url, json={**body, "ad_account_id": "act_111", "radius_miles": 25})
    check("C9 links", r.status_code == 200, r.text)
    link = r.json()["business"]["adsMeta"]["link"]
    check("C9b account, Page and area stored", link["accountId"] == "act_111" and link["pageName"] == "Main St Hardware"
          and link["geo"] == {"key": "2490299", "name": "Windom", "region": "Minnesota", "radiusMiles": 25}, link)
    check("C9c checked with Meta (account, Page, town)", len(G.requests) - calls_before == 3)
    check("C9f the account's spending limit is shown to staff", link["spendCapCents"] == 60000, link)
    with SessionLocal() as db:
        conn = db.query(AdConnection).filter_by(business_id=biz_id, platform="fb_ig").one()
        check("C9d no per-client token stored", conn.oauth_token is None and conn.status == "connected")
        log = db.query(AdActionLog).filter_by(business_id=biz_id, action="link").all()
        check("C9e logged", len(log) == 1 and "act_111" in log[0].detail, [x.detail for x in log])
    conns = owner.get("/api/ads").json()["connections"]
    meta_conn = next(c for c in conns if c["platform"] == "fb_ig")
    check("C10 the owner sees it linked through the API", meta_conn["viaApi"] and meta_conn["status"] == "connected", meta_conn)

    print("D. Through the app")
    with SessionLocal() as db:
        token = current_tenant_id.set(biz_id)
        posts = []
        for i, result in enumerate([{"posts": [{"platform": "facebook", "id": f"{PAGE}_9001", "url": "https://fb/9001"}]},
                                    None,
                                    {"posts": [{"platform": "facebook", "id": "444_9002", "url": "https://fb/9002"}]}]):
            p = Post(business_id=biz_id, date="2026-10-05", platform="fb", status="published", title=f"Paint sale {i}",
                     draft="20% off paint", publish_state="posted" if result else None, publish_result_json=result)
            db.add(p)
            posts.append(p)
        db.commit()
        post_ids = [p.id for p in posts]
        current_tenant_id.reset(token)

    def camp(cid):
        return next(c for c in owner.get("/api/ads").json()["campaigns"] if c["id"] == cid)

    def create(post_id, name="Fall paint sale"):
        return owner.post("/api/ads/campaigns", json={"platform": "fb_ig", "name": name, "daily_budget_cents": 2000,
                                                      "duration_days": 7, "post_id": post_id,
                                                      "target_audience": "Homeowners within 20 miles"})

    G.requests.clear()
    r = create(post_ids[0])
    check("D1 create", r.status_code == 200, r.text)
    c1 = r.json()
    ext = c1["externalCampaignId"]
    check("D1b created on Meta, PAUSED", ext in G.objects and G.objects[ext]["status"] == "PAUSED", c1)
    check("D1c owner sees 'Ready to turn on', no hand request", c1["stage"] == "ready_to_turn_on" and not c1["openRequest"], c1)
    adset = G.objects[G.children(ext, "adset")[0]]
    check("D1d lifetime budget $140 = $20 × 7", adset["lifetime_budget"] == "14000")
    t = json.loads(adset["targeting"])
    check("D1e the linked town with the hint's radius", t["geo_locations"]["cities"][0]["radius"] == 20, t)
    creative = next(v for v in G.objects.values() if v["kind"] == "creative" and v["object_story_id"] == f"{PAGE}_9001")
    check("D1f the ad is the owner's published post", creative is not None)
    check("D1g every call signed", G.bad_proof == 0)

    r = owner.post(f"/api/ads/campaigns/{c1['id']}/turn-on")
    check("D2 turn on", r.status_code == 200 and r.json()["status"] == "active", r.text)
    check("D2b live on Meta", G.objects[ext]["status"] == "ACTIVE")
    end = datetime.strptime(adset["end_time"], "%Y-%m-%dT%H:%M:%S+0000")
    check("D2c ends 7 days from turning on", abs((end - datetime.utcnow()) - timedelta(days=7)) < timedelta(minutes=5), end)

    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "paused"})
    check("D3 pause goes straight to Meta", r.status_code == 200 and G.objects[ext]["status"] == "PAUSED"
          and camp(c1["id"])["status"] == "paused", r.text)
    end_before = adset["end_time"]
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
    check("D4 restart goes straight to Meta", r.status_code == 200 and G.objects[ext]["status"] == "ACTIVE", r.text)
    check("D4b restarting keeps the original end date", adset["end_time"] == end_before, (adset["end_time"], end_before))

    G.fail["PAUSED"] = _err(17, "User request limit reached")
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "paused"})
    cc = camp(c1["id"])
    check("D5 Meta busy → becomes a pause request for Amplafai", r.status_code == 200 and cc["stage"] == "pause_requested"
          and "Meta" in r.json().get("message", ""), (r.text, cc["stage"]))
    check("D5b and it's still running on Meta (nothing pretends)", G.objects[ext]["status"] == "ACTIVE")
    with SessionLocal() as db:
        bad = db.query(AdActionLog).filter_by(business_id=biz_id, action="pause", ok=False).all()
        check("D5c the refusal is logged", len(bad) == 1 and "few minutes" in bad[-1].detail, [b.detail for b in bad])
        req = db.query(AdOpsRequest).filter_by(campaign_id=c1["id"], kind="pause").one()
    r = admin.post(f"/api/admin/ads/requests/{req.id}/done", json={"note": "Paused in Ads Manager."})
    check("D5d Amplafai closes it by hand", r.status_code == 200, r.text)
    G.objects[ext]["status"] = "PAUSED"

    print("E. When Meta can't do it on its own")
    r = create(post_ids[1], name="No post yet")
    c2 = r.json()
    check("E1 post not on Facebook → Amplafai launches it by hand", r.status_code == 200 and c2["externalCampaignId"] is None
          and c2["stage"] == "waiting_for_launch", c2)
    r = create(post_ids[2], name="Other page")
    c3 = r.json()
    check("E2 post from a different Page → by hand too", r.status_code == 200 and c3["stage"] == "waiting_for_launch", c3)
    r = create(None, name="No post at all")
    check("E3 no post → by hand", r.status_code == 200 and r.json()["stage"] == "waiting_for_launch", r.text)
    with SessionLocal() as db:
        notes = [x.detail for x in db.query(AdActionLog).filter_by(business_id=biz_id, action="create_paused").all()]
    check("E4 each logged with the reason", any("isn't on Facebook yet" in n for n in notes)
          and any("different Facebook Page" in n for n in notes), notes)

    G.fail["adsets"] = _err(100, "Invalid", user_msg="Your budget is too low.")
    n_before = len([1 for v in G.objects.values() if v["kind"] == "campaign"])
    r = create(post_ids[0], name="Rejected")
    check("E5 Meta refuses the build → the owner sees Meta's reason, nothing is saved",
          r.status_code == 502 and "budget is too low" in r.text, r.text)
    left = [k for k, v in G.objects.items() if v["kind"] == "campaign"][n_before:]
    check("E5b nothing left behind on the client's account", all(G.objects[k]["status"] == "DELETED" for k in left), left)

    r = create(post_ids[0], name="Token test")
    c4 = r.json()
    os.environ["META_ACCESS_TOKEN"] = "EAAB-revoked"
    r = owner.post(f"/api/ads/campaigns/{c4['id']}/turn-on")
    check("E6 a refused token → 502 that says the connection needs renewing", r.status_code == 502 and "renewed" in r.text, r.text)
    check("E6b still paused", camp(c4["id"])["stage"] == "ready_to_turn_on")
    _live()

    print("F. Spend from Meta")
    today = datetime.utcnow().date()
    d1, d2 = (today - timedelta(days=2)).isoformat(), (today - timedelta(days=1)).isoformat()
    G.insights = [{"campaign_id": ext, "date_start": d1, "spend": "19.996", "impressions": "1500", "clicks": "30"},
                  {"campaign_id": ext, "date_start": d2, "spend": "20.004", "impressions": "1400", "clicks": "25"}]
    with SessionLocal() as db:
        done = ad_sync.run_all(db)
    check("F1 the scheduled sync reads Meta", biz_id in done and done[biz_id]["platforms"]["fb_ig"]["ok"], done)
    cc = camp(c1["id"])
    check("F1b campaign spend to the cent ($20.00 + $20.00)", cc["actualSpendCents"] == 4000, cc["actualSpendCents"])
    ins = [r for r in G.requests if r["path"].endswith("/insights")][-1]["form"]
    check("F1c asks per campaign, per day", ins["level"] == "campaign" and ins["time_increment"] == "1", ins)
    ads = owner.get("/api/ads").json()
    src = ads["lastImports"].get("fb_ig") or {}
    check("F1d the owner sees the numbers came from Meta's API", src.get("viaApi") and src.get("source") == "Meta API"
          and src.get("through") == d2, src)

    # The cap sits $10 above this month's spend so far (the two days above may
    # fall in last month when this runs on the 1st or 2nd); today's $12 crosses it.
    month_spend = next((b["spendCents"] for b in ads["budgets"] if b["platform"] == "fb_ig"), 0)
    r = owner.put("/api/ads/budgets/fb_ig", json={"monthly_cap_cents": month_spend + 1000})
    check("F2 owner sets a cap", r.status_code == 200, r.text)
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
    check("F2b restarted through the API", r.status_code == 200 and G.objects[ext]["status"] == "ACTIVE"
          and camp(c1["id"])["status"] == "active", r.text)
    G.insights.append({"campaign_id": ext, "date_start": today.isoformat(), "spend": "12.00", "impressions": "900", "clicks": "9"})
    with SessionLocal() as db:
        done = ad_sync.run_all(db)
    check("F3 over the cap → paused on Meta through the API", G.objects[ext]["status"] == "PAUSED"
          and camp(c1["id"])["status"] == "paused", (G.objects[ext]["status"], camp(c1["id"])["status"]))
    check("F3b owner and Amplafai alerted at 100%", any(a.get("level") == 100 for a in done[biz_id]["alerts"]), done[biz_id]["alerts"])

    r = owner.delete(f"/api/ads/campaigns/{c1['id']}")
    check("F4 cancel archives it on Meta", r.status_code == 200 and G.objects[ext]["status"] == "ARCHIVED", r.text)

    print("G. Halt, dormancy and the demo")
    r = create(post_ids[0], name="Halt test")
    c5 = r.json()
    owner.post(f"/api/ads/campaigns/{c5['id']}/turn-on")
    ext5 = c5["externalCampaignId"]
    check("G1 running", G.objects[ext5]["status"] == "ACTIVE")
    r = owner.post("/api/ads/halt")
    check("G2 'Pause all paid ads' pauses it on Meta", r.status_code == 200 and G.objects[ext5]["status"] == "PAUSED", r.text)
    owner.post("/api/ads/allow")

    _live(False)
    with SessionLocal() as db:
        check("G3 without the keys no adapter, even though the account is linked",
              ad_platforms.adapter_for(db, biz_id, "fb_ig") is None)
    r = create(post_ids[0], name="Dormant")
    check("G3b campaigns go to the hand queue", r.status_code == 200 and r.json()["stage"] == "waiting_for_launch", r.text)
    _live()
    with SessionLocal() as db:
        check("G4 the demo's mock Meta connection never gets the real API", ad_platforms.adapter_for(db, 1, "fb_ig") is None)

    conn_id = next(c for c in owner.get("/api/ads").json()["connections"] if c["platform"] == "fb_ig")["id"]
    r = owner.delete(f"/api/ads/connections/{conn_id}")
    check("G5 the owner can disconnect it", r.status_code == 200, r.text)
    with SessionLocal() as db:
        check("G5b then nothing goes through the API", ad_platforms.adapter_for(db, biz_id, "fb_ig") is None)
        log = db.query(AdActionLog).filter_by(business_id=biz_id, action="unlink").all()
        check("G5c and it's logged", len(log) == 1 and "act_111" in log[0].detail, [x.detail for x in log])
    row = next(b for b in admin.get("/api/admin/businesses").json()["businesses"] if b["id"] == biz_id)
    check("G6 the admin card shows it unlinked", row["adsMeta"] == {"live": True, "link": None}, row["adsMeta"])
    r = admin.delete(url)
    check("G6b unlinking twice says so", r.status_code == 409, r.text)
    check("G7 every call signed with appsecret_proof", G.bad_proof == 0, G.bad_proof)


if __name__ == "__main__":
    main()
