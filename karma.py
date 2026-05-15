"""
Karma tracking. Logs swap requests and covers to karma.json.
"""

import json
from pathlib import Path
from storage import DATA_DIR

KARMA_FILE = DATA_DIR / "karma.json"


def load_karma() -> dict:
    if KARMA_FILE.exists():
        return json.loads(KARMA_FILE.read_text())
    return {}


def save_karma(data: dict) -> None:
    KARMA_FILE.write_text(json.dumps(data, indent=2))


def _ensure(data: dict, family_id: str, family_name: str) -> None:
    if family_id not in data:
        data[family_id] = {"name": family_name, "requested": 0, "covered": 0}


def record_swap_request(family_id: str, family_name: str) -> None:
    data = load_karma()
    _ensure(data, family_id, family_name)
    data[family_id]["requested"] += 1
    save_karma(data)


def record_swap_cover(family_id: str, family_name: str) -> None:
    data = load_karma()
    _ensure(data, family_id, family_name)
    data[family_id]["covered"] += 1
    save_karma(data)


def get_karma() -> list:
    data = load_karma()
    result = []
    for fid, d in data.items():
        score = d["covered"] - d["requested"]
        result.append({
            "family_id": fid,
            "name": d["name"],
            "requested": d["requested"],
            "covered": d["covered"],
            "score": score,
        })
    result.sort(key=lambda x: x["score"], reverse=True)
    return result
