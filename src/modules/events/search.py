"""Search functionality for events."""

import logging

from src.modules.events.database import get_all_event_categories, search_events

logger = logging.getLogger(__name__)


class EventSearch:
    """Search functionality for events."""

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        location: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        max_price: float | None = None,
        free_only: bool = False,
    ) -> list[dict]:
        """Search for local events.

        Args:
            query: Optional search query (for future semantic matching).
            category: Event category filter (Music, Sports, Arts, Food, Community).
            location: Location/venue filter (partial match).
            date_from: Start date filter (YYYY-MM-DD).
            date_to: End date filter (YYYY-MM-DD).
            max_price: Maximum price filter.
            free_only: Only return free events.

        Returns:
            List of matching events.
        """
        logger.info(
            f"Event search: category={category}, location={location}, "
            f"date_from={date_from}, date_to={date_to}, "
            f"max_price={max_price}, free_only={free_only}"
        )

        # Get events from database
        events = search_events(
            category=category,
            location=location,
            date_from=date_from,
            date_to=date_to,
            max_price=max_price,
            free_only=free_only,
        )

        # Format events as results
        results = []
        for event in events:
            # Format time info
            time_info = ""
            if event.get("event_date"):
                time_info = event["event_date"]
                if event.get("event_time"):
                    time_info += f" at {event['event_time']}"

            # Format price info
            price_info = (
                "Free"
                if event.get("price") is None or event.get("price") == 0
                else f"${event['price']:.2f}"
            )

            # Format location info
            location_info = event.get("location", "")
            if event.get("address"):
                location_info += f" ({event['address']})"

            result = {
                "text": f"{event['title']}: {event.get('description', '')} - {time_info} at {location_info}. {price_info}",
                "metadata": {
                    "event_id": event["event_id"],
                    "title": event["title"],
                    "location": event.get("location", ""),
                    "address": event.get("address", ""),
                    "event_date": event.get("event_date", ""),
                    "event_time": event.get("event_time", ""),
                    "category": event.get("category", ""),
                    "price": event.get("price"),
                    "url": event.get("url", ""),
                },
                "score": 1.0,
                "search_type": "event",
            }
            results.append(result)

        logger.info(f"Event search returned {len(results)} events")
        return results


def get_event_tools_schema() -> list[dict]:
    """Get the event tools schema with dynamic categories.

    Returns:
        List of tool definitions with actual category values from database.
    """
    categories = get_all_event_categories()
    if categories:
        category_desc = f"Event category. Available: {', '.join(categories)}"
    else:
        category_desc = "Event category"

    return [
        {
            "name": "search_events",
            "description": "Search for local events like concerts, sports, arts, food festivals, and community gatherings. Use when user asks about events, things to do, activities, or what's happening.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": category_desc,
                    },
                    "location": {
                        "type": "string",
                        "description": "Venue or area to search in (partial match)",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format",
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Maximum ticket price filter",
                    },
                    "free_only": {
                        "type": "boolean",
                        "description": "Only return free events",
                    },
                },
            },
        },
    ]


# Keep static schema for backward compatibility
EVENT_TOOLS_SCHEMA = get_event_tools_schema()
