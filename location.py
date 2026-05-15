import json
from datetime import datetime
from pathlib import Path
from storage import DATA_DIR

LOCATION_FILE = DATA_DIR / "location.json"


def _load() -> dict:
    if not LOCATION_FILE.exists():
        return {"active": False}
    return json.loads(LOCATION_FILE.read_text())


def _save(data: dict) -> None:
    LOCATION_FILE.write_text(json.dumps(data, indent=2))


def start_ride(driver_name: str, trip_leg: str = "outbound") -> None:
    data = _load()
    data["active"] = True
    data["driver_name"] = driver_name
    data["trip_leg"] = trip_leg
    data["started_at"] = datetime.now().isoformat()
    _save(data)


def stop_ride() -> None:
    _save({"active": False})


def update_location(lat: float, lng: float) -> None:
    data = _load()
    data["lat"] = lat
    data["lng"] = lng
    data["updated_at"] = datetime.now().isoformat()
    _save(data)


def get_location() -> dict:
    return _load()
