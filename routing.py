"""
Route optimization via Google Routes API.

Given:
- driver's home (lat/lng)
- N pickup addresses (lat/lng each)
- destination (lat/lng)
- desired arrival time

Returns:
- the optimal pickup order
- estimated travel time accounting for traffic
- recommended departure time so the driver arrives on time

We use Google's Routes API with `optimizeWaypointOrder=True`, which solves
the traveling-salesman piece for us in one call. For 3-7 pickups this is
overkill (you could brute-force every permutation in <1ms) but the API also
gives us traffic-aware travel times in the same call, so it's the right tool.
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def compute_optimal_route(
    driver_lat: float,
    driver_lng: float,
    pickups: list[dict],  # [{"id": ..., "lat": ..., "lng": ..., "label": ...}]
    dest_lat: float,
    dest_lng: float,
    arrival_time: datetime,
    buffer_minutes: int = 5,
) -> dict:
    """
    Compute the optimal pickup order and departure time.

    Args:
        driver_lat/lng: driver's starting location
        pickups: list of dicts, each with id/lat/lng/label
        dest_lat/lng: destination
        arrival_time: when the driver needs to arrive at the destination (timezone-aware)
        buffer_minutes: how much extra to budget on top of the predicted time

    Returns dict with:
        - ordered_pickups: pickups in optimal order
        - total_seconds: predicted travel time
        - depart_at: datetime to leave home
        - leg_durations_seconds: time for each leg
    """
    if not API_KEY:
        raise RuntimeError(
            "GOOGLE_MAPS_API_KEY not set. Create a .env file with your key."
        )

    if arrival_time.tzinfo is None:
        raise ValueError("arrival_time must be timezone-aware")

    # Build the Routes API request. We pass arrival_time so traffic prediction
    # is anchored to when the trip actually happens.
    body = {
        "origin": {"location": {"latLng": {"latitude": driver_lat, "longitude": driver_lng}}},
        "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lng}}},
        "intermediates": [
            {"location": {"latLng": {"latitude": p["lat"], "longitude": p["lng"]}}}
            for p in pickups
        ],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "optimizeWaypointOrder": True,
        "arrivalTime": arrival_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # FieldMask is required: tells the API exactly what to return. Without it
    # you get an error. We need the optimized order, total duration, and leg
    # durations.
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": (
            "routes.duration,"
            "routes.distanceMeters,"
            "routes.optimizedIntermediateWaypointIndex,"
            "routes.legs.duration"
        ),
    }

    response = requests.post(ROUTES_URL, json=body, headers=headers, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"Routes API error {response.status_code}: {response.text}")

    data = response.json()
    if not data.get("routes"):
        raise RuntimeError(f"Routes API returned no routes: {data}")

    route = data["routes"][0]

    # duration comes back as a string like "1234s"
    total_seconds = int(route["duration"].rstrip("s"))
    total_miles = round(route.get("distanceMeters", 0) / 1609.34, 1)

    # The optimized order is given as indices into our `pickups` list.
    optimized_indices = route.get("optimizedIntermediateWaypointIndex", list(range(len(pickups))))
    ordered_pickups = [pickups[i] for i in optimized_indices]

    # Per-leg durations (driver -> pickup 1 -> pickup 2 -> ... -> destination)
    leg_durations = [int(leg["duration"].rstrip("s")) for leg in route["legs"]]

    # When to leave: arrival - travel time - buffer
    depart_at = arrival_time - timedelta(seconds=total_seconds) - timedelta(minutes=buffer_minutes)

    return {
        "ordered_pickups": ordered_pickups,
        "total_seconds": total_seconds,
        "total_miles": total_miles,
        "depart_at": depart_at,
        "leg_durations_seconds": leg_durations,
        "arrival_time": arrival_time,
        "buffer_minutes": buffer_minutes,
        "driver_lat": driver_lat,
        "driver_lng": driver_lng,
        "dest_lat": dest_lat,
        "dest_lng": dest_lng,
    }


def format_route_summary(result: dict, driver_label: str, dest_label: str) -> str:
    """Pretty-print the route for a human reader."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"Route plan: {driver_label} -> {dest_label}")
    lines.append("=" * 60)

    arrival = result["arrival_time"]
    depart = result["depart_at"]
    total_min = result["total_seconds"] // 60
    buf = result["buffer_minutes"]

    lines.append(f"Arrival target:  {arrival.strftime('%a %b %d, %I:%M %p %Z')}")
    lines.append(f"Drive time:      {total_min} min (with traffic prediction)")
    lines.append(f"Buffer:          {buf} min")
    lines.append(f"Leave at:        {depart.strftime('%I:%M %p')}")
    lines.append("")
    lines.append("Pickup order:")

    current_time = depart
    legs = result["leg_durations_seconds"]
    pickups = result["ordered_pickups"]

    # legs[0] is driver-home -> first pickup
    # legs[i] for i in 1..N-1 is pickup i-1 -> pickup i
    # legs[N] is last pickup -> destination
    for i, pickup in enumerate(pickups):
        current_time = current_time + timedelta(seconds=legs[i])
        lines.append(f"  {i+1}. {pickup['label']:30s}  at {current_time.strftime('%I:%M %p')}")

    # Final leg to destination
    current_time = current_time + timedelta(seconds=legs[-1])
    lines.append(f"  -> {dest_label:29s}  at {current_time.strftime('%I:%M %p')}")
    lines.append("=" * 60)

    # Google Maps link with waypoints in optimal order
    origin = f"{result['driver_lat']},{result['driver_lng']}"
    destination = f"{result['dest_lat']},{result['dest_lng']}"
    waypoints = "|".join(f"{p['lat']},{p['lng']}" for p in pickups)
    maps_url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(origin)}"
        f"&destination={quote_plus(destination)}"
        f"&waypoints={quote_plus(waypoints)}"
        f"&travelmode=driving"
    )
    lines.append("")
    lines.append("Google Maps link:")
    lines.append(build_maps_url(result))

    return "\n".join(lines)


def build_maps_url(result: dict) -> str:
    """Build a Google Maps URL with waypoints in optimal order."""
    origin = f"{result['driver_lat']},{result['driver_lng']}"
    destination = f"{result['dest_lat']},{result['dest_lng']}"
    waypoints = "|".join(f"{p['lat']},{p['lng']}" for p in result["ordered_pickups"])
    return (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(origin)}"
        f"&destination={quote_plus(destination)}"
        f"&waypoints={quote_plus(waypoints)}"
        f"&travelmode=driving"
    )
