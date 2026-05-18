from datetime import datetime
from pathlib import Path
from storage import group_dir, atomic_write_json, read_json


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "location.json"


def _load(group_id: str) -> dict:
    return read_json(_file(group_id), default={"active": False})


def _save(data: dict, group_id: str) -> None:
    atomic_write_json(_file(group_id), data)


def start_ride(driver_name: str, group_id: str, trip_leg: str = "outbound") -> None:
    data = _load(group_id)
    data["active"] = True
    data["driver_name"] = driver_name
    data["trip_leg"] = trip_leg
    data["started_at"] = datetime.now().isoformat()
    _save(data, group_id)


def stop_ride(group_id: str) -> None:
    _save({"active": False}, group_id)


def update_location(lat: float, lng: float, group_id: str) -> None:
    data = _load(group_id)
    data["lat"] = lat
    data["lng"] = lng
    data["updated_at"] = datetime.now().isoformat()
    _save(data, group_id)


def get_location(group_id: str) -> dict:
    return _load(group_id)
