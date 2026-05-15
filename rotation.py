"""
Driver rotation tracker.

Stores the rotation order and current position in rotation.json.
Call next_driver() to get who drives next, and record_trip() after a trip runs.
"""

import json
from pathlib import Path
from storage import DATA_DIR

ROTATION_FILE = DATA_DIR / "rotation.json"


def _load() -> dict:
    if ROTATION_FILE.exists():
        return json.loads(ROTATION_FILE.read_text())
    return {"order": [], "current_index": 0}


def _save(data: dict) -> None:
    ROTATION_FILE.write_text(json.dumps(data, indent=2))


def setup_rotation(family_ids: list[str]) -> None:
    """Initialize the rotation order. Call once to set up."""
    _save({"order": family_ids, "current_index": 0})
    print(f"Rotation set: {' -> '.join(family_ids)} (repeating)")


def next_driver() -> str:
    """Returns the family_id of the next driver without advancing the rotation."""
    data = _load()
    if not data["order"]:
        raise RuntimeError("Rotation not set up. Run setup_rotation() first.")
    return data["order"][data["current_index"]]


def record_trip() -> str:
    """Advance the rotation after a trip. Returns the next driver's family_id."""
    data = _load()
    if not data["order"]:
        raise RuntimeError("Rotation not set up.")
    data["current_index"] = (data["current_index"] + 1) % len(data["order"])
    _save(data)
    return data["order"][data["current_index"]]


def set_driver(family_id: str) -> None:
    """Force a specific family to be next driver (used after a swap)."""
    data = _load()
    if family_id not in data["order"]:
        raise ValueError(f"{family_id} not in rotation")
    data["current_index"] = data["order"].index(family_id)
    _save(data)


def add_to_rotation(family_id: str) -> None:
    """Append a new family to the end of the rotation."""
    data = _load()
    if family_id not in data["order"]:
        data["order"].append(family_id)
        _save(data)


def show_rotation() -> None:
    """Print the current rotation state."""
    data = _load()
    if not data["order"]:
        print("Rotation not set up yet.")
        return
    order = data["order"]
    current = data["current_index"]
    print("Driver rotation:")
    for i, fid in enumerate(order):
        marker = " <- next" if i == current else ""
        print(f"  {i+1}. {fid}{marker}")
