"""
Geocoding: address string -> (latitude, longitude).

We cache results to geocode_cache.json so we only pay Google for each unique
address once. Cache key is the raw address string. If a family moves, change
the address string in families.py and it'll re-geocode that one entry.

Uses Google Geocoding API. ~$5 per 1000 requests, but we hit the free tier
for all realistic carpool use.
"""

import json
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
from storage import DATA_DIR
CACHE_FILE = DATA_DIR / "geocode_cache.json"


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def geocode(address: str) -> tuple[float, float]:
    """Returns (latitude, longitude) for the given address string.
    Caches results on disk so repeat calls are free."""
    if not API_KEY:
        raise RuntimeError(
            "GOOGLE_MAPS_API_KEY not set. Create a .env file with your key."
        )

    cache = _load_cache()
    if address in cache:
        return cache[address]["lat"], cache[address]["lng"]

    print(f"  Geocoding (API call): {address}")
    response = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": API_KEY},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    if data["status"] != "OK":
        raise RuntimeError(
            f"Geocoding failed for '{address}': {data['status']} "
            f"({data.get('error_message', 'no message')})"
        )

    location = data["results"][0]["geometry"]["location"]
    lat, lng = location["lat"], location["lng"]

    cache[address] = {"lat": lat, "lng": lng}
    _save_cache(cache)
    return lat, lng


def geocode_address(addr) -> None:
    """Fills in lat/long on an Address or Destination object in place."""
    if addr.is_geocoded:
        return
    lat, lng = geocode(addr.street)
    addr.latitude = lat
    addr.longitude = lng
