"""
Per-group trip history log.
"""

import json
from datetime import datetime
from pathlib import Path
from storage import group_dir, atomic_write_json, read_json


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "trips.json"


def load_trips(group_id: str) -> list:
    return read_json(_file(group_id), default=[])


def record_trip(driver_family_id: str, driver_name: str, miles: float,
                minutes: int, arrival: datetime, pickup_family_ids: list,
                group_id: str) -> None:
    trips = load_trips(group_id)
    trips.append({
        "date": arrival.strftime("%Y-%m-%d"),
        "driver_family_id": driver_family_id,
        "driver_name": driver_name,
        "miles": miles,
        "minutes": minutes,
        "pickups": pickup_family_ids,
    })
    atomic_write_json(_file(group_id), trips)


def get_stats(group_id: str) -> dict:
    trips = load_trips(group_id)
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
