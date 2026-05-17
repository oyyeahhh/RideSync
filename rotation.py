"""
Per-group driver rotation tracker.
"""

import json
from pathlib import Path
from storage import group_dir


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "rotation.json"


def _load(group_id: str) -> dict:
    f = _file(group_id)
    if f.exists():
        return json.loads(f.read_text())
    return {"order": [], "current_index": 0}


def _save(data: dict, group_id: str) -> None:
    _file(group_id).write_text(json.dumps(data, indent=2))


def setup_rotation(family_ids: list[str], group_id: str) -> None:
    _save({"order": family_ids, "current_index": 0}, group_id)


def next_driver(group_id: str) -> str | None:
    data = _load(group_id)
    if not data["order"]:
        return None
    return data["order"][data["current_index"]]


def record_trip(group_id: str) -> str | None:
    data = _load(group_id)
    if not data["order"]:
        return None
    data["current_index"] = (data["current_index"] + 1) % len(data["order"])
    _save(data, group_id)
    return data["order"][data["current_index"]]


def set_driver(family_id: str, group_id: str) -> None:
    data = _load(group_id)
    if family_id not in data["order"]:
        raise ValueError(f"{family_id} not in rotation for group {group_id}")
    data["current_index"] = data["order"].index(family_id)
    _save(data, group_id)


def add_to_rotation(family_id: str, group_id: str) -> None:
    data = _load(group_id)
    if family_id not in data["order"]:
        data["order"].append(family_id)
        _save(data, group_id)
