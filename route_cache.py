"""
Per-group route cache.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from storage import group_dir


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "route_cache.json"


def save(result: dict, driver_name: str, dest_name: str, group_id: str) -> None:
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
    _file(group_id).write_text(json.dumps(cache, indent=2))


def load(group_id: str) -> dict | None:
    f = _file(group_id)
    if not f.exists():
        return None
    return json.loads(f.read_text())
