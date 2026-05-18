from pathlib import Path
from storage import group_dir, atomic_write_json, read_json


def _file(group_id: str) -> Path:
    return group_dir(group_id) / "absences.json"


def _load(group_id: str) -> dict:
    return read_json(_file(group_id), default={})


def _save(data: dict, group_id: str) -> None:
    atomic_write_json(_file(group_id), data)


def toggle_absent(date: str, family_id: str, group_id: str) -> bool:
    data = _load(group_id)
    data.setdefault(date, [])
    if family_id in data[date]:
        data[date].remove(family_id)
        _save(data, group_id)
        return False
    else:
        data[date].append(family_id)
        _save(data, group_id)
        return True


def get_absences(date: str, group_id: str) -> list[str]:
    return _load(group_id).get(date, [])
