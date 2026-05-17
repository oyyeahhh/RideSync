"""
Central data directory.

Locally: data files sit next to the code.
On Railway: set DATA_DIR=/data and mount a volume there — data survives deploys.
"""

import os
import shutil
from pathlib import Path

CODE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(CODE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def group_dir(group_id: str) -> Path:
    """Return (and create) the per-group data directory."""
    d = DATA_DIR / "groups" / group_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# Legacy flat files that lived directly in DATA_DIR before multi-tenancy.
_LEGACY_FILES = [
    "trip_config.json", "families.json", "rotation.json", "schedule.json",
    "trips.json", "absences.json", "karma.json", "location.json",
    "route_cache.json", "swap_state.json", "confirmations.json",
]


def migrate_legacy_data(legacy_group_id: str = "grp_main") -> bool:
    """
    One-time migration: copy legacy flat data files into the group subdirectory.
    Safe to call repeatedly — only copies if source exists and dest doesn't.
    Returns True if any files were migrated.
    """
    gdir = group_dir(legacy_group_id)
    migrated = False
    for fname in _LEGACY_FILES:
        src = DATA_DIR / fname
        dst = gdir / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            migrated = True
    return migrated
