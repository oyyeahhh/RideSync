"""
Per-group upcoming trip schedule.
"""

import json
import secrets
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from storage import group_dir, atomic_write_json, read_json, update_json

WEEKDAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "schedule.json"


def load_schedule(group_id: str) -> list:
    return read_json(_file(group_id), default=[])


def save_schedule(trips: list, group_id: str) -> None:
    atomic_write_json(_file(group_id), trips)


def add_trip(date: str, arrival_time: str, destination_name: str,
             destination_address: str, driver_family_id: str, driver_name: str,
             group_id: str, return_time: str = "", return_driver_family_id: str = "",
             return_driver_name: str = "") -> dict:
    trips = load_schedule(group_id)
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
    save_schedule(trips, group_id)
    return trip


def update_trip(trip_id: str, group_id: str, **fields) -> dict | None:
    """Update specific fields on an existing trip. Returns updated trip or None."""
    trips = load_schedule(group_id)
    for t in trips:
        if t["id"] == trip_id:
            t.update(fields)
            save_schedule(trips, group_id)
            return t
    return None


def remove_trip(trip_id: str, group_id: str) -> None:
    trips = [t for t in load_schedule(group_id) if t["id"] != trip_id]
    save_schedule(trips, group_id)


def remove_series(series_id: str, group_id: str) -> int:
    trips = load_schedule(group_id)
    before = len(trips)
    trips = [t for t in trips if t.get("series_id") != series_id]
    save_schedule(trips, group_id)
    return before - len(trips)


def get_trip(trip_id: str, group_id: str) -> dict | None:
    return next((t for t in load_schedule(group_id) if t["id"] == trip_id), None)


def get_or_create_drive_token(trip_id: str, group_id: str) -> str | None:
    """Opaque per-trip token for the driver's no-login day-of page
    (/drive/<token>). Same pattern as the kid-bulletin display token: the
    token IS the auth, so it must be unguessable. Returns None if the trip
    doesn't exist."""
    result = {}

    def mutate(trips):
        for t in trips:
            if t["id"] == trip_id:
                if not t.get("drive_token"):
                    t["drive_token"] = secrets.token_urlsafe(16)
                result["token"] = t["drive_token"]
        return trips

    update_json(_file(group_id), mutate, default=[])
    return result.get("token")


def find_trip_by_drive_token(token: str, group_id: str) -> dict | None:
    if not token or len(token) < 12:
        return None
    return next((t for t in load_schedule(group_id)
                 if t.get("drive_token") == token), None)


def record_checkin(trip_id: str, group_id: str, family_id: str,
                   undo: bool = False) -> tuple[dict | None, bool]:
    """Mark (or unmark) a family as picked up on a trip. Read-modify-write
    under a single lock so two quick taps can't drop each other.
    Returns (updated_trip, changed) — changed is False when the tap was a
    no-op (already checked in / already clear), which callers use to avoid
    duplicate WhatsApp pings."""
    result = {"trip": None, "changed": False}

    def mutate(trips):
        for t in trips:
            if t["id"] == trip_id:
                checkins = t.setdefault("checkins", {})
                if undo:
                    result["changed"] = checkins.pop(family_id, None) is not None
                elif family_id not in checkins:
                    checkins[family_id] = datetime.now().isoformat()
                    result["changed"] = True
                result["trip"] = t
        return trips

    update_json(_file(group_id), mutate, default=[])
    return result["trip"], result["changed"]


def claim_trip(trip_id: str, leg: str, family_id: str, family_name: str, group_id: str) -> dict | None:
    trips = load_schedule(group_id)
    for t in trips:
        if t["id"] == trip_id:
            if leg == "outbound":
                t["driver_family_id"] = family_id
                t["driver_name"] = family_name
            elif leg == "return":
                t["return_driver_family_id"] = family_id
                t["return_driver_name"] = family_name
            save_schedule(trips, group_id)
            return t
    return None


def add_recurring_trips(*, start_date: str, end_date: str, weekdays: list[int],
                         arrival_time: str, group_id: str, return_time: str = "",
                         return_driver_family_id: str = "", return_driver_name: str = "",
                         destination_name: str = "", destination_address: str = "",
                         driver_family_id: str = "", driver_name: str = "") -> list[dict]:
    series_id = str(uuid.uuid4())[:12]
    trips = load_schedule(group_id)
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
    save_schedule(trips, group_id)
    return created
