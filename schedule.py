"""
Upcoming trip schedule. Stored in schedule.json.
Separate from trips.json (which is past trip history).
"""

import json
import uuid
from datetime import date, timedelta
from pathlib import Path
from storage import DATA_DIR

# Mon=0 … Sun=6  (matches Python's date.weekday())
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SCHEDULE_FILE = DATA_DIR / "schedule.json"


def load_schedule() -> list:
    if SCHEDULE_FILE.exists():
        return json.loads(SCHEDULE_FILE.read_text())
    return []


def save_schedule(trips: list) -> None:
    SCHEDULE_FILE.write_text(json.dumps(trips, indent=2))


def add_trip(date: str, arrival_time: str, destination_name: str,
             destination_address: str, driver_family_id: str, driver_name: str,
             return_time: str = "", return_driver_family_id: str = "",
             return_driver_name: str = "") -> dict:
    trips = load_schedule()
    trip = {
        "id": str(uuid.uuid4())[:8],
        "date": date,
        "arrival_time": arrival_time,
        "return_time": return_time,
        "return_driver_family_id": return_driver_family_id,
        "return_driver_name": return_driver_name,
        "destination_name": destination_name,
        "destination_address": destination_address,
        "driver_family_id": driver_family_id,
        "driver_name": driver_name,
    }
    trips.append(trip)
    trips.sort(key=lambda t: t["date"])
    save_schedule(trips)
    return trip


def remove_trip(trip_id: str) -> None:
    trips = [t for t in load_schedule() if t["id"] != trip_id]
    save_schedule(trips)


def remove_series(series_id: str) -> int:
    """Remove all trips that share the given series_id. Returns count removed."""
    trips = load_schedule()
    before = len(trips)
    trips = [t for t in trips if t.get("series_id") != series_id]
    save_schedule(trips)
    return before - len(trips)


def get_trip(trip_id: str) -> dict | None:
    return next((t for t in load_schedule() if t["id"] == trip_id), None)


def claim_trip(trip_id: str, leg: str, family_id: str, family_name: str) -> dict | None:
    """
    Assign family as driver for one leg of a trip.
    leg = "outbound" or "return". Returns updated trip or None if not found.
    """
    trips = load_schedule()
    for t in trips:
        if t["id"] == trip_id:
            if leg == "outbound":
                t["driver_family_id"] = family_id
                t["driver_name"] = family_name
            elif leg == "return":
                t["return_driver_family_id"] = family_id
                t["return_driver_name"] = family_name
            save_schedule(trips)
            return t
    return None


def add_recurring_trips(
    *,
    start_date: str,          # "YYYY-MM-DD" — first date (inclusive)
    end_date: str,            # "YYYY-MM-DD" — last date (inclusive)
    weekdays: list[int],      # e.g. [0,1,2,3,4] for Mon-Fri
    arrival_time: str,
    return_time: str = "",
    return_driver_family_id: str = "",
    return_driver_name: str = "",
    destination_name: str = "",
    destination_address: str = "",
    driver_family_id: str = "",
    driver_name: str = "",
) -> list[dict]:
    """
    Generate one trip per matching weekday in [start_date, end_date].
    All generated trips share a series_id so they can be bulk-deleted.
    Returns the list of created trip dicts.
    """
    series_id = str(uuid.uuid4())[:12]
    trips = load_schedule()
    created = []

    cur = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    while cur <= end:
        if cur.weekday() in weekdays:
            trip = {
                "id": str(uuid.uuid4())[:8],
                "series_id": series_id,
                "date": cur.isoformat(),
                "arrival_time": arrival_time,
                "return_time": return_time,
                "return_driver_family_id": return_driver_family_id,
                "return_driver_name": return_driver_name,
                "destination_name": destination_name,
                "destination_address": destination_address,
                "driver_family_id": driver_family_id,
                "driver_name": driver_name,
            }
            trips.append(trip)
            created.append(trip)
        cur += timedelta(days=1)

    trips.sort(key=lambda t: t["date"])
    save_schedule(trips)
    return created
