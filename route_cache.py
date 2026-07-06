"""
Per-group route cache, keyed by trip.

The old format was a single cache dict per group, so computing the route for
a second same-day trip overwrote the first — the kid bulletin and /arrived
would show pickup times belonging to the other trip. The file now holds
{"entries": {<trip_id or date>: {...}}}; the old single-dict format is still
readable (treated as one legacy entry) so existing volumes need no migration.
"""

from datetime import datetime, timedelta
from pathlib import Path
from storage import group_dir, atomic_write_json, read_json

# Plenty for a week of trips; prevents the file from growing forever.
_MAX_ENTRIES = 20


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "route_cache.json"


def _build_entry(result: dict, driver_name: str, dest_name: str, trip_id: str) -> dict:
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
    return {
        "computed_at": datetime.now().isoformat(),
        "trip_id": trip_id,
        "driver_name": driver_name,
        "dest_name": dest_name,
        "depart_at": depart.strftime("%I:%M %p"),
        "arrive_by": arrival.strftime("%I:%M %p"),
        "date": arrival.strftime("%Y-%m-%d"),
        "schedule": schedule,
        # Persist these so /arrived can credit the driver with real miles/minutes
        # in trip history (stats panel reads from trips.json).
        "total_miles": result.get("total_miles", 0),
        "leg_durations_seconds": legs,
    }


def _load_entries(group_id: str) -> dict:
    """Return the entries dict, converting the legacy single-entry format."""
    f = _file(group_id)
    if not f.exists():
        return {}
    data = read_json(f, default={})
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("entries"), dict):
        return data["entries"]
    if data.get("schedule") is not None:
        # Legacy single-entry file — key it by trip_id/date so it stays findable.
        key = data.get("trip_id") or data.get("date") or "legacy"
        return {key: data}
    return {}


def save(result: dict, driver_name: str, dest_name: str, group_id: str,
         trip_id: str = "") -> None:
    entry = _build_entry(result, driver_name, dest_name, trip_id)
    entries = _load_entries(group_id)
    entries[trip_id or entry["date"]] = entry
    if len(entries) > _MAX_ENTRIES:
        newest = sorted(entries.items(),
                        key=lambda kv: kv[1].get("computed_at", ""),
                        reverse=True)[:_MAX_ENTRIES]
        entries = dict(newest)
    atomic_write_json(_file(group_id), {"entries": entries})


def load(group_id: str, trip_id: str | None = None, date: str | None = None) -> dict | None:
    """Return the cached route for a trip.

    Match order: exact trip_id, then the newest entry for `date`, then — only
    when neither filter was given — the newest entry overall. Returns None if
    nothing matches; callers should still verify entry["date"] against the
    trip they're rendering.
    """
    entries = _load_entries(group_id)
    if not entries:
        return None
    candidates = sorted(entries.values(),
                        key=lambda e: e.get("computed_at", ""),
                        reverse=True)
    if trip_id:
        for e in candidates:
            if e.get("trip_id") == trip_id:
                return e
    if date:
        for e in candidates:
            if e.get("date") == date:
                return e
    if not trip_id and not date:
        return candidates[0]
    return None
