"""
Carpool group registry. Groups are the top-level isolation boundary.
Each group has its own families, schedule, rotation, config, etc.
The groups index lives at DATA_DIR/groups.json (not inside a group subdir).
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from storage import DATA_DIR, group_dir, atomic_write_json, read_json

GROUPS_FILE = DATA_DIR / "groups.json"

_DEFAULT_CONFIG = {
    "arrival_date": "",
    "arrival_time": "17:00",
    "return_time": "",
    "return_driver_family_id": "",
    "return_driver_name": "",
    "destination_name": "",
    "destination_address": "",
    "buffer_minutes": 10,
    "group_name": "",
    "timezone": "America/New_York",
    "destination_id": "dest_main",
    "assignment_mode": "auto",
}


def _load_groups() -> list:
    return read_json(GROUPS_FILE, default=[])


def _save_groups(groups: list) -> None:
    atomic_write_json(GROUPS_FILE, groups)


def create_group(name: str) -> dict:
    """Create a new group, initialize its data directory and default config."""
    groups = _load_groups()
    group_id = "grp_" + uuid.uuid4().hex[:8]
    group = {
        "id": group_id,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    groups.append(group)
    _save_groups(groups)

    # Write default config into the group's directory
    cfg = dict(_DEFAULT_CONFIG)
    cfg["group_name"] = name
    gdir = group_dir(group_id)
    atomic_write_json(gdir / "trip_config.json", cfg)

    return group


def get_group(group_id: str) -> dict | None:
    for g in _load_groups():
        if g["id"] == group_id:
            return g
    return None


def list_groups() -> list:
    return _load_groups()


def get_or_create_display_token(group_id: str) -> str:
    """Per-group opaque token for the kid-bulletin display URL. Lazily created
    so existing groups don't need a migration. Anyone with the URL can view
    the bulletin (read-only); regenerate to revoke."""
    import secrets
    groups = _load_groups()
    for g in groups:
        if g["id"] == group_id:
            if not g.get("display_token"):
                g["display_token"] = secrets.token_urlsafe(20)
                _save_groups(groups)
            return g["display_token"]
    raise ValueError(f"group {group_id!r} not found")


def regenerate_display_token(group_id: str) -> str:
    """Rotate the display token — old kid-bulletin URLs stop working."""
    import secrets
    groups = _load_groups()
    for g in groups:
        if g["id"] == group_id:
            g["display_token"] = secrets.token_urlsafe(20)
            _save_groups(groups)
            return g["display_token"]
    raise ValueError(f"group {group_id!r} not found")


def find_group_by_display_token(token: str) -> dict | None:
    if not token or len(token) < 12:
        return None
    for g in _load_groups():
        if g.get("display_token") == token:
            return g
    return None
