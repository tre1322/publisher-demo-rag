"""Phase 4b — real spend from the ad platforms' own exports.

Until each platform's reporting API is approved, Amplafai downloads a
campaign report from Meta Ads Manager, Google Ads or LinkedIn Campaign
Manager every day or two and uploads it in the admin console. This module
turns that file into per-day spend for each managed campaign, so:

  * a campaign's spend, impressions and clicks are the platform's numbers,
    labelled with where they came from and when;
  * each platform's monthly spend is summed from the days in that month, so
    the owner's caps are checked against real money;
  * crossing 80% or 100% of a monthly cap emails the owner and Amplafai, and
    at 100% Amplafai is asked to pause what's still running on that platform.

Re-importing an overlapping date range replaces those days, so uploading the
same file twice (or a longer range later) never double-counts.

Exports differ by platform and change over time, so the parser looks for
columns by meaning (campaign ID or name, day, spend, impressions, clicks)
rather than fixed positions, and every import is previewed first, showing
which columns it used and which rows it couldn't match.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import managed_ads
from .models import AdCampaign, AdImport, AdPlatformBudget, AdSpendDay, Business

MAX_BYTES = 3 * 1024 * 1024
PLATFORMS = ("fb_ig", "google_ads", "linkedin", "tiktok")
SOURCE_LABELS = {"fb_ig": "Meta Ads Manager export", "google_ads": "Google Ads export",
                 "linkedin": "LinkedIn Campaign Manager export", "tiktok": "TikTok Ads Manager export"}
# How to get a by-day export, shown when a file has one row per campaign.
BY_DAY_HELP = ("Meta: Breakdown → By time → Day. Google Ads: Segment → Time → Day. "
               "LinkedIn: Time breakdown → Daily.")
ALERT_LEVELS = (80, 100)

# Header meanings, in order of preference. Matched after normalizing:
# lowercase, single spaces, and a trailing "(USD)" moved into the currency.
COLUMNS: dict[str, tuple[str, ...]] = {
    "campaign_id": ("campaign id",),
    "campaign_name": ("campaign name", "campaign"),
    "day": ("day", "date", "start date (in utc)", "start date"),
    "starts": ("reporting starts",),
    "ends": ("reporting ends",),
    "spend": ("amount spent", "cost", "total spent", "spend", "amount spend"),
    "impressions": ("impressions", "impr.", "impr"),
    "clicks": ("link clicks", "clicks", "clicks (all)"),
    "currency": ("currency", "currency code"),
}
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%a, %b %d, %Y",
                "%A, %B %d, %Y", "%Y/%m/%d")


class ImportProblem(Exception):
    """The file can't be used as it is; the message says what to do."""


@dataclass
class Parsed:
    rows: list[dict[str, Any]]
    columns: dict[str, str]
    currency: Optional[str]
    skipped: int = 0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reading the file
# --------------------------------------------------------------------------- #

def decode(data: bytes) -> str:
    """Exports arrive as UTF-8 (often with a BOM) or, from Google Ads'
    'Excel' download, as UTF-16 with tabs."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def _norm_header(h: str) -> tuple[str, Optional[str]]:
    h = re.sub(r"\s+", " ", (h or "").replace("﻿", "").strip().lower())
    m = re.search(r"\s*\(([a-z]{3})\)$", h)
    cur = None
    if m and m.group(1) not in ("all", "utc", "gmt"):
        cur = m.group(1).upper()
        h = h[: m.start()].strip()
    return h, cur


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int], Optional[str]]:
    for i, row in enumerate(rows[:20]):
        normed = [_norm_header(c) for c in row]
        names = [n for n, _ in normed]
        found: dict[str, int] = {}
        for meaning, options in COLUMNS.items():
            for opt in options:
                if opt in names:
                    found[meaning] = names.index(opt)
                    break
        if ("campaign_id" in found or "campaign_name" in found) and "spend" in found:
            cur = normed[found["spend"]][1]
            return i, found, cur
    raise ImportProblem(
        "Couldn't find the campaign and spend columns. Download the campaign report as a CSV that "
        "includes Campaign name (or Campaign ID) and Amount spent / Cost.")


def _money(raw: str) -> int:
    s = re.sub(r"[^\d.\-]", "", raw or "")
    if s in ("", "-", ".", "--"):
        return 0
    try:
        return int((Decimal(s) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ImportProblem(f"Couldn't read the amount \"{raw}\".")


def _count(raw: str) -> int:
    s = re.sub(r"[^\d.]", "", raw or "")
    if not s or s == ".":
        return 0
    return int(Decimal(s))


def _date(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse(data: bytes) -> Parsed:
    if not data:
        raise ImportProblem("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ImportProblem("The file is over 3 MB. Export a shorter date range.")
    text = decode(data)
    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    hi, cols, cur = _find_header(rows)
    used = {k: rows[hi][v].replace("﻿", "").strip() for k, v in cols.items()}

    def cell(row: list[str], key: str) -> str:
        i = cols.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    out: list[dict[str, Any]] = []
    skipped = 0
    for row in rows[hi + 1:]:
        if not any(c.strip() for c in row):
            continue
        first = row[0].strip().lower() if row else ""
        cid, name = cell(row, "campaign_id"), cell(row, "campaign_name")
        if first.startswith("total") or (not cid and not name) or name.lower().startswith("total:"):
            skipped += 1
            continue
        day = _date(cell(row, "day")) if "day" in cols else None
        if day is None and "starts" in cols:
            starts, ends = _date(cell(row, "starts")), _date(cell(row, "ends") or cell(row, "starts"))
            if starts and starts == ends:
                day = starts
            elif starts:
                raise ImportProblem(
                    f"This export has one row per campaign for {starts} to {ends}, so spend can't be put "
                    f"in the right days and months. Download it broken down by day. {BY_DAY_HELP}")
        if day is None:
            raw = cell(row, "day")
            if raw:
                raise ImportProblem(f"Couldn't read the date \"{raw}\".")
            raise ImportProblem(f"This export has no day column. Download it broken down by day. {BY_DAY_HELP}")
        row_cur = cell(row, "currency").upper() or cur
        if row_cur and row_cur != "USD":
            raise ImportProblem(f"This export is in {row_cur}. The dashboard tracks spend in US dollars.")
        out.append({
            "campaign_id": cid, "name": name, "day": day,
            "spend_cents": _money(cell(row, "spend")),
            "impressions": _count(cell(row, "impressions")),
            "clicks": _count(cell(row, "clicks")),
        })
    if not out:
        raise ImportProblem("No campaign rows found under the header row.")
    return Parsed(rows=out, columns=used, currency=cur or ("USD" if "currency" in cols else None), skipped=skipped)


# --------------------------------------------------------------------------- #
# Matching rows to the dashboard's campaigns
# --------------------------------------------------------------------------- #

def _norm_id(raw: Optional[str]) -> str:
    raw = (raw or "").strip()
    return raw.rsplit(":", 1)[-1] if raw.startswith("urn:") else raw


def match(db: Session, business_id: int, platform: str, rows: list[dict[str, Any]]):
    campaigns = [
        c for c in db.query(AdCampaign).filter(AdCampaign.business_id == business_id,
                                               AdCampaign.platform == platform).all()
        if managed_ads.on_platform(c)
    ]
    by_id = {_norm_id(c.external_campaign_id): c for c in campaigns}
    by_name: dict[str, list[AdCampaign]] = defaultdict(list)
    for c in campaigns:
        by_name[c.name.strip().lower()].append(c)

    matched: dict[int, dict[str, dict[str, int]]] = defaultdict(dict)  # campaign id → day → totals
    unmatched: dict[str, dict[str, Any]] = {}
    for r in rows:
        c = by_id.get(_norm_id(r["campaign_id"])) if r["campaign_id"] else None
        reason = None
        if c is None:
            cands = by_name.get(r["name"].strip().lower(), []) if r["name"] else []
            if len(cands) == 1:
                c = cands[0]
            elif len(cands) > 1:
                reason = "two dashboard campaigns have this name; include the Campaign ID column"
            else:
                reason = "no launched campaign in the dashboard has this ID or name"
        if c is None:
            key = r["campaign_id"] or r["name"]
            u = unmatched.setdefault(key, {"campaign": r["name"] or r["campaign_id"], "id": r["campaign_id"],
                                           "reason": reason, "rows": 0, "spendCents": 0})
            u["rows"] += 1
            u["spendCents"] += r["spend_cents"]
            continue
        d = matched[c.id].setdefault(r["day"], {"spend_cents": 0, "impressions": 0, "clicks": 0})
        for k in ("spend_cents", "impressions", "clicks"):
            d[k] += r[k]   # an ad-set-level export has several rows per campaign-day
    return {c.id: c for c in campaigns}, matched, list(unmatched.values())


# --------------------------------------------------------------------------- #
# Preview and import
# --------------------------------------------------------------------------- #

def _campaign_totals(db: Session, campaign_id: int) -> tuple[int, int, int]:
    s, i, c = db.query(func.coalesce(func.sum(AdSpendDay.spend_cents), 0),
                       func.coalesce(func.sum(AdSpendDay.impressions), 0),
                       func.coalesce(func.sum(AdSpendDay.clicks), 0)).filter(AdSpendDay.campaign_id == campaign_id).one()
    return int(s), int(i), int(c)


def month_spend(db: Session, business_id: int, platform: str, month: str) -> int:
    return int(db.query(func.coalesce(func.sum(AdSpendDay.spend_cents), 0)).filter(
        AdSpendDay.business_id == business_id, AdSpendDay.platform == platform,
        AdSpendDay.day.like(f"{month}-%")).scalar())


def run_import(db: Session, biz: Business, platform: str, data: bytes, *, filename: str, by: str,
               commit: bool, now: Optional[datetime] = None) -> dict[str, Any]:
    """Preview (commit=False) or apply an export. Never commits the session."""
    if platform not in PLATFORMS:
        raise ImportProblem("Pick which ad platform the file came from.")
    if not managed_ads.is_managed(biz):
        raise ImportProblem("The demo account uses simulated spend; imports are for real clients.")
    now = now or datetime.utcnow()
    parsed = parse(data)
    campaigns, matched, unmatched = match(db, biz.id, platform, parsed.rows)
    days = sorted({d for per in matched.values() for d in per})
    summary: dict[str, Any] = {
        "platform": platform, "source": SOURCE_LABELS[platform], "filename": filename,
        "columns": parsed.columns, "currency": parsed.currency or "USD (assumed)",
        "dateFrom": days[0] if days else None, "dateTo": days[-1] if days else None,
        "skippedRows": parsed.skipped, "unmatched": unmatched,
        "campaigns": [], "totalSpendCents": 0, "committed": False, "alerts": [],
    }
    for cid, per in sorted(matched.items()):
        c = campaigns[cid]
        before = c.actual_spend_cents or 0
        existing = {d.day: d.spend_cents for d in db.query(AdSpendDay).filter(AdSpendDay.campaign_id == cid,
                                                                               AdSpendDay.day.in_(list(per)))}
        delta = sum(v["spend_cents"] for v in per.values()) - sum(existing.values())
        summary["campaigns"].append({
            "id": cid, "name": c.name, "externalCampaignId": c.external_campaign_id, "days": len(per),
            "fileSpendCents": sum(v["spend_cents"] for v in per.values()),
            "beforeCents": before, "afterCents": before + delta,
            "replacedDays": len(existing),
        })
        summary["totalSpendCents"] += sum(v["spend_cents"] for v in per.values())
    if not matched:
        summary["problem"] = ("None of the rows matched a launched campaign. Check the platform, and that the "
                              "export includes the Campaign ID column.")
    if not commit or not matched:
        return summary

    imp = AdImport(business_id=biz.id, platform=platform, filename=filename[:200], imported_by=by,
                   imported_at=now, date_from=summary["dateFrom"], date_to=summary["dateTo"],
                   rows=len(parsed.rows), total_spend_cents=summary["totalSpendCents"],
                   unmatched_json=unmatched)
    db.add(imp)
    db.flush()
    summary["alerts"] = store_days(db, biz, platform, matched, campaigns, source=SOURCE_LABELS[platform],
                                   import_id=imp.id, now=now, by=by)
    summary["committed"] = True
    summary["importId"] = imp.id
    return summary


def store_days(db: Session, biz: Business, platform: str, matched: dict[int, dict[str, dict[str, int]]],
               campaigns: dict[int, AdCampaign], *, source: str, import_id: Optional[int], now: datetime,
               by: str) -> list[dict[str, Any]]:
    """Write per-day results (replacing those days), recompute each campaign's
    totals and each touched month's platform spend, then check the caps.
    Shared by export uploads (4b) and the platforms' API sync (5a)."""
    days = {d for per in matched.values() for d in per}
    for cid, per in matched.items():
        old = {d.day: d for d in db.query(AdSpendDay).filter(AdSpendDay.campaign_id == cid,
                                                              AdSpendDay.day.in_(list(per)))}
        for day, v in per.items():
            row = old.get(day)
            if row is None:
                row = AdSpendDay(business_id=biz.id, campaign_id=cid, platform=platform, day=day)
                db.add(row)
            row.spend_cents, row.impressions, row.clicks = v["spend_cents"], v["impressions"], v["clicks"]
            row.import_id, row.imported_at = import_id, now
        db.flush()
        c = campaigns[cid]
        spend, imps, clicks = _campaign_totals(db, cid)
        c.actual_spend_cents = spend
        latest = db.query(func.max(AdSpendDay.day)).filter(AdSpendDay.campaign_id == cid).scalar()
        c.performance_json = {"impressions": imps, "clicks": clicks, "ctr": (clicks / imps) if imps else 0.0,
                              "source": source, "importedAt": now.isoformat(), "through": latest}
    for month in sorted({d[:7] for d in days}):
        row = (db.query(AdPlatformBudget)
               .filter(AdPlatformBudget.business_id == biz.id, AdPlatformBudget.platform == platform,
                       AdPlatformBudget.month_year == month).one_or_none())
        if row is None:
            row = AdPlatformBudget(business_id=biz.id, platform=platform, month_year=month,
                                   monthly_cap_cents=0, spend_cents=0, status="active")
            db.add(row)
        row.spend_cents = month_spend(db, biz.id, platform, month)
        row.updated_at = now
    db.flush()
    return check_caps(db, biz, platform, now=now, by=by)


# --------------------------------------------------------------------------- #
# Cap alerts
# --------------------------------------------------------------------------- #

def check_caps(db: Session, biz: Business, platform: str, *, now: datetime, by: str) -> list[dict[str, Any]]:
    """Email at 80% and 100% of this month's cap, once per level. At 100%,
    ask Amplafai to pause every campaign still running on the platform."""
    month = now.strftime("%Y-%m")
    row = (db.query(AdPlatformBudget)
           .filter(AdPlatformBudget.business_id == biz.id, AdPlatformBudget.platform == platform,
                   AdPlatformBudget.month_year == month).one_or_none())
    if row is None or (row.monthly_cap_cents or 0) <= 0:
        return []
    pct = row.spend_cents / row.monthly_cap_cents
    sent = dict(row.alerts_json or {})
    due = [lvl for lvl in ALERT_LEVELS if pct * 100 >= lvl and str(lvl) not in sent]
    if not due:
        return []
    level = max(due)          # one email, at the highest level just crossed
    for lvl in due:
        sent[str(lvl)] = now.isoformat()
    row.alerts_json = sent
    paused: list[str] = []
    if level >= 100:
        live = (db.query(AdCampaign)
                .filter(AdCampaign.business_id == biz.id, AdCampaign.platform == platform,
                        AdCampaign.status == "active").all())
        for c in live:
            if managed_ads.on_platform(c):
                out = managed_ads.change(db, biz, c, "pause", by="monthly cap reached", source="cap")
                if out["outcome"] == "requested":
                    paused.append(c.name)
    alert = {"level": level, "platform": platform, "spendCents": row.spend_cents, "capCents": row.monthly_cap_cents,
             "month": month, "pauseRequested": paused}
    managed_ads.queue_cap_alert(db, biz, alert)
    return [alert]
