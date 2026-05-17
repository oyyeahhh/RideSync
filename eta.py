"""
Compute live ETAs from the driver's current position to each pickup family.
Uses the Routes API computeRouteMatrix endpoint (already enabled).
"""

import os
import requests
from datetime import datetime
from families import get_all_family_ids
from families import get_family
from geocode import geocode_address
from rotation import _load as load_rotation
from absences import get_absences

MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"


def compute_etas(driver_lat: float, driver_lng: float, trip_date: str, group_id: str = "grp_main") -> list[dict]:
    """
    Returns a list of dicts sorted by ETA:
      [{family_id, name, minutes, lat, lng}, ...]
    Families that are absent or are the driver are excluded.
    """
    rotation_data = load_rotation(group_id)
    order = rotation_data.get("order", get_all_family_ids(group_id))
    current_index = rotation_data.get("current_index", 0)
    driver_id = order[current_index] if order else None
    absences = get_absences(trip_date, group_id)

    pickup_families = []
    for fid in get_all_family_ids(group_id):
        if fid == driver_id or fid in absences:
            continue
        family = get_family(fid, group_id)
        addr = family.primary_address
        if not addr.latitude or not addr.longitude:
            geocode_address(addr)
        if addr.latitude and addr.longitude:
            pickup_families.append({
                "family_id": fid,
                "name": family.name,
                "lat": addr.latitude,
                "lng": addr.longitude,
            })

    if not pickup_families:
        return []

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    body = {
        "origins": [{"waypoint": {"location": {"latLng": {
            "latitude": driver_lat, "longitude": driver_lng
        }}}}],
        "destinations": [
            {"waypoint": {"location": {"latLng": {
                "latitude": f["lat"], "longitude": f["lng"]
            }}}}
            for f in pickup_families
        ],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "originIndex,destinationIndex,duration,status",
    }

    try:
        res = requests.post(MATRIX_URL, json=body, headers=headers, timeout=10)
        elements = res.json() if res.ok else []
        etas = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            status = el.get("status", {})
            if status and status.get("code", 0) != 0:
                continue
            dest_idx = el.get("destinationIndex", 0)
            duration_str = el.get("duration", "0s")
            secs = int(duration_str.rstrip("s")) if duration_str else 0
            mins = round(secs / 60)
            fam = pickup_families[dest_idx]
            etas.append({
                "family_id": fam["family_id"],
                "name": fam["name"],
                "minutes": mins,
                "lat": fam["lat"],
                "lng": fam["lng"],
                "label": "Arriving now" if mins == 0 else f"{mins} min",
            })
        return sorted(etas, key=lambda x: x["minutes"])
    except Exception:
        return []
