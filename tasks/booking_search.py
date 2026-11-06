"""
Demo task: Booking.com hotel search.

Demonstrates:
- Complex form (dates, guest count)
- Search results parsing
- Structured data extraction

Site: https://www.booking.com
Legal: Normal search usage, no reservation, no account creation.
       Booking.com allows automated search (not booking).
       We only extract publicly visible results (hotel names, prices).
"""

TASK = {
    "name": "search",
    "url": "https://www.booking.com",
    "goal": (
        "Search for hotels in Paris for 2 adults, check-in 2025-06-01, "
        "check-out 2025-06-05. Extract the names and prices of the "
        "first 5 results, and return them as a JSON list."
    ),
    "variables": {
        "destination": "Paris",
        "checkin": "2025-06-01",
        "checkout": "2025-06-05",
        "adults": "2",
    },
    "success_hint": "results list",
    "max_steps": 15,
    "extract_schema": {
        "hotels": [
            {"name": "string", "price": "string", "rating": "string"}
        ]
    },
}
