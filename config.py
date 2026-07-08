"""
Per-group trip configuration stored in trip_config.json inside each group's directory.
All public functions take group_id as their first parameter.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from storage import group_dir, atomic_write_json, read_json, data_file_exists

# Keep ADMIN_PHONE for backwards compat with SMS webhook
ADMIN_PHONE = ""


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "trip_config.json"


def _default() -> dict:
    return {
        "arrival_date": "",
        "arrival_time": "17:00",
        "return_time": "",
        "return_driver_family_id": "",
        "return_driver_name": "",
        "destination_name": "",
        "destination_address": "",
        "buffer_minutes": 10,
        "group_name": "Carpool",
        "timezone": "America/New_York",
        "destination_id": "dest_main",
        "assignment_mode": "auto",
    }


def load_config(group_id: str) -> dict:
    f = _file(group_id)
    if not data_file_exists(f):
        return _default()
    return read_json(f, default=_default())


def save_config(data: dict, group_id: str) -> None:
    atomic_write_json(_file(group_id), data)


def arrival_time(group_id: str) -> datetime:
    cfg = load_config(group_id)
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    date_str = cfg.get("arrival_date", "")
    time_str = cfg.get("arrival_time", "17:00")
    if not date_str:
        # Default to today if not configured
        return datetime.now(tz)
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)


def get_destination_id(group_id: str) -> str:
    return load_config(group_id).get("destination_id", "dest_main")


def get_buffer_minutes(group_id: str) -> int:
    return load_config(group_id).get("buffer_minutes", 5)


def get_group_name(group_id: str) -> str:
    return load_config(group_id).get("group_name", "Carpool")


def get_assignment_mode(group_id: str) -> str:
    return load_config(group_id).get("assignment_mode", "auto")


def set_assignment_mode(mode: str, group_id: str) -> None:
    cfg = load_config(group_id)
    cfg["assignment_mode"] = mode
    save_config(cfg, group_id)
