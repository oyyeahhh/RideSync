"""
Shared trip configuration. Edit via the dashboard or trip_config.json directly.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from storage import DATA_DIR

CONFIG_FILE = DATA_DIR / "trip_config.json"

def _load_family_ids() -> list[str]:
    families_file = Path(__file__).parent / "families.json"
    if families_file.exists():
        return [f["id"] for f in json.loads(families_file.read_text())]
    return ["fam_nadler", "fam_bickel", "fam_heiding", "fam_tracer"]

ALL_FAMILY_IDS = _load_family_ids()
ADMIN_PHONE = "+19173997404"


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text())


def save_config(data: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def arrival_time() -> datetime:
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    dt_str = f"{cfg['arrival_date']} {cfg['arrival_time']}"
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=tz)


def get_destination_id() -> str:
    return load_config()["destination_id"]


def get_buffer_minutes() -> int:
    return load_config().get("buffer_minutes", 5)


def get_group_name() -> str:
    return load_config().get("group_name", "Carpool")


def get_assignment_mode() -> str:
    """'auto' = rotation assigns drivers; 'manual' = families volunteer."""
    return load_config().get("assignment_mode", "auto")


def set_assignment_mode(mode: str) -> None:
    cfg = load_config()
    cfg["assignment_mode"] = mode
    save_config(cfg)


# Keep these as module-level constants for backwards compat
DESTINATION_ID = property(get_destination_id)
BUFFER_MINUTES = 5
