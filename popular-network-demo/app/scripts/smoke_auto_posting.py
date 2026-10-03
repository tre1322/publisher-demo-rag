"""Phase 5b smoke — approved posts publish themselves through the posting
service (Ayrshare), Tier 2 and up.

Run with:  uv run python -m app.scripts.smoke_auto_posting

The posting service is simulated at the HTTP level (httpx.MockTransport), so
the real client code, headers and request bodies are exercised.

  A. The client: auth headers, profiles, link pages, linked accounts, posting
  B. Who gets it: Tier 1, the demo, and "not switched on yet" never post
  C. Linking: one profile per business, its key encrypted, never exported
  D. Approving queues a post for its planned day (website stays by hand)
  E. Publishing what's due: links back, partial posts, unlinked platforms
  F. Failures say why; Try again; rate limits wait instead of hammering
  G. The dashboard sees it all on each post
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_auto_posting_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"
os.environ["POPULAR_NOTIFY_LOOP"] = "0"
os.environ["POPULAR_AD_SYNC_LOOP"] = "0"
os.environ["ENVIRONMENT"] = "development"

OWNER = ("lee@cafe.example.com", "lee-correct-horse-battery")
SMALL = ("sam@shop.example.com", "sam-correct-horse-battery")
API_KEY = "AYR-TEST-KEY-123"


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


class FakeAyrshare:
    """Just enough of api.ayrshare.com for the client."""

    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.requests: list[dict] = []
        self.next_post: list = []          # queued responses for /post: dict or ("status", code)

    def handler(self, request):
        import httpx

        body = json.loads(request.content or b"{}") if request.content else {}
        self.requests.append({"method": request.method, "path": request.url.path, "headers": dict(request.headers),
                              "body": body})
        if request.headers.get("authorization") != f"Bearer {API_KEY}":
            return httpx.Response(403, json={"status": "error", "message": "API Key not valid"})
        path = request.url.path.replace("/api", "", 1)
        pk = request.headers.get("profile-key")
        if path == "/profiles" and request.method == "POST":
            key = f"PK-{len(self.profiles) + 1:04d}"
            self.profiles[key] = {"title": body["title"], "linked": []}
            return httpx.Response(200, json={"status": "success", "profileKey": key, "refId": f"ref{len(self.profiles)}"})
        if path == "/profiles/link-sessions":
            return httpx.Response(200, json={"status": "success", "url": f"https://profile.ayrshare.com?session=ayr_ls_{pk}",
                                             "sessionId": "s1", "expiresAt": "2026-10-04T00:05:00Z"})
        if path == "/user":
            prof = self.profiles.get(pk) or {"linked": []}
            return httpx.Response(200, json={"activeSocialAccounts": prof["linked"],
                                             "displayNames": [{"platform": p, "username": f"Corner Cafe ({p})",
                                                               "profileUrl": f"https://{p}.example/cafe"} for p in prof["linked"]]})
        if path == "/post":
            if self.next_post:
                nxt = self.next_post.pop(0)
                if isinstance(nxt, tuple):
                    return httpx.Response(nxt[1], json={"status": "error", "message": "Too many requests"})
                return httpx.Response(200, json=nxt)
            plats = body["platforms"]
            return httpx.Response(200, json={"status": "success", "errors": [], "id": "ayr-post-1", "postIds": [
                {"status": "success", "id": f"{p}-123", "platform": p, "postUrl": f"https://{p}.example/posts/123"} for p in plats]})
        return httpx.Response(404, json={"status": "error", "message": "unknown"})


FAKE = FakeAyrshare()


def main() -> None:
    import shutil

    import httpx
    from fastapi.testclient import TestClient

    import app.auto_posting as ap
    from app import posting_service as ps
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)
    os.environ.pop(ps.ENV_KEY, None)
    from cryptography.fernet import Fernet

    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    def fake_client():
        return ps.AyrshareClient(http=httpx.Client(transport=httpx.MockTransport(FAKE.handler)))

    with patch.object(ap, "_client", fake_client), \
         TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner, \
         TestClient(app, follow_redirects=False) as small:
        bootstrap_login(admin)
        _run(admin, owner, small, httpx)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 5b automatic posting smoke green ✓")


def _claim(client, invite, password):
    token = invite["claimUrl"].split("token=", 1)[1]
    r = client.post("/api/auth/invites/claim", json={"token": token, "password": password, "display_name": "x",
                                                         "accept_terms": True})
    check(f"claim {invite['email']}", r.status_code == 200, r.text)


def _run(admin, owner, small, httpx) -> None:  # noqa: C901 — linear smoke
    from sqlalchemy import text

    from app import auto_posting as ap
    from app import posting_service as ps
    from app.data_lifecycle import export_business
    from app.db import SessionLocal, engine
    from app.models import Approval, Business, Post

    def mk(name, owner_name, email, pw, client, tier):
        r = admin.post("/api/admin/businesses", json={"name": name, "owner": owner_name, "owner_email": email,
                                                      "location": "Windom, MN", "tier": tier, "billing_mode": "outside"})
        bid = r.json()["business"]["id"]
        _claim(client, r.json()["invite"], pw)
        return bid

    biz_id = mk("Corner Cafe", "Lee Park", *OWNER, owner, 2)
    small_id = mk("Small Shop", "Sam Lee", *SMALL, small, 1)

    def approve_post(client, bid, platform="fb", planned=None, title="Pumpkin bars are back"):
        with SessionLocal() as db:
            a = Approval(business_id=bid, kind="post", platform=platform, title=title,
                         draft=f"{title}. Stop by this week.", payload_json={"plannedDate": planned} if planned else None)
            db.add(a)
            db.commit()
            aid = a.id
        r = client.post(f"/api/approvals/{aid}/decide", json={"decision": "approve"})
        assert r.status_code == 200, r.text
        return r.json()["post"]["internalId"]

    def post(pid):
        with SessionLocal() as db:
            return db.get(Post, pid)

    # ---- A ------------------------------------------------------------------
    print("\nA. the posting-service client")
    c = ps.AyrshareClient(API_KEY, http=httpx.Client(transport=httpx.MockTransport(FAKE.handler)))
    prof = c.create_profile("Test Co (Amplafai #9)")
    req = FAKE.requests[-1]
    check("A1 create profile: Bearer key, POST /api/profiles {title}", req["path"] == "/api/profiles"
          and req["headers"]["authorization"] == f"Bearer {API_KEY}" and req["body"] == {"title": "Test Co (Amplafai #9)"}
          and prof["profileKey"].startswith("PK-"), req)
    sess = c.link_session(prof["profileKey"], redirect="https://dashboard.amplafai.com/?tab=settings")
    req = FAKE.requests[-1]
    check("A2 link page: acts as the client (Profile-Key), only our three platforms",
          req["headers"]["profile-key"] == prof["profileKey"] and req["body"]["allowedSocial"] == ["facebook", "instagram", "gmb"]
          and sess["url"].startswith("https://profile.ayrshare.com"), req)
    FAKE.profiles[prof["profileKey"]]["linked"] = ["facebook"]
    check("A3 linked accounts are read with names", c.linked_accounts(prof["profileKey"])[0]["name"] == "Corner Cafe (facebook)")
    res = c.publish(prof["profileKey"], text="Hi", platforms=["facebook"], media_urls=["https://img.example/a.jpg"])
    req = FAKE.requests[-1]
    check("A4 publish: post text, platforms, https media; post links come back",
          req["body"] == {"post": "Hi", "platforms": ["facebook"], "mediaUrls": ["https://img.example/a.jpg"]}
          and res["ok"] and res["posts"][0]["url"] == "https://facebook.example/posts/123", (req["body"], res))
    FAKE.next_post.append(("rate", 429))
    try:
        c.publish(prof["profileKey"], text="Hi", platforms=["facebook"])
        code = None
    except ps.PostingError as e:
        code = e.code
    check("A5 a 429 is a PostingError with code 429 (callers back off)", code == 429, code)
    bad = ps.AyrshareClient("wrong-key", http=httpx.Client(transport=httpx.MockTransport(FAKE.handler)))
    try:
        bad.create_profile("x")
        msg = ""
    except ps.PostingError as e:
        msg = str(e)
    check("A6 a bad key says what the service said", "API Key not valid" in msg, msg)

    def boom(request):
        raise httpx.ConnectError("down")

    try:
        ps.AyrshareClient(API_KEY, http=httpx.Client(transport=httpx.MockTransport(boom))).linked_accounts("PK")
        msg = ""
    except ps.PostingError as e:
        msg = str(e)
    check("A7 the service being down is an owner-safe message", msg.startswith("Couldn't reach the posting service"), msg)

    # ---- B ------------------------------------------------------------------
    print("\nB. who gets automatic posting")
    st = owner.get("/api/posting/status").json()
    check("B1 not switched on yet: status says so, nothing linked", st["configured"] is False and st["planAllows"] is True, st)
    r = owner.post("/api/posting/link")
    check("B2 ...and linking explains why it can't yet", r.status_code == 409 and "isn't switched on yet" in r.json()["detail"], r.text)
    pid = approve_post(owner, biz_id)
    check("B3 ...and approving a post doesn't queue anything (copy and paste as today)", post(pid).publish_state is None)
    os.environ[ps.ENV_KEY] = API_KEY
    st = small.get("/api/posting/status").json()
    check("B4 Tier 1 is told it's Tier 2 and up", st["configured"] and st["planAllows"] is False and "Tier 2" in st["planMessage"], st)
    r = small.post("/api/posting/link")
    check("B5 Tier 1 can't link", r.status_code == 403, r.text)
    spid = approve_post(small, small_id)
    check("B6 Tier 1's approved posts stay copy and paste", post(spid).publish_state is None)
    r = small.post(f"/api/posts/{spid}/publish")
    check("B7 ...and can't be force-posted", r.status_code == 409 and "Tier 2" in r.json()["detail"], r.text)
    r = admin.get("/api/posting/status")
    check("B8 the demo account never posts for real", r.json()["planAllows"] is False, r.json())

    # ---- C ------------------------------------------------------------------
    print("\nC. linking accounts")
    n_profiles = len(FAKE.profiles)
    r = owner.post("/api/posting/link")
    check("C1 Tier 2: a link page for the owner", r.status_code == 200 and r.json()["url"].startswith("https://profile.ayrshare.com"), r.text)
    r = owner.post("/api/posting/link")
    check("C2 one profile per business (the second link reuses it)", len(FAKE.profiles) == n_profiles + 1, len(FAKE.profiles))
    with SessionLocal() as db:
        key = db.get(Business, biz_id).posting_profile_key
        exported = export_business(db, biz_id)
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT posting_profile_key FROM businesses WHERE id=:i"), {"i": biz_id}).scalar()
    check("C3 the profile key is encrypted in the database", raw.startswith("enc:v1:") and key.startswith("PK-"), raw[:12])
    check("C4 ...and left out of the owner's data export", "posting_profile_key" not in exported["business"], list(exported["business"])[:5])
    FAKE.profiles[key]["linked"] = ["facebook", "gmb"]
    st = owner.get("/api/posting/status").json()
    check("C5 status lists the linked accounts", [a["platform"] for a in st["linked"]] == ["facebook", "gmb"] and st["hasProfile"], st)

    # ---- D ------------------------------------------------------------------
    print("\nD. approving queues a post")
    now = datetime.utcnow()
    today_pid = approve_post(owner, biz_id, "fb", title="Soup is on")
    p1 = post(today_pid)
    check("D1 a post for today is queued to go out right away", p1.publish_state == "queued" and p1.publish_at <= datetime.utcnow(), (p1.publish_state, p1.publish_at))
    future = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    fut_pid = approve_post(owner, biz_id, "gbp", planned=future, title="Weekend brunch")
    p2 = post(fut_pid)
    check("D2 a planned post waits for its day (around 10am Central)", p2.publish_state == "queued"
          and p2.publish_at.strftime("%Y-%m-%d %H") == f"{future} {ap.POST_HOUR_UTC:02d}", p2.publish_at)
    web_pid = approve_post(owner, biz_id, "web", title="Website note")
    check("D3 website posts stay by hand, and say why", post(web_pid).publish_state == "manual"
          and "website" in post(web_pid).publish_result_json["reason"].lower())

    # ---- E ------------------------------------------------------------------
    print("\nE. publishing what's due")
    meta_pid = approve_post(owner, biz_id, "meta", title="New mugs")
    ig_pid = approve_post(owner, biz_id, "ig", title="Latte art")
    with SessionLocal() as db:
        n = ap.publish_due(db)
    p1 = post(today_pid)
    check("E1 due posts publish; the future one waits", n == 3 and post(fut_pid).publish_state == "queued", n)
    check("E2 a posted post is marked published, with its link",
          p1.publish_state == "posted" and p1.status == "published" and p1.published_at
          and p1.publish_result_json["posts"][0]["url"] == "https://facebook.example/posts/123", p1.publish_result_json)
    sent = [r for r in FAKE.requests if r["path"] == "/api/post"][-2:]
    check("E3 it went out as this business (its Profile-Key), with the approved text",
          all(r["headers"]["profile-key"] == key for r in sent) and any(r["body"]["post"] == "Soup is on. Stop by this week." for r in sent), sent)
    pm = post(meta_pid)
    check("E4 Facebook + Instagram without a photo: Facebook posts, Instagram explains",
          pm.publish_state == "partial" and [x["platform"] for x in pm.publish_result_json["posts"]] == ["facebook"]
          and "photo" in pm.publish_result_json["errors"][0]["message"], pm.publish_result_json)
    pi = post(ig_pid)
    check("E5 Instagram only, no photo: nothing sent, marked to post by hand",
          pi.publish_state == "manual" and pi.status == "approved" and "photo" in pi.publish_result_json["errors"][0]["message"], pi.publish_result_json)
    FAKE.profiles[key]["linked"] = ["gmb"]
    fb2 = approve_post(owner, biz_id, "fb", title="Closed Monday")
    with SessionLocal() as db:
        ap.publish_due(db)
    pf = post(fb2)
    check("E6 a platform that isn't linked: not sent, says how to fix it", pf.publish_state == "manual"
          and "isn't linked" in pf.publish_result_json["errors"][0]["message"], pf.publish_result_json)
    FAKE.profiles[key]["linked"] = ["facebook", "gmb"]

    # ---- F ------------------------------------------------------------------
    print("\nF. failures, Try again, rate limits")
    r = owner.post(f"/api/posts/{fb2}/publish")
    check("F1 Try again after linking: posted", r.status_code == 200 and r.json()["ok"] and post(fb2).publish_state == "posted", r.text)
    fail_pid = approve_post(owner, biz_id, "fb", title="Rejected one")
    FAKE.next_post.append({"status": "error", "errors": [{"action": "post", "status": "error", "code": 110,
                                                         "message": "Facebook rejected the post: duplicate content",
                                                         "platform": "facebook"}], "postIds": [], "id": "x"})
    with SessionLocal() as db:
        ap.publish_due(db)
    pf = post(fail_pid)
    check("F2 a refusal is recorded with the platform's reason; still not published",
          pf.publish_state == "failed" and pf.status == "approved" and "duplicate content" in pf.publish_result_json["errors"][0]["message"],
          pf.publish_result_json)
    rate_pid = approve_post(owner, biz_id, "gbp", title="Rate limited")
    FAKE.next_post.append(("rate", 429))
    before = datetime.utcnow()
    with SessionLocal() as db:
        ap.publish_due(db)
    pr = post(rate_pid)
    check("F3 a rate limit leaves it queued for 30 minutes later (no hammering)", pr.publish_state == "queued"
          and pr.publish_at >= before + timedelta(minutes=29), (pr.publish_state, pr.publish_at))
    r = owner.post(f"/api/posts/{fail_pid}/publish")
    check("F4 Try again on the refused one posts it", r.status_code == 200 and post(fail_pid).publish_state == "posted", r.text)
    r = owner.post(f"/api/posts/{fail_pid}/publish")
    check("F5 a posted post can't be posted twice", r.status_code == 409, r.text)
    n_posts = len([r for r in FAKE.requests if r["path"] == "/api/post"])
    r = owner.post(f"/api/posts/{meta_pid}/publish")
    pm = post(meta_pid)
    check("F5b Try again on a partial post never posts to Facebook twice",
          len([r for r in FAKE.requests if r["path"] == "/api/post"]) == n_posts and pm.publish_state == "partial"
          and [x["platform"] for x in pm.publish_result_json["posts"]] == ["facebook"]
          and pm.publish_result_json["errors"][0].get("retry") is False, (r.text, pm.publish_result_json))
    r = small.post(f"/api/posts/{today_pid}/publish")
    check("F6 another business can't touch it", r.status_code == 404, r.text)
    os.environ.pop(ps.ENV_KEY, None)
    with SessionLocal() as db:
        check("F7 with the key removed, nothing tries to post", ap.publish_due(db) == 0)
    os.environ[ps.ENV_KEY] = API_KEY

    # ---- G ------------------------------------------------------------------
    print("\nG. the dashboard sees it")
    boot = owner.get("/api/bootstrap").json()
    byid = {p["internalId"]: p for p in boot["posts"]}
    check("G1 bootstrap posts carry their posting state", byid[today_pid]["publish"]["state"] == "posted"
          and byid[fut_pid]["publish"]["state"] == "queued" and byid[web_pid]["publish"]["state"] == "manual", byid[today_pid].get("publish"))
    check("G2 copy-and-paste posts carry none", byid.get(pid, {}).get("publish") is None)
    check("G3 plan lists: Tier 1 copies, Tier 2+ posts automatically",
          any("copy" in f.lower() for f in boot["billing"]["plans"][0]["features"])
          and any("publish themselves" in f for f in boot["billing"]["plans"][1]["features"]), boot["billing"]["plans"][1]["features"])


if __name__ == "__main__":
    main()
