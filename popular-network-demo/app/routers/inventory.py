"""Inventory router — Phase F.1 (Tier 4 Inventory Verticals).

Endpoints:
    GET    /api/inventory                          slice for InventoryView
    GET    /api/inventory/feeds                    list connected feeds
    POST   /api/inventory/feeds                    connect a feed (fixture CSV import path)
    PUT    /api/inventory/feeds/{id}               update label / status / config
    DELETE /api/inventory/feeds/{id}               disconnect feed (soft — listings stay)
    POST   /api/inventory/feeds/{id}/sync          re-import (mock: bumps last_sync_at)
    GET    /api/inventory/listings                 list w/ filters (status, stale, search)
    GET    /api/inventory/facet-hits               top-N faceted-search visibility rows
    POST   /api/inventory/import-fixture           load a built-in fixture CSV (demo affordance)

Real DealerCenter / vAuto / TractorHouse / MLS / BoatTrader adapters are
deferred to Phase 2 production. For the demo every feed_type is mocked
behind the fixture-CSV import path — the UI behavior + downstream schema
are identical regardless of which adapter eventually fills the data.

Tier gating: business.tier < 4 → 403 with a "Tier 4 required" message so the
dashboard can render an upsell card. The router doesn't 403 on read endpoints
when tier == 3 (the upsell needs to be able to fetch the empty-state preview).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Business,
    InventoryFacetHit,
    InventoryFeed,
    InventoryListing,
)

router = APIRouter()


INVENTORY_FEED_TYPES = ("dealercenter", "vauto", "tractor_house", "mls", "boat_trader", "generic_csv")
FeedType = Literal["dealercenter", "vauto", "tractor_house", "mls", "boat_trader", "generic_csv"]


# Display labels for the Connect-a-feed form. Order matters — this is how
# the form lists them in the UI's segmented picker.
FEED_TYPE_LABELS: dict[str, str] = {
    "dealercenter":  "DealerCenter (auto DMS)",
    "vauto":         "vAuto (auto inventory)",
    "tractor_house": "TractorHouse (ag equipment)",
    "mls":           "MLS / RETS (real estate)",
    "boat_trader":   "BoatTrader (marine)",
    "generic_csv":   "Generic CSV upload",
}

# Stale rule: a listing is stale once it's been listed >30 days with zero
# clicks in the last 30 days. Constant lives here so the smoke test and the
# UI explainer can both reference the same number.
STALE_DAYS_THRESHOLD = 30


# ---------------------------------------------------------------------------
# Pydantic bodies
# ---------------------------------------------------------------------------

class FeedConnectBody(BaseModel):
    feed_type: FeedType
    location_label: str = Field(min_length=1, max_length=120)
    config_json: Optional[dict[str, Any]] = None


class FeedUpdateBody(BaseModel):
    location_label: Optional[str] = Field(default=None, min_length=1, max_length=120)
    status: Optional[Literal["connected", "disconnected", "paused", "error"]] = None
    config_json: Optional[dict[str, Any]] = None


class ImportFixtureBody(BaseModel):
    feed_type: FeedType = "generic_csv"
    location_label: str = "Main lot"
    # Owner can paste a multi-line CSV; default uses the bundled SAMPLE_CSV.
    csv_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Payload shapers
# ---------------------------------------------------------------------------

def feed_payload(f: InventoryFeed) -> dict[str, Any]:
    return {
        "id":             f.id,
        "feedType":       f.feed_type,
        "feedTypeLabel":  FEED_TYPE_LABELS.get(f.feed_type, f.feed_type),
        "locationLabel":  f.location_label,
        "status":         f.status,
        "lastSyncAt":     f.last_sync_at.isoformat() if f.last_sync_at else None,
        "lastSyncStatus": f.last_sync_status,
        "listingCount":   f.listing_count,
        "config":         f.config_json or {},
    }


def listing_payload(li: InventoryListing) -> dict[str, Any]:
    return {
        "id":                 li.id,
        "feedId":              li.feed_id,
        "externalId":          li.external_id,
        "title":               li.title,
        "priceCents":          li.price_cents,
        "status":              li.status,
        "daysListed":          li.days_listed,
        "queryImpressions30d": li.query_impressions_30d,
        "clicks30d":           li.clicks_30d,
        "lastSeenAt":          li.last_seen_at.isoformat() if li.last_seen_at else None,
        "attributes":          li.attributes_json or {},
        "isStale":             li.is_stale,
    }


def facet_hit_payload(h: InventoryFacetHit) -> dict[str, Any]:
    return {
        "id":              h.id,
        "facetText":       h.facet_text,
        "rank":            h.rank,
        "hits30d":         h.hits_30d,
        "sampleListingId": h.sample_listing_id,
    }


# ---------------------------------------------------------------------------
# Tier gating
# ---------------------------------------------------------------------------

def _require_tier_4_for_writes(db: Session, business_id: int) -> Business:
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail=f"business {business_id} not found")
    if biz.tier < 4:
        raise HTTPException(
            status_code=403,
            detail=f"Tier 4 required for inventory feeds (this business is tier {biz.tier}). "
                   f"Upgrade in Settings → Billing to enable.",
        )
    return biz


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/inventory")
def get_inventory(business_id: int = 1, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Aggregate slice — used by InventoryView on mount."""
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail=f"business {business_id} not found")

    feeds = (
        db.query(InventoryFeed)
        .filter(InventoryFeed.business_id == business_id)
        .order_by(InventoryFeed.id.asc())
        .all()
    )
    listings = (
        db.query(InventoryListing)
        .filter(InventoryListing.business_id == business_id)
        .order_by(InventoryListing.days_listed.desc())
        .all()
    )
    facet_hits = (
        db.query(InventoryFacetHit)
        .filter(InventoryFacetHit.business_id == business_id)
        .order_by(InventoryFacetHit.rank.asc(), InventoryFacetHit.hits_30d.desc())
        .limit(8)
        .all()
    )

    total_listings = len(listings)
    active_count   = sum(1 for li in listings if li.status == "active")
    sold_count     = sum(1 for li in listings if li.status == "sold")
    stale_count    = sum(1 for li in listings if li.is_stale)
    total_impr_30d = sum(li.query_impressions_30d for li in listings)
    total_clk_30d  = sum(li.clicks_30d for li in listings)

    # Distinct locations across all feeds. Used by the Inventory view to
    # decide whether to render multi-location tabs.
    location_labels = sorted({f.location_label for f in feeds})

    return {
        "tier":             biz.tier,
        "tierEligible":     biz.tier >= 4,
        "feedTypes":        [{"key": k, "label": v} for k, v in FEED_TYPE_LABELS.items()],
        "feeds":            [feed_payload(f) for f in feeds],
        "listings":         [listing_payload(li) for li in listings],
        "facetHits":        [facet_hit_payload(h) for h in facet_hits],
        "locationLabels":   location_labels,
        "totals": {
            "listings":           total_listings,
            "active":             active_count,
            "sold":               sold_count,
            "stale":              stale_count,
            "impressions30d":     total_impr_30d,
            "clicks30d":          total_clk_30d,
            "ctrPct":             (total_clk_30d / total_impr_30d * 100) if total_impr_30d else 0.0,
        },
        "hasAnyFeed":     len(feeds) > 0,
        "hasAnyListing":  total_listings > 0,
        "staleThreshold": STALE_DAYS_THRESHOLD,
    }


@router.get("/inventory/feeds")
def list_feeds(business_id: int = 1, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (
        db.query(InventoryFeed)
        .filter(InventoryFeed.business_id == business_id)
        .order_by(InventoryFeed.id.asc())
        .all()
    )
    return [feed_payload(f) for f in rows]


@router.post("/inventory/feeds")
def connect_feed(
    body: FeedConnectBody,
    business_id: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Connect a new feed. Tier 4 required."""
    _require_tier_4_for_writes(db, business_id)

    feed = InventoryFeed(
        business_id=business_id,
        feed_type=body.feed_type,
        location_label=body.location_label,
        config_json=body.config_json or {},
        status="connected",
        last_sync_at=datetime.utcnow(),
        last_sync_status="0 listings · first sync pending",
        listing_count=0,
    )
    db.add(feed)
    db.commit()
    db.refresh(feed)
    return feed_payload(feed)


@router.put("/inventory/feeds/{feed_id}")
def update_feed(
    feed_id: int,
    body: FeedUpdateBody,
    business_id: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_tier_4_for_writes(db, business_id)
    feed = db.get(InventoryFeed, feed_id)
    if feed is None or feed.business_id != business_id:
        raise HTTPException(status_code=404, detail=f"feed {feed_id} not found")
    if body.location_label is not None:
        feed.location_label = body.location_label
    if body.status is not None:
        feed.status = body.status
    if body.config_json is not None:
        feed.config_json = body.config_json
    db.commit()
    db.refresh(feed)
    return feed_payload(feed)


@router.delete("/inventory/feeds/{feed_id}")
def disconnect_feed(
    feed_id: int,
    business_id: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Soft-disconnect — keeps the feed row + its listings, just marks status."""
    _require_tier_4_for_writes(db, business_id)
    feed = db.get(InventoryFeed, feed_id)
    if feed is None or feed.business_id != business_id:
        raise HTTPException(status_code=404, detail=f"feed {feed_id} not found")
    feed.status = "disconnected"
    db.commit()
    return {"ok": True, "id": feed_id, "status": "disconnected"}


@router.post("/inventory/feeds/{feed_id}/sync")
def sync_feed(
    feed_id: int,
    business_id: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Re-import path. Mocked — bumps last_sync_at + refreshes stale flags."""
    _require_tier_4_for_writes(db, business_id)
    feed = db.get(InventoryFeed, feed_id)
    if feed is None or feed.business_id != business_id:
        raise HTTPException(status_code=404, detail=f"feed {feed_id} not found")

    listings = (
        db.query(InventoryListing)
        .filter(InventoryListing.feed_id == feed_id)
        .all()
    )
    # Recompute stale flag for everything in this feed.
    total_stale = 0
    for li in listings:
        li.is_stale = li.days_listed > STALE_DAYS_THRESHOLD and li.clicks_30d == 0
        if li.is_stale:
            total_stale += 1
            if li.status == "active":
                li.status = "stale"

    feed.last_sync_at = datetime.utcnow()
    feed.last_sync_status = f"{len(listings)} listings · {total_stale} stale"
    feed.listing_count = len(listings)
    db.commit()
    db.refresh(feed)
    return feed_payload(feed)


@router.get("/inventory/listings")
def list_listings(
    business_id: int = 1,
    status: Optional[str] = Query(None, description="active|sold|pending|stale|removed"),
    stale_only: bool = Query(False),
    search: Optional[str] = Query(None, max_length=200),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    q = db.query(InventoryListing).filter(InventoryListing.business_id == business_id)
    if status:
        q = q.filter(InventoryListing.status == status)
    if stale_only:
        q = q.filter(InventoryListing.is_stale.is_(True))
    if search:
        q = q.filter(InventoryListing.title.ilike(f"%{search}%"))
    rows = q.order_by(InventoryListing.days_listed.desc(), InventoryListing.id.asc()).all()
    return [listing_payload(li) for li in rows]


@router.get("/inventory/facet-hits")
def list_facet_hits(
    business_id: int = 1,
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        db.query(InventoryFacetHit)
        .filter(InventoryFacetHit.business_id == business_id)
        .order_by(InventoryFacetHit.rank.asc(), InventoryFacetHit.hits_30d.desc())
        .limit(limit)
        .all()
    )
    return [facet_hit_payload(h) for h in rows]


# ---------------------------------------------------------------------------
# Fixture-CSV importer
# ---------------------------------------------------------------------------
#
# Demo affordance: a sales rep can show "what Tier 4 looks like populated"
# in one click. The owner picks a vertical, the importer creates a feed +
# 12 sample listings + 4 sample facet-hit rows from the corresponding CSV.
#
# Headers expected in CSV: external_id, title, price_cents, status, days_listed,
# query_impressions_30d, clicks_30d, attribute_<key>... (any number of attribute_*
# columns becomes attributes_json).

# Auto-dealer fixture — closest to the "Westbrook Auto" reference vertical
# the original plan called out. Twelve listings, mixed status, a couple stale.
_AUTO_CSV = """external_id,title,price_cents,status,days_listed,query_impressions_30d,clicks_30d,attribute_year,attribute_make,attribute_model,attribute_miles
W-1041,2019 Ford F-150 XLT 4WD,3499500,active,12,847,38,2019,Ford,F-150,42100
W-1042,2017 Chevrolet Silverado 1500 LT,2799500,active,18,612,29,2017,Chevrolet,Silverado 1500,58800
W-1043,2021 Toyota Tacoma TRD Off-Road,3899500,pending,4,1204,71,2021,Toyota,Tacoma,18400
W-1044,2015 Ram 2500 Laramie 4x4,3199500,active,33,210,3,2015,Ram,2500,72500
W-1045,2020 Subaru Outback Limited AWD,2799500,active,7,521,24,2020,Subaru,Outback,28100
W-1046,2014 GMC Sierra 1500 SLE,1849500,stale,46,82,0,2014,GMC,Sierra 1500,98300
W-1047,2018 Ford Escape SE AWD,1599500,sold,2,440,18,2018,Ford,Escape,51200
W-1048,2022 Honda Ridgeline RTL-E,4199500,active,9,1080,52,2022,Honda,Ridgeline,12800
W-1049,2013 Jeep Wrangler Unlimited Sport,1999500,active,38,310,4,2013,Jeep,Wrangler Unlimited,89100
W-1050,2019 Chevrolet Equinox LT AWD,2099500,active,15,398,17,2019,Chevrolet,Equinox,38400
W-1051,2016 Ford Edge SEL AWD,1799500,stale,52,71,0,2016,Ford,Edge,76900
W-1052,2023 Hyundai Tucson SEL AWD,2999500,active,3,633,29,2023,Hyundai,Tucson,4200
"""

_AUTO_FACETS = [
    ("4WD trucks under $20,000 near Westbrook", 1, 47, "W-1046"),
    ("Used Ford F-150 in southwest Minnesota",  1, 62, "W-1041"),
    ("Pickup trucks 4WD under 50k miles",       2, 31, "W-1041"),
    ("AWD SUV under $20k near New Ulm",         1, 28, "W-1045"),
]

_REALTY_CSV = """external_id,title,price_cents,status,days_listed,query_impressions_30d,clicks_30d,attribute_beds,attribute_baths,attribute_sqft,attribute_zip
H-2204,3BR Craftsman on Oak Street,28500000,active,11,612,41,3,2,1840,56101
H-2205,Lakefront 4BR/3BA with dock,49500000,active,5,1280,82,4,3,2680,56123
H-2206,Updated 2BR starter home,16500000,pending,3,420,22,2,1,1020,56101
H-2207,Hobby farm — 7 acres + barn,38500000,active,21,540,17,3,2,1950,56115
H-2208,Downtown 1BR loft,12500000,active,38,180,2,1,1,820,56101
H-2209,Mid-century 4BR on Maple Lane,29900000,active,9,711,38,4,2,2210,56101
H-2210,Acreage hunting parcel — 18 acres,15900000,active,15,302,9,0,0,0,56115
H-2211,5BR/3BA new construction,52900000,active,6,890,44,5,3,3100,56123
H-2212,Walkout ranch 3BR/2BA on cul-de-sac,33500000,active,13,395,22,3,2,2050,56101
H-2213,Investment duplex — 2 units,21900000,active,42,160,1,4,2,1850,56101
H-2214,Riverfront cabin + 2 acres,24900000,sold,4,510,26,2,1,940,56115
H-2215,Hilltop 6BR estate on 4 acres,79500000,active,28,610,11,6,4,4200,56123
"""

_REALTY_FACETS = [
    ("Homes under $200k near Windom",             1, 38, "H-2206"),
    ("Lakefront homes within 30 miles",            1, 51, "H-2205"),
    ("Acreage with outbuildings near Pipestone",   2, 22, "H-2207"),
    ("Move-in-ready 3BR for under $300k",          1, 33, "H-2206"),
]

_AG_CSV = """external_id,title,price_cents,status,days_listed,query_impressions_30d,clicks_30d,attribute_make,attribute_model,attribute_year,attribute_hours
A-330,John Deere 8320R MFWD Tractor,18500000,active,10,540,28,John Deere,8320R,2017,3200
A-331,Case IH 7240 Magnum 270HP,16900000,active,18,420,19,Case IH,Magnum 7240,2015,4100
A-332,Kubota M5-111 Cab Tractor,5450000,active,7,310,16,Kubota,M5-111,2021,820
A-333,New Holland TG285 4WD,9800000,active,33,140,2,New Holland,TG285,2008,7400
A-334,John Deere S680 Combine,21500000,pending,4,810,42,John Deere,S680,2019,2100
A-335,Case IH Patriot 4440 Sprayer,28500000,active,12,510,21,Case IH,Patriot 4440,2020,1850
A-336,JD 568 Round Baler,3950000,stale,52,82,0,John Deere,568,2010,—
A-337,Vermeer R2300 Twin Rake,1750000,active,21,180,7,Vermeer,R2300,2018,—
A-338,Sunflower 1435 Disk 40ft,3850000,active,15,225,11,Sunflower,1435,2019,—
A-339,Brent 1196 Avalanche Grain Cart,8950000,sold,6,640,33,Brent,1196,2018,—
A-340,JD 1775NT 24R30 Planter,18750000,active,28,310,8,John Deere,1775NT,2020,—
A-341,Kuhn FC3525 Disc Mower-Conditioner,2250000,active,4,140,9,Kuhn,FC3525,2022,—
"""

_AG_FACETS = [
    ("Used combine under $250k near Marshall",  1, 31, "A-334"),
    ("MFWD tractor 250+ HP under 5k hours",      1, 24, "A-330"),
    ("Round balers under $50k in MN",            2, 18, "A-336"),
    ("Sprayer self-propelled used southwest MN", 1, 22, "A-335"),
]

_FIXTURE_BY_FEED_TYPE = {
    "dealercenter":  ("Westbrook Auto & Tire", _AUTO_CSV, _AUTO_FACETS),
    "vauto":         ("Westbrook Auto & Tire", _AUTO_CSV, _AUTO_FACETS),
    "tractor_house": ("Pipestone Implement",   _AG_CSV,   _AG_FACETS),
    "mls":           ("Cottonwood County Realty", _REALTY_CSV, _REALTY_FACETS),
    "boat_trader":   ("Lake Shetek Marine",    _REALTY_CSV, _REALTY_FACETS),  # placeholder
    "generic_csv":   ("Sample Lot",            _AUTO_CSV, _AUTO_FACETS),
}


@router.post("/inventory/import-fixture")
def import_fixture(
    body: ImportFixtureBody,
    business_id: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Import a built-in fixture CSV (or paste your own) into a brand-new feed.

    Demo affordance — sales rep can show what populated Tier 4 looks like with
    one button-click. Returns the new feed + count of listings created.
    """
    _require_tier_4_for_writes(db, business_id)

    default_label, default_csv, default_facets = _FIXTURE_BY_FEED_TYPE.get(
        body.feed_type, _FIXTURE_BY_FEED_TYPE["generic_csv"]
    )
    csv_text = body.csv_text or default_csv
    location_label = body.location_label or default_label

    feed = InventoryFeed(
        business_id=business_id,
        feed_type=body.feed_type,
        location_label=location_label,
        config_json={"source": "fixture", "size_bytes": len(csv_text)},
        status="connected",
        last_sync_at=datetime.utcnow(),
        last_sync_status="initial fixture import",
        listing_count=0,
    )
    db.add(feed)
    db.flush()

    reader = csv.DictReader(io.StringIO(csv_text))
    created = 0
    for row in reader:
        attributes = {
            k[len("attribute_"):]: v
            for k, v in row.items()
            if k.startswith("attribute_") and v != ""
        }
        days = int(row.get("days_listed") or 0)
        clicks = int(row.get("clicks_30d") or 0)
        is_stale_now = days > STALE_DAYS_THRESHOLD and clicks == 0
        li = InventoryListing(
            feed_id=feed.id,
            business_id=business_id,
            external_id=row.get("external_id", f"row-{created}"),
            title=row.get("title", "(untitled)"),
            price_cents=int(row.get("price_cents") or 0),
            status=row.get("status") or "active",
            days_listed=days,
            query_impressions_30d=int(row.get("query_impressions_30d") or 0),
            clicks_30d=clicks,
            last_seen_at=datetime.utcnow() - timedelta(days=max(0, days)),
            attributes_json=attributes,
            is_stale=is_stale_now,
        )
        db.add(li)
        created += 1

    feed.listing_count = created
    feed.last_sync_status = f"{created} listings · imported from fixture"

    # Seed facet-hit rows alongside, so the search-visibility card has data.
    db.flush()
    sample_listing = (
        db.query(InventoryListing).filter(InventoryListing.feed_id == feed.id).first()
    )
    for facet_text, rank, hits, _external in default_facets:
        db.add(InventoryFacetHit(
            business_id=business_id,
            facet_text=facet_text,
            rank=rank,
            hits_30d=hits,
            sample_listing_id=sample_listing.id if sample_listing else None,
        ))

    db.commit()
    db.refresh(feed)
    return {"ok": True, "feed": feed_payload(feed), "listingsCreated": created}
