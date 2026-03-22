#!/usr/bin/env python
"""Load sample events into the database."""

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.modules.events import insert_event, get_event_count

# Sample events - Pipestone, MN area
SAMPLE_EVENTS = [
    {
        "title": "Pipestone Holiday Parade",
        "description": "Annual holiday parade down Main Street featuring floats, marching bands, and Santa Claus. Hot cocoa available at the Pipestone Area Chamber tent.",
        "location": "Main Street",
        "address": "Main Street, Pipestone, MN 56164",
        "category": "Community",
        "time": "14:00",
        "end_time": "16:00",
        "price": None,
        "url": None,
        "days_from_now": 5,
    },
    {
        "title": "Tour of Homes",
        "description": "Visit beautifully decorated historic homes in Pipestone during this popular holiday tradition. Tickets available at the Chamber office.",
        "location": "Various Historic Homes",
        "address": "Pipestone, MN 56164",
        "category": "Community",
        "time": "13:00",
        "end_time": "17:00",
        "price": 15.00,
        "url": None,
        "days_from_now": 12,
    },
    {
        "title": "Live Music at Stonehouse",
        "description": "Enjoy live country and rock music from local band Prairie Wind while enjoying dinner and drinks.",
        "location": "Stonehouse & Quarry Lounge",
        "address": "106 E Main St, Pipestone, MN 56164",
        "category": "Entertainment",
        "time": "19:00",
        "end_time": "22:00",
        "price": None,
        "url": None,
        "days_from_now": 11,
    },
    {
        "title": "Pipestone National Monument Winter Walk",
        "description": "Guided winter walk through the historic pipestone quarries. Learn about the cultural significance of this sacred site. Dress warmly.",
        "location": "Pipestone National Monument",
        "address": "36 Reservation Ave, Pipestone, MN 56164",
        "category": "Education",
        "time": "10:00",
        "end_time": "12:00",
        "price": None,
        "url": None,
        "days_from_now": 19,
    },
    {
        "title": "Christmas Eve Service",
        "description": "Traditional candlelight Christmas Eve service with carols and special music. All are welcome.",
        "location": "First Lutheran Church",
        "address": "313 3rd St SE, Pipestone, MN 56164",
        "category": "Religious",
        "time": "17:00",
        "end_time": "18:30",
        "price": None,
        "url": None,
        "days_from_now": 22,
    },
    {
        "title": "Pipestone County Historical Society Open House",
        "description": "Explore the beautiful Carnegie library building and local history exhibits. Refreshments served. Free admission.",
        "location": "Pipestone County Museum",
        "address": "113 S Hiawatha Ave, Pipestone, MN 56164",
        "category": "Education",
        "time": "11:00",
        "end_time": "15:00",
        "price": None,
        "url": None,
        "days_from_now": 6,
    },
    {
        "title": "Youth Basketball Tournament",
        "description": "Area youth basketball teams compete in this annual holiday tournament. Concessions available.",
        "location": "Pipestone Area High School",
        "address": "1401 7th St SW, Pipestone, MN 56164",
        "category": "Sports",
        "time": "09:00",
        "end_time": "17:00",
        "price": 5.00,
        "url": None,
        "days_from_now": 26,
    },
    {
        "title": "New Year's Eve Celebration",
        "description": "Ring in 2026 with live music, champagne toast, and party favors. Reservations recommended.",
        "location": "Stonehouse & Quarry Lounge",
        "address": "106 E Main St, Pipestone, MN 56164",
        "category": "Entertainment",
        "time": "20:00",
        "end_time": "01:00",
        "price": 25.00,
        "url": None,
        "days_from_now": 29,
    },
    {
        "title": "Pancake Breakfast Fundraiser",
        "description": "All-you-can-eat pancakes, sausage, and eggs. Proceeds benefit the Pipestone Fire Department equipment fund.",
        "location": "Pipestone Fire Hall",
        "address": "119 2nd Ave SW, Pipestone, MN 56164",
        "category": "Community",
        "time": "07:00",
        "end_time": "11:00",
        "price": 8.00,
        "url": None,
        "days_from_now": 13,
    },
    {
        "title": "Santa at Fort Pipestone",
        "description": "Meet Santa in the historic log cabin setting! Free photos with Santa, hot cocoa, and holiday treats for the kids.",
        "location": "Fort Pipestone",
        "address": "Highway 75 N, Pipestone, MN 56164",
        "category": "Family",
        "time": "10:00",
        "end_time": "14:00",
        "price": None,
        "url": None,
        "days_from_now": 18,
    },
]


def load_sample_events() -> None:
    """Load sample events into the database."""
    print("Loading sample events...")
    print("=" * 50)

    today = datetime.now()

    loaded = 0
    for event in SAMPLE_EVENTS:
        event_id = str(uuid.uuid4())

        # Calculate event date
        event_date = (today + timedelta(days=event["days_from_now"])).strftime(
            "%Y-%m-%d"
        )

        insert_event(
            event_id=event_id,
            title=event["title"],
            description=event["description"],
            location=event["location"],
            address=event["address"],
            event_date=event_date,
            event_time=event["time"],
            end_time=event.get("end_time"),
            category=event["category"],
            price=event.get("price"),
            url=event["url"],
        )
        loaded += 1

        price_str = f"${event['price']:.2f}" if event.get("price") else "Free"
        print(
            f"  [{loaded}] {event['title']} ({event['category']}) - {event_date} - {price_str}"
        )

    print("\n" + "=" * 50)
    print(f"Loaded {loaded} events")
    print(f"Total events in database: {get_event_count()}")


def main() -> None:
    """Run the event loading script."""
    load_sample_events()


if __name__ == "__main__":
    main()
