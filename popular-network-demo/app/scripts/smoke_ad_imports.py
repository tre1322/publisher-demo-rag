"""Phase 4b smoke — real spend comes from the platforms' own exports, to the
cent, and crossing a monthly cap emails the owner and Amplafai.

Run with:  uv run python -m app.scripts.smoke_ad_imports

The sample files below follow each platform's export layout (Meta Ads
Manager, Google Ads' UTF-16 "Excel" download, LinkedIn Campaign Manager).

  A. Reading the files: columns by meaning, preambles, totals rows, encodings
  B. Files that can't be used say what to do (no day breakdown, not USD)
  C. Preview changes nothing; import matches by platform ID, to the cent
  D. Re-importing replaces days instead of double-counting; months split
  E. Unmatched and ambiguous rows are reported, never guessed
  F. Cap alerts at 80% and 100%, once each; at 100% Amplafai is asked to pause
  G. The owner sees where the numbers came from
  H. Admin only; the demo account keeps its simulated spend
"""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_ad_imports_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"
os.environ["POPULAR_NOTIFY_LOOP"] = "0"
os.environ["ENVIRONMENT"] = "development"

OWNER = ("ana@hardware.example.com", "ana-correct-horse-battery")
OPS = "ops@amplafai.example.com"
OUTBOX: list[dict] = []
SENT: list[dict] = []
MONTH = datetime.utcnow().strftime("%Y-%m")
PREV = (datetime.utcnow().replace(day=1).toordinal() - 1)
PREV_MONTH = datetime.fromordinal(PREV).strftime("%Y-%m")
D1, D2, D3 = f"{MONTH}-01", f"{MONTH}-02", f"{MONTH}-03"
P30 = datetime.fromordinal(PREV).strftime("%Y-%m-%d")   # last day of last month

META_ID, META_ID2, GOOGLE_ID, LI_ID = "120210000000000001", "120210000000000002", "21456789012", "312345678"


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def capture_send(to_email, subject, html_body, text_body, *, kind):
    SENT.append({"to": to_email, "subject": subject, "text": text_body, "kind": kind})
    return {"sent": True}


def meta_csv(rows, by_day=True) -> bytes:
    head = "Reporting starts,Reporting ends,Campaign name,Campaign ID," + ("Day," if by_day else "") \
        + "Amount spent (USD),Impressions,Link clicks,Clicks (all)\n"
    body = "".join(
        f"{r[1] if by_day else r[1]},{r[1] if by_day else r[4]},\"{r[0]}\",{r[5] if len(r) > 5 else META_ID},"
        + (f"{r[1]}," if by_day else "") + f"{r[2]},{r[3]},{r[3] // 50},{r[3] // 20}\n"
        for r in rows)
    return b"\xef\xbb\xbf" + (head + body).encode("utf-8")


def google_csv() -> bytes:
    lines = [
        "Campaign report",
        f"{D1} - {D2}",
        "\t".join(["Campaign status", "Campaign", "Campaign ID", "Day", "Currency code", "Cost", "Impr.", "Clicks"]),
        "\t".join(["Enabled", "Contractor night", GOOGLE_ID, D1, "USD", "1,033.33", "12,345", "321"]),
        "\t".join(["Enabled", "Contractor night", GOOGLE_ID, D2, "USD", "0.01", "1", "--"]),
        "\t".join(["Enabled", "Someone else's campaign", "999", D2, "USD", "5.00", "10", "1"]),
        "\t".join(["Total: Campaigns", "", "", "", "USD", "1,038.34", "12,356", "322"]),
        "\t".join(["Total: Account", "", "", "", "USD", "1,038.34", "12,356", "322"]),
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-16")


def linkedin_csv(cur="USD") -> bytes:
    def us(d):  # LinkedIn writes M/D/YYYY
        y, m, dd = d.split("-")
        return f"{int(m)}/{int(dd)}/{y}"
    lines = [
        "Campaign Performance Report (in UTC)",
        f"Report Start: {us(D1)}",
        "",
        "Start Date (in UTC),Account Name,Campaign Group Name,Campaign Name,Campaign ID,Currency,Total Spent,Impressions,Clicks",
        f"{us(D1)},Main St Hardware,Default,B2B contractors,{LI_ID},{cur},33.33,1000,4",
        f"{us(D2)},Main St Hardware,Default,B2B contractors,{LI_ID},{cur},33.33,1100,5",
        f"{us(D3)},Main St Hardware,Default,B2B contractors,{LI_ID},{cur},33.34,1200,6",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    import app.managed_ads as ma
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)
    os.environ["ADS_OPS_EMAIL"] = OPS
    with patch.object(ma, "dispatch", OUTBOX.extend), \
         TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner:
        bootstrap_login(admin)
        _run(admin, owner)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 4b ad imports smoke green ✓")


def _run(admin, owner) -> None:  # noqa: C901 — linear smoke
    import app.email as mailer
    import app.managed_ads as ma
    from app import ad_imports as ai
    from app.db import SessionLocal
    from app.models import AdImport, AdOpsRequest, AdSpendDay

    r = admin.post("/api/admin/businesses", json={"name": "Main St Hardware", "owner": "Ana Ruiz",
                                                  "owner_email": OWNER[0], "location": "Windom, MN", "tier": 3})
    biz_id = r.json()["business"]["id"]
    token = r.json()["invite"]["claimUrl"].split("token=", 1)[1]
    r = owner.post("/api/auth/invites/claim", json={"token": token, "password": OWNER[1], "display_name": "x",
                                                     "accept_terms": True})
    check("owner set up", r.status_code == 200, r.text)

    def launch(name, platform, ext):
        c = owner.post("/api/ads/campaigns", json={"platform": platform, "name": name, "daily_budget_cents": 5000,
                                                   "duration_days": 30}).json()
        q = next(x for x in admin.get("/api/admin/ads/requests").json()["requests"] if x["campaign"]["id"] == c["id"])
        r = admin.post(f"/api/admin/ads/requests/{q['id']}/done", json={"external_campaign_id": ext})
        assert r.status_code == 200, r.text
        return c["id"]

    paint = launch("Fall paint sale", "fb_ig", META_ID)
    grill = launch("Grill season clearance", "fb_ig", META_ID2)
    contractor = launch("Contractor night", "google_ads", GOOGLE_ID)
    b2b = launch("B2B contractors", "linkedin", f"urn:li:sponsoredCampaign:{LI_ID}")

    def upload(platform, data, commit, client=admin, bid=None, filename="export.csv"):
        return client.post(f"/api/admin/businesses/{bid or biz_id}/ads/import", json={
            "platform": platform, "filename": filename, "commit": commit,
            "content_b64": base64.b64encode(data).decode()})

    def camp(cid):
        return next(c for c in owner.get("/api/ads").json()["campaigns"] if c["id"] == cid)

    def budget(platform, month=MONTH):
        with SessionLocal() as db:
            from app.models import AdPlatformBudget
            row = db.query(AdPlatformBudget).filter(AdPlatformBudget.business_id == biz_id,
                                                     AdPlatformBudget.platform == platform,
                                                     AdPlatformBudget.month_year == month).one_or_none()
            return row.spend_cents if row else None

    # ---- A ------------------------------------------------------------------
    print("\nA. reading the files")
    p = ai.parse(meta_csv([("Fall paint sale", D1, "100.00", 5000)]))
    check("A1 Meta: columns found by meaning, BOM and (USD) handled, link clicks preferred",
          p.columns["spend"] == "Amount spent (USD)" and p.columns["clicks"] == "Link clicks"
          and p.currency == "USD" and p.rows[0]["spend_cents"] == 10000 and p.rows[0]["day"] == D1, (p.columns, p.rows))
    p = ai.parse(google_csv())
    check("A2 Google: UTF-16 + tabs, preamble skipped, both Total rows skipped, commas and '--' read",
          len(p.rows) == 3 and p.skipped == 2 and p.rows[0]["spend_cents"] == 103333
          and p.rows[0]["impressions"] == 12345 and p.rows[1]["clicks"] == 0 and p.columns["impressions"] == "Impr.",
          (p.rows, p.skipped))
    p = ai.parse(linkedin_csv())
    check("A3 LinkedIn: preamble skipped, M/D/YYYY dates, 'Total Spent', '(in UTC)' isn't a currency",
          len(p.rows) == 3 and p.rows[0]["day"] == D1 and p.columns["spend"] == "Total Spent"
          and p.columns["day"] == "Start Date (in UTC)" and sum(r["spend_cents"] for r in p.rows) == 10000, p.columns)

    # ---- B ------------------------------------------------------------------
    print("\nB. files that can't be used")
    r = upload("fb_ig", meta_csv([("Fall paint sale", D1, "100.00", 5000, D3)], by_day=False), False)
    check("B1 a whole-range export is refused with how to fix it",
          r.status_code == 422 and "broken down by day" in r.json()["detail"] and "Breakdown → By time → Day" in r.json()["detail"], r.text)
    r = upload("linkedin", linkedin_csv("CAD"), False)
    check("B2 another currency is refused", r.status_code == 422 and "CAD" in r.json()["detail"], r.text)
    r = upload("fb_ig", b"hello,world\n1,2\n", False)
    check("B3 an unrelated file says which columns it needs", r.status_code == 422 and "Campaign name" in r.json()["detail"], r.text)
    r = upload("fb_ig", b"", False)
    check("B4 an empty file is refused", r.status_code == 422, r.text)
    r = upload("snapchat", meta_csv([("Fall paint sale", D1, "1.00", 5)]), False)
    check("B5 unknown platform → 422", r.status_code == 422, r.text)

    # ---- C ------------------------------------------------------------------
    print("\nC. preview, then import")
    rows = [("Fall paint sale", D1, "33.33", 1000), ("Fall paint sale", D2, "33.33", 1000), ("Fall paint sale", D3, "33.34", 1000),
            ("Grill season clearance", D1, "10.10", 400, None, META_ID2)]
    data = meta_csv(rows)
    r = upload("fb_ig", data, False)
    s = r.json()
    check("C1 preview shows the match, the money and the columns used",
          r.status_code == 200 and s["committed"] is False and s["totalSpendCents"] == 11010
          and {c["id"]: c["afterCents"] for c in s["campaigns"]} == {paint: 10000, grill: 1010}
          and s["dateFrom"] == D1 and s["dateTo"] == D3 and s["columns"]["campaign_id"] == "Campaign ID", s)
    with SessionLocal() as db:
        check("C2 ...and writes nothing", db.query(AdSpendDay).count() == 0 and db.query(AdImport).count() == 0)
    check("C3 spend still $0 on the dashboard", camp(paint)["actualSpendCents"] == 0)
    r = upload("fb_ig", data, True, filename="meta-oct.csv")
    s = r.json()
    check("C4 import applied", r.status_code == 200 and s["committed"] and s["importId"], r.text)
    c = camp(paint)
    check("C5 spend matches the file to the cent (33.33 + 33.33 + 33.34 = $100.00)", c["actualSpendCents"] == 10000, c)
    check("C6 impressions and clicks from the file", c["performance"]["impressions"] == 3000 and c["performance"]["clicks"] == 60, c["performance"])
    check("C7 this month's Meta spend = both campaigns", budget("fb_ig") == 11010, budget("fb_ig"))
    r = upload("google_ads", google_csv(), True)
    check("C8 Google import, to the cent ($1,033.33 + $0.01)", r.status_code == 200 and camp(contractor)["actualSpendCents"] == 103334, r.text)
    r = upload("linkedin", linkedin_csv(), True)
    check("C9 LinkedIn matches a URN campaign by its number", r.status_code == 200 and camp(b2b)["actualSpendCents"] == 10000, r.text)

    # ---- D ------------------------------------------------------------------
    print("\nD. re-imports and months")
    upload("fb_ig", data, True)
    check("D1 the same file twice doesn't double-count", camp(paint)["actualSpendCents"] == 10000 and budget("fb_ig") == 11010,
          (camp(paint)["actualSpendCents"], budget("fb_ig")))
    r = upload("fb_ig", meta_csv([("Fall paint sale", D3, "50.00", 2000)]), False)
    one = next(x for x in r.json()["campaigns"] if x["id"] == paint)
    check("D2 a corrected day replaces the old one (preview says so)", one["replacedDays"] == 1 and one["afterCents"] == 11666, one)
    upload("fb_ig", meta_csv([("Fall paint sale", D3, "50.00", 2000)]), True)
    check("D3 ...and the import agrees", camp(paint)["actualSpendCents"] == 11666 and budget("fb_ig") == 12676, budget("fb_ig"))
    upload("fb_ig", meta_csv([("Fall paint sale", P30, "7.00", 300)]), True)
    check("D4 a day from last month counts toward last month's spend, not this month's",
          budget("fb_ig", PREV_MONTH) == 700 and budget("fb_ig") == 12676 and camp(paint)["actualSpendCents"] == 12366,
          (budget("fb_ig", PREV_MONTH), budget("fb_ig")))

    # ---- E ------------------------------------------------------------------
    print("\nE. unmatched rows")
    s = upload("google_ads", google_csv(), False).json()
    check("E1 a campaign Amplafai doesn't manage is listed, with its spend, not guessed",
          s["unmatched"] == [{"campaign": "Someone else's campaign", "id": "999", "reason": "no launched campaign in the dashboard has this ID or name", "rows": 1, "spendCents": 500}],
          s["unmatched"])
    with SessionLocal() as db:
        from app.models import AdCampaign
        twin = AdCampaign(business_id=biz_id, platform="fb_ig", name="Fall paint sale", daily_budget_cents=100, duration_days=1,
                          planned_total_cents=100, status="active", external_campaign_id="120210000000000009")
        db.add(twin)
        db.commit()
    noid = ("﻿Campaign name,Day,Amount spent (USD)\nFall paint sale," + D1 + ",1.00\n").encode()
    s = upload("fb_ig", noid, False).json()
    check("E2 two campaigns with the same name and no ID column → not guessed",
          not s["campaigns"] and "Campaign ID" in s["unmatched"][0]["reason"] and "problem" in s, s)
    r = upload("tiktok", meta_csv([("Fall paint sale", D1, "1.00", 5)]), True)
    check("E3 nothing matched → nothing imported, and it says why", r.status_code == 200 and not r.json()["committed"]
          and "None of the rows matched" in r.json()["problem"], r.text)

    # ---- F ------------------------------------------------------------------
    print("\nF. cap alerts")
    OUTBOX.clear()
    r = owner.put("/api/ads/budgets/google_ads", json={"monthly_cap_cents": 125000})
    check("F0 owner sets a $1,250 Google cap", r.status_code == 200, r.text)
    big = "\r\n".join(["\t".join(["Campaign", "Campaign ID", "Day", "Currency code", "Cost"]),
                       "\t".join(["Contractor night", GOOGLE_ID, D3, "USD", "0.00"])]).encode("utf-16")
    upload("google_ads", big, True)
    check("F1 under 80% ($1,033.34 of $1,250 = 83%)? It's over 80, so one alert",
          [o.get("level") for o in OUTBOX if o.get("type") == "cap"] == [80], OUTBOX)
    alert = next(o for o in OUTBOX if o.get("type") == "cap")
    subject, text, to = ma.build_cap_email(alert, "https://dashboard.amplafai.com")
    check("F2 the owner and Amplafai both get it, with the numbers and a link",
          OWNER[0] in to and OPS in to and "80%" in subject and "$1,033.34" in text and "$1,250" in text
          and "/?tab=ads&b=" in text, (subject, text, to))
    with patch.object(mailer, "_send", capture_send):
        ma._deliver([alert])
    check("F3a outside production only Amplafai is emailed (a dev copy never emails clients)",
          [m["to"] for m in SENT] == [OPS], SENT)
    SENT.clear()
    with patch.object(mailer, "_send", capture_send), patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        ma._deliver([alert])
    check("F3b in production the owner and Amplafai both get it", sorted(m["to"] for m in SENT) == sorted([OWNER[0], OPS])
          and all(m["kind"] == "ads-cap-alert" for m in SENT), SENT)
    OUTBOX.clear()
    upload("google_ads", big, True)
    check("F4 importing again doesn't repeat the 80% alert", not [o for o in OUTBOX if o.get("type") == "cap"], OUTBOX)
    over = "\r\n".join(["\t".join(["Campaign", "Campaign ID", "Day", "Currency code", "Cost"]),
                        "\t".join(["Contractor night", GOOGLE_ID, D3, "USD", "300.00"])]).encode("utf-16")
    r = upload("google_ads", over, True)
    caps = [o for o in OUTBOX if o.get("type") == "cap"]
    check("F5 crossing 100% sends the 100% alert", r.status_code == 200 and [o["level"] for o in caps] == [100]
          and r.json()["alerts"][0]["pauseRequested"] == ["Contractor night"], (caps, r.text))
    c = camp(contractor)
    check("F6 ...and asks Amplafai to pause what's still running on Google (it keeps running until then)",
          c["stage"] == "pause_requested" and c["status"] == "active" and c["openRequest"]["source"] == "cap", c)
    check("F7 the pause request email says why", any(o.get("kind") == "pause" and o.get("source") == "cap" for o in OUTBOX), OUTBOX)
    subject, text, to = ma.build_cap_email(caps[0], "x")
    check("F8 the 100% email tells the owner what happens next", "100%" in subject and "pause" in text.lower()
          and "Contractor night" in text, text)
    OUTBOX.clear()
    upload("google_ads", over, True)
    with SessionLocal() as db:
        n = db.query(AdOpsRequest).filter(AdOpsRequest.campaign_id == contractor, AdOpsRequest.status == "open").count()
    check("F9 no repeat alert or second pause request", n == 1 and not [o for o in OUTBOX if o.get("type") == "cap"], OUTBOX)
    r = owner.get("/api/ads").json()
    gb = next(b for b in r["budgets"] if b["platform"] == "google_ads")
    check("F10 the cap bar shows real spend: $1,333.34 of $1,250", gb["spendCents"] == 133334 and gb["monthlyCapCents"] == 125000, gb)

    # ---- G ------------------------------------------------------------------
    print("\nG. what the owner sees")
    c = camp(paint)
    check("G1 each campaign says where its numbers came from and when",
          c["performance"]["source"] == "Meta Ads Manager export" and c["performance"]["importedAt"] and c["performance"]["through"] == D3, c["performance"])
    li = owner.get("/api/ads").json()["lastImports"]
    check("G2 the Ads screen knows the latest import per platform",
          li["fb_ig"]["through"] == D3 and li["google_ads"]["source"] == "Google Ads export" and "linkedin" in li, li)
    r = admin.get(f"/api/admin/businesses/{biz_id}/ads/imports")
    check("G3 the admin console lists past imports", r.status_code == 200 and len(r.json()["imports"]) >= 6
          and r.json()["imports"][0]["by"], r.text)

    # ---- H ------------------------------------------------------------------
    print("\nH. who can import")
    r = upload("fb_ig", data, True, client=owner)
    check("H1 owners can't import", r.status_code in (401, 403), r.status_code)
    r = upload("fb_ig", data, False, bid=1)
    check("H2 the demo account is refused (it uses simulated spend)", r.status_code == 422 and "demo" in r.json()["detail"], r.text)
    r = upload("fb_ig", data, False, bid=99999)
    check("H3 unknown business → 404", r.status_code == 404, r.text)
    r = admin.post(f"/api/admin/businesses/{biz_id}/ads/import", json={"platform": "fb_ig", "filename": "x.csv",
                                                                      "commit": False, "content_b64": "%%%not-base64"})
    check("H4 a garbled upload is refused", r.status_code == 422, r.text)


if __name__ == "__main__":
    main()
