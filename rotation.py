"""
Per-group driver rotation tracker.
"""

from pathlib import Path
from storage import group_dir, atomic_write_json, read_json


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "rotation.json"


def _load(group_id: str) -> dict:
    return read_json(_file(group_id), default={"order": [], "current_index": 0})


def _save(data: dict, group_id: str) -> None:
    atomic_write_json(_file(group_id), data)


def setup_rotation(family_ids: list, group_id: str) -> None:
    _save({"order": family_ids, "current_index": 0}, group_id)


def next_driver(group_id: str, absent_ids: set | None = None) -> str | None:
    """Return the next driver, optionally skipping anyone in `absent_ids`.
    Does NOT advance the rotation pointer — read-only."""
    data = _load(group_id)
    order = data.get("order", [])
    if not order:
        return None
    idx = data.get("current_index", 0) % len(order)
    if not absent_ids:
        return order[idx]
    # Look forward from current index, wrapping; if everyone is absent, fall back.
    for i in range(len(order)):
        candidate = order[(idx + i) % len(order)]
        if candidate not in absent_ids:
            return candidate
    return order[idx]


def advance(group_id: str, absent_ids: set | None = None) -> str | None:
    """Advance the rotation pointer one step (skipping absent drivers).
    Returns the *new* current driver."""
    data = _load(group_id)
    order = data.get("order", [])
    if not order:
        return None
    cur = data.get("current_index", 0) % len(order)
    # Step forward at least once.
    new_idx = (cur + 1) % len(order)
    if absent_ids:
        for _ in range(len(order)):
            if order[new_idx] not in absent_ids:
                break
            new_idx = (new_idx + 1) % len(order)
    data["current_index"] = new_idx
    _save(data, group_id)
    return order[new_idx]


# Backward-compat alias — callers should migrate to `advance`.
record_trip = advance


def set_driver(family_id: str, group_id: str) -> None:
    data = _load(group_id)
    if family_id not in data["order"]:
        raise ValueError(f"{family_id} not in rotation for group {group_id}")
    data["current_index"] = data["order"].index(family_id)
    _save(data, group_id)


def set_index(index: int, group_id: str) -> None:
    """Set the current_index directly (used after generating recurring trips so
    the in-memory cursor advance is persisted)."""
    data = _load(group_id)
    if data["order"]:
        data["current_index"] = index % len(data["order"])
        _save(data, group_id)


def add_to_rotation(family_id: str, group_id: str) -> None:
    data = _load(group_id)
    if family_id not in data["order"]:
        data["order"].append(family_id)
        _save(data, group_id)


def remove_from_rotation(family_id: str, group_id: str) -> None:
    data = _load(group_id)
    if family_id not in data["order"]:
        return
    idx = data["order"].index(family_id)
    data["order"].remove(family_id)
    # Keep current_index sane after removal.
    if data["order"]:
        if idx < data.get("current_index", 0):
            data["current_index"] = max(0, data.get("current_index", 0) - 1)
        data["current_index"] = data["current_index"] % len(data["order"])
    else:
        data["current_index"] = 0
    _save(data, group_id)
