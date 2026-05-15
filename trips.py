"""
Trip history log. Each completed trip is saved to trips.json.
"""

import json
from datetime import datetime
from pathlib import Path
from storage import DATA_DIR

TRIPS_FILE = DATA_DIR / "trips.json"


def load_trips() -> list:
    if TRIPS_FILE.exists():
        return json.loads(TRIPS_FILE.read_text())
    return []


def record_trip(driver_family_id: str, driver_name: str, miles: float, minutes: int, arrival: datetime, pickup_family_ids: list) -> None:
    trips = load_trips()
    trips.append({
        "date": arrival.strftime("%Y-%m-%d"),
        "driver_family_id": driver_family_id,
        "driver_name": driver_name,
        "miles": miles,
        "minutes": minutes,
        "pickups": pickup_family_ids,
    })
    TRIPS_FILE.write_text(json.dumps(trips, indent=2))


def get_stats() -> dict:
    """Returns per-family stats: trip count, total miles, and total drive time."""
    trips = load_trips()
    stats = {}
    for trip in trips:
        fid = trip["driver_family_id"]
        if fid not in stats:
            stats[fid] = {"name": trip["driver_name"], "trips": 0, "miles": 0.0, "minutes": 0}
        stats[fid]["trips"] += 1
        stats[fid]["miles"] += trip.get("miles", 0)
        stats[fid]["minutes"] += trip.get("minutes", 0)
    for fid in stats:
        stats[fid]["miles"] = round(stats[fid]["miles"], 1)
    return stats
