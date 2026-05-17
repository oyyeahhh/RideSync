"""
Per-group karma tracking.
"""

import json
from pathlib import Path
from storage import group_dir


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "karma.json"


def _load_karma(group_id: str) -> dict:
    f = _file(group_id)
    if f.exists():
        return json.loads(f.read_text())
    return {}


def _save_karma(data: dict, group_id: str) -> None:
    _file(group_id).write_text(json.dumps(data, indent=2))


def _ensure(data: dict, family_id: str, family_name: str) -> None:
    if family_id not in data:
        data[family_id] = {"name": family_name, "requested": 0, "covered": 0}


def record_swap_request(family_id: str, family_name: str, group_id: str) -> None:
    data = _load_karma(group_id)
    _ensure(data, family_id, family_name)
    data[family_id]["requested"] += 1
    _save_karma(data, group_id)


def record_swap_cover(family_id: str, family_name: str, group_id: str) -> None:
    data = _load_karma(group_id)
    _ensure(data, family_id, family_name)
    data[family_id]["covered"] += 1
    _save_karma(data, group_id)


def get_karma(group_id: str) -> list:
    data = _load_karma(group_id)
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
