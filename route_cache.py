"""
Caches the last computed route so the bulletin page can show pickup times.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from storage import DATA_DIR

CACHE_FILE = DATA_DIR / "route_cache.json"


def save(result: dict, driver_name: str, dest_name: str) -> None:
    """Save pickup schedule from a compute_optimal_route result."""
    depart = result["depart_at"]
    legs = result["leg_durations_seconds"]
    pickups = result["ordered_pickups"]

    schedule = []
    current_time = depart
    for i, pickup in enumerate(pickups):
        current_time = current_time + timedelta(seconds=legs[i])
        schedule.append({
            "family_id": pickup["id"],
            "label": pickup["label"],
            "pickup_time": current_time.strftime("%I:%M %p"),
        })

    arrival = result["arrival_time"]
    cache = {
        "computed_at": datetime.now().isoformat(),
        "driver_name": driver_name,
        "dest_name": dest_name,
        "depart_at": depart.strftime("%I:%M %p"),
        "arrive_by": arrival.strftime("%I:%M %p"),
        "date": arrival.strftime("%Y-%m-%d"),
        "schedule": schedule,
    }
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def load() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    return json.loads(CACHE_FILE.read_text())
