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

from maps_keys import server_key
from storage import DATA_DIR, atomic_write_json, read_json
CACHE_FILE = DATA_DIR / "geocode_cache.json"


def _load_cache() -> dict:
    return read_json(CACHE_FILE, default={})


def _save_cache(cache: dict) -> None:
    atomic_write_json(CACHE_FILE, cache)


def geocode(address: str) -> tuple[float, float]:
    """Returns (latitude, longitude) for the given address string.
    Caches results on disk so repeat calls are free."""
    api_key = server_key()
    if not api_key:
        raise RuntimeError(
            "No Google Maps server key. Set GOOGLE_MAPS_SERVER_KEY (restricted "
            "to the Geocoding and Routes APIs) in your environment."
        )

    cache = _load_cache()
    if address in cache:
        return cache[address]["lat"], cache[address]["lng"]

    print(f"  Geocoding (API call): {address}")
    response = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": api_key},
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
