import json
from pathlib import Path
from storage import DATA_DIR

ABSENCES_FILE = DATA_DIR / "absences.json"


def _load() -> dict:
    if not ABSENCES_FILE.exists():
        return {}
    return json.loads(ABSENCES_FILE.read_text())


def _save(data: dict) -> None:
    ABSENCES_FILE.write_text(json.dumps(data, indent=2))


def toggle_absent(date: str, family_id: str) -> bool:
    """Toggle absence for a family on a given date. Returns True if now absent."""
    data = _load()
    data.setdefault(date, [])
    if family_id in data[date]:
        data[date].remove(family_id)
        _save(data)
        return False
    else:
        data[date].append(family_id)
        _save(data)
        return True


def get_absences(date: str) -> list[str]:
    return _load().get(date, [])
