#!/usr/bin/env python
"""Load sample advertisements into the database."""

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.modules.advertisements import insert_advertisement, get_advertisement_count

# Sample advertisements - Pipestone, MN local businesses
SAMPLE_ADS = [
    {
        "product_name": "Holiday Tool Sale",
        "advertiser": "Pipestone True Value",
        "description": "Get ready for winter projects! 20% off all power tools, snow shovels, and ice melt this weekend only.",
        "category": "Hardware",
        "price": None,
        "original_price": None,
        "discount_percent": 20,
        "url": None,
    },
    {
        "product_name": "Prime Rib Friday",
        "advertiser": "Stonehouse & Quarry Lounge",
        "description": "Join us every Friday for our famous slow-roasted prime rib dinner. Includes salad bar and choice of potato.",
        "category": "Dining",
        "price": 24.99,
        "original_price": 29.99,
        "discount_percent": 17,
        "url": None,
    },
    {
        "product_name": "Weekly Grocery Specials",
        "advertiser": "Coborn's",
        "description": "Fresh holiday hams $2.99/lb, local eggs $3.49/dozen, and buy 2 get 1 free on all baking supplies.",
        "category": "Grocery",
        "price": None,
        "original_price": None,
        "discount_percent": 25,
        "url": None,
    },
    {
        "product_name": "Winter Auto Service Package",
        "advertiser": "S & S Truck Repair",
        "description": "Prepare your vehicle for winter! Oil change, tire rotation, battery check, and fluid top-off for one low price.",
        "category": "Automotive",
        "price": 79.99,
        "original_price": 120.00,
        "discount_percent": 33,
        "url": None,
    },
    {
        "product_name": "Holiday Floral Arrangements",
        "advertiser": "Pipestone Floral LLC",
        "description": "Beautiful poinsettias, holiday centerpieces, and custom arrangements. Order early for Christmas delivery!",
        "category": "Florist",
        "price": 39.99,
        "original_price": 49.99,
        "discount_percent": 20,
        "url": None,
    },
    {
        "product_name": "Home Insurance Bundle",
        "advertiser": "Kozlowski Insurance Agency",
        "description": "Bundle your home and auto insurance and save up to 15%. Free quotes available. Serving Pipestone since 1970.",
        "category": "Insurance",
        "price": None,
        "original_price": None,
        "discount_percent": 15,
        "url": None,
    },
    {
        "product_name": "Native American Art Collection",
        "advertiser": "Keepers Gift Shop & Gallery",
        "description": "Unique handcrafted gifts from tribal artists. Pipestone carvings, beadwork, and paintings. Perfect for holiday giving.",
        "category": "Gifts",
        "price": None,
        "original_price": None,
        "discount_percent": 10,
        "url": None,
    },
    {
        "product_name": "Fish Fry Fridays",
        "advertiser": "Fish & Chips",
        "description": "Southern soul food with a northern twist! All-you-can-eat fish fry every Friday. Located near Split Rock Creek State Park.",
        "category": "Dining",
        "price": 14.99,
        "original_price": 18.99,
        "discount_percent": 21,
        "url": None,
    },
    {
        "product_name": "Holiday Meat Bundles",
        "advertiser": "Hank's Foods",
        "description": "Stock up for the holidays! 10lb meat bundle includes ground beef, pork chops, and chicken breasts. Family owned since 1952.",
        "category": "Grocery",
        "price": 49.99,
        "original_price": 65.00,
        "discount_percent": 23,
        "url": None,
    },
    {
        "product_name": "Used Vehicle Winter Sale",
        "advertiser": "Dahl Motors",
        "description": "Dependable pre-owned cars, trucks, and SUVs ready for Minnesota winters. Financing available. Free vehicle history report.",
        "category": "Automotive",
        "price": None,
        "original_price": None,
        "discount_percent": 10,
        "url": None,
    },
]


def load_sample_ads() -> None:
    """Load sample advertisements into the database."""
    print("Loading sample advertisements...")
    print("=" * 50)

    # Set valid dates
    today = datetime.now()
    valid_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    valid_to = (today + timedelta(days=30)).strftime("%Y-%m-%d")

    loaded = 0
    for ad in SAMPLE_ADS:
        ad_id = str(uuid.uuid4())

        insert_advertisement(
            ad_id=ad_id,
            product_name=ad["product_name"],
            advertiser=ad["advertiser"],
            description=ad["description"],
            category=ad["category"],
            price=ad["price"],
            original_price=ad["original_price"],
            discount_percent=ad["discount_percent"],
            valid_from=valid_from,
            valid_to=valid_to,
            url=ad["url"],
        )
        loaded += 1
        print(f"  [{loaded}] {ad['product_name']} ({ad['category']}) - ${ad['price']}")

    print("\n" + "=" * 50)
    print(f"Loaded {loaded} advertisements")
    print(f"Total ads in database: {get_advertisement_count()}")


def main() -> None:
    """Run the ad loading script."""
    load_sample_ads()


if __name__ == "__main__":
    main()
