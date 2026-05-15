"""
Edit this file with your actual carpool families.

For v0, the data lives in code. When we add a database, this gets replaced by
a seed script that loads the same data into Postgres. The shape doesn't change.

Use any unique IDs you want (uuid4, short strings, whatever). I'm using short
human-readable strings to make debugging easier.

Addresses don't need lat/long here — the geocoder fills those in and caches
them in geocode_cache.json on first run.
"""

import json
import uuid
from pathlib import Path

from models import Group, Family, Guardian, Kid, Address, Destination
from storage import DATA_DIR, CODE_DIR

FAMILIES_FILE = DATA_DIR / "families.json"
_SEED_FAMILIES_FILE = CODE_DIR / "families.json"  # seed file bundled with code


# The one group, for now.
GROUP = Group(id="grp_main", name="Teaneck Carpool")


FAMILIES = [
    Family(
        id="fam_nadler",
        group_id=GROUP.id,
        name="Nadler Family",
        guardians=[
            Guardian(
                id="g_nadler",
                group_id=GROUP.id,
                family_id="fam_nadler",
                name="Nadler Parent",
                phone="+19173997404",
                email="",
                is_driver=True,
            ),
        ],
        kids=[],
        addresses=[
            Address(
                id="addr_nadler_home",
                group_id=GROUP.id,
                family_id="fam_nadler",
                label="Home",
                street="1163 E Laurelton Parkway, Teaneck, NJ",
            ),
        ],
    ),
    Family(
        id="fam_bickel",
        group_id=GROUP.id,
        name="Bickel Family",
        guardians=[
            Guardian(
                id="g_bickel",
                group_id=GROUP.id,
                family_id="fam_bickel",
                name="Bickel Parent",
                phone="+19173997404",
                email="",
                is_driver=True,
            ),
        ],
        kids=[],
        addresses=[
            Address(
                id="addr_bickel_home",
                group_id=GROUP.id,
                family_id="fam_bickel",
                label="Home",
                street="1291 Princeton Rd, Teaneck, NJ",
            ),
        ],
    ),
    Family(
        id="fam_heiding",
        group_id=GROUP.id,
        name="Heiding Family",
        guardians=[
            Guardian(
                id="g_heiding",
                group_id=GROUP.id,
                family_id="fam_heiding",
                name="Heiding Parent",
                phone="+19173997404",
                email="",
                is_driver=True,
            ),
        ],
        kids=[],
        addresses=[
            Address(
                id="addr_heiding_home",
                group_id=GROUP.id,
                family_id="fam_heiding",
                label="Home",
                street="415 Sagamore Ave, Teaneck, NJ",
            ),
        ],
    ),
    Family(
        id="fam_tracer",
        group_id=GROUP.id,
        name="Tracer Family",
        guardians=[
            Guardian(
                id="g_tracer",
                group_id=GROUP.id,
                family_id="fam_tracer",
                name="Tracer Parent",
                phone="+19173997404",
                email="",
                is_driver=True,
            ),
        ],
        kids=[],
        addresses=[
            Address(
                id="addr_tracer_home",
                group_id=GROUP.id,
                family_id="fam_tracer",
                label="Home",
                street="335 Griggs Ave, Teaneck, NJ",
            ),
        ],
    ),
]


# === Destinations ===

DESTINATIONS = [
    Destination(
        id="dest_powerup",
        group_id=GROUP.id,
        name="Power Up Arena",
        street="Garden State Plaza Blvd Store 2145, Paramus, NJ 07652",
    ),
]


def _load_families_json() -> list[dict]:
    if FAMILIES_FILE.exists():
        return json.loads(FAMILIES_FILE.read_text())
    # On first deploy DATA_DIR may be empty — fall back to seed file in code dir
    if _SEED_FAMILIES_FILE.exists() and _SEED_FAMILIES_FILE != FAMILIES_FILE:
        return json.loads(_SEED_FAMILIES_FILE.read_text())
    return []


def _save_families_json(data: list[dict]) -> None:
    FAMILIES_FILE.write_text(json.dumps(data, indent=2))


def _dict_to_family(d: dict) -> Family:
    return Family(
        id=d["id"],
        group_id="grp_main",
        name=d["name"],
        guardians=[Guardian(
            id=f"g_{d['id']}",
            group_id="grp_main",
            family_id=d["id"],
            name=d["name"],
            phone=d.get("phone", ""),
            email=d.get("email", ""),
            is_driver=True,
        )],
        kids=[Kid(id=f"kid_{i}_{d['id']}", group_id="grp_main", family_id=d["id"], name=c)
              for i, c in enumerate(d.get("children", []))],
        addresses=[Address(
            id=f"addr_{d['id']}_home",
            group_id="grp_main",
            family_id=d["id"],
            label="Home",
            street=d.get("address", ""),
        )],
    )


def get_all_family_ids() -> list[str]:
    data = _load_families_json()
    if data:
        return [f["id"] for f in data]
    return [f.id for f in FAMILIES]


def add_family(name: str, address: str, phone: str, children: list[str]) -> dict:
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
    data = _load_families_json()
    data.append(entry)
    _save_families_json(data)
    return entry


def get_family(family_id: str) -> Family:
    """Look up a family by ID — checks families.json first, then hardcoded list."""
    for d in _load_families_json():
        if d["id"] == family_id:
            return _dict_to_family(d)
    for f in FAMILIES:
        if f.id == family_id:
            return f
    raise ValueError(f"No family with id {family_id}")


def get_destination(dest_id: str) -> Destination:
    for d in DESTINATIONS:
        if d.id == dest_id:
            return d
    raise ValueError(f"No destination with id {dest_id}")
