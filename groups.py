"""
Carpool group registry. Groups are the top-level isolation boundary.
Each group has its own families, schedule, rotation, config, etc.
The groups index lives at DATA_DIR/groups.json (not inside a group subdir).
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from storage import DATA_DIR, group_dir

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
    if GROUPS_FILE.exists():
        return json.loads(GROUPS_FILE.read_text())
    return []


def _save_groups(groups: list) -> None:
    GROUPS_FILE.write_text(json.dumps(groups, indent=2))


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
    (gdir / "trip_config.json").write_text(json.dumps(cfg, indent=2))

    return group


def get_group(group_id: str) -> dict | None:
    for g in _load_groups():
        if g["id"] == group_id:
            return g
    return None


def list_groups() -> list:
    return _load_groups()
