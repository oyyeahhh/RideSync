"""
Per-group family storage. Each group's families live in its group directory.
All public functions take group_id as a parameter.
"""

import json
import uuid
from pathlib import Path

from models import Group, Family, Guardian, Kid, Address, Destination
from storage import DATA_DIR, CODE_DIR, group_dir, atomic_write_json, read_json

_SEED_FAMILIES_FILE = CODE_DIR / "families.json"


def _families_file(group_id: str) -> Path:
    return group_dir(group_id) / "families.json"


def _seed_data(group_id: str) -> list[dict]:
    """Return seed families for the legacy grp_main group, empty list otherwise."""
    if group_id == "grp_main" and _SEED_FAMILIES_FILE.exists():
        return json.loads(_SEED_FAMILIES_FILE.read_text())
    return []


def _load_families_json(group_id: str) -> list[dict]:
    f = _families_file(group_id)
    if f.exists():
        return read_json(f, default=[])
    return _seed_data(group_id)


def _save_families_json(data: list[dict], group_id: str) -> None:
    atomic_write_json(_families_file(group_id), data)


def _dict_to_family(d: dict, group_id: str) -> Family:
    return Family(
        id=d["id"],
        group_id=group_id,
        name=d["name"],
        guardians=[Guardian(
            id=f"g_{d['id']}",
            group_id=group_id,
            family_id=d["id"],
            name=d["name"],
            phone=d.get("phone", ""),
            email=d.get("email", ""),
            is_driver=True,
        )],
        kids=[Kid(id=f"kid_{i}_{d['id']}", group_id=group_id, family_id=d["id"], name=c)
              for i, c in enumerate(d.get("children", []))],
        addresses=[Address(
            id=f"addr_{d['id']}_home",
            group_id=group_id,
            family_id=d["id"],
            label="Home",
            street=d.get("address", ""),
        )],
    )


def get_all_family_ids(group_id: str) -> list[str]:
    return [f["id"] for f in _load_families_json(group_id)]


def add_family(name: str, address: str, phone: str, children: list[str], group_id: str) -> dict:
    """Create a new family entry, persist it, and return the dict."""
    slug = name.lower().split()[-1] if name else "family"
    family_id = f"fam_{slug}_{uuid.uuid4().hex[:4]}"
    entry = {
        "id": family_id,
        "name": name,
        "address": address,
        "phone": phone,
        "children": children,
    }
    data = _load_families_json(group_id)
    data.append(entry)
    _save_families_json(data, group_id)
    return entry


def get_family(family_id: str, group_id: str) -> Family:
    """Look up a family by ID within the given group."""
    for d in _load_families_json(group_id):
        if d["id"] == family_id:
            return _dict_to_family(d, group_id)
    raise ValueError(f"No family with id {family_id} in group {group_id}")


# Destinations are currently hardcoded globally; kept for backward compat.
_DESTINATIONS = [
    Destination(
        id="dest_powerup",
        group_id="grp_main",
        name="Power Up Arena",
        street="Garden State Plaza Blvd Store 2145, Paramus, NJ 07652",
    ),
    Destination(
        id="dest_main",
        group_id="",
        name="",
        street="",
    ),
]


def get_destination(dest_id: str) -> Destination:
    for d in _DESTINATIONS:
        if d.id == dest_id:
            return d
    # Return a blank destination rather than crashing
    return Destination(id=dest_id, group_id="", name="", street="")
