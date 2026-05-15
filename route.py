"""
v0 entry point. Run with:
    python3 route.py

Edit config.py to change the trip details.
"""

import json
from pathlib import Path

from config import ALL_FAMILY_IDS, arrival_time, get_destination_id, get_buffer_minutes, load_config
from families import get_family, get_destination
from geocode import geocode_address, geocode
from routing import compute_optimal_route, format_route_summary, build_maps_url
from sms import send_route_sms
from rotation import next_driver, record_trip
from trips import record_trip as log_trip
from route_cache import save as save_route_cache

CONFIRMATIONS_FILE = Path(__file__).parent / "confirmations.json"


def load_confirmations() -> dict:
    if CONFIRMATIONS_FILE.exists():
        return json.loads(CONFIRMATIONS_FILE.read_text())
    return {}


def main():
    trip_arrival = arrival_time()

    # Driver is picked automatically from the rotation
    driver_family_id = next_driver()
    pickup_family_ids = [f for f in ALL_FAMILY_IDS if f != driver_family_id]

    driver_family = get_family(driver_family_id)
    cfg = load_config()
    custom_dest_address = cfg.get("destination_address", "").strip()
    custom_dest_name = cfg.get("destination_name", "").strip()

    if custom_dest_address:
        # Use custom destination from dashboard
        from models import Destination
        destination = Destination(
            id="custom",
            group_id="grp_main",
            name=custom_dest_name or custom_dest_address,
            street=custom_dest_address,
        )
        geocode_address(destination)
    else:
        destination = get_destination(get_destination_id())

    print(f"\nPlanning trip:")
    print(f"  Driver:      {driver_family.name}")
    print(f"  Picking up:  {', '.join(get_family(f).name for f in pickup_family_ids)}")
    print(f"  Destination: {destination.name}")
    print(f"  Arrive by:   {trip_arrival.strftime('%a %b %d, %I:%M %p %Z')}")
    print()

    print("Geocoding addresses...")
    geocode_address(driver_family.primary_address)
    for fid in pickup_family_ids:
        geocode_address(get_family(fid).primary_address)
    geocode_address(destination)
    print("Done.\n")

    confirmations = load_confirmations()
    pickups = []
    for fid in pickup_family_ids:
        family = get_family(fid)
        phone = family.guardians[0].phone
        confirmed = confirmations.get(phone, "").strip().upper()

        if confirmed and confirmed != "YES":
            print(f"  {family.name} confirmed new address: {confirmations[phone]}")
            lat, lng = geocode(confirmations[phone])
            label = f"{family.name} (confirmed)"
        else:
            addr = family.primary_address
            lat, lng = addr.latitude, addr.longitude
            label = f"{family.name} ({addr.label})"
            if confirmed == "YES":
                print(f"  {family.name} confirmed default address")

        pickups.append({"id": family.id, "lat": lat, "lng": lng, "label": label})

    driver_addr = driver_family.primary_address
    result = compute_optimal_route(
        driver_lat=driver_addr.latitude,
        driver_lng=driver_addr.longitude,
        pickups=pickups,
        dest_lat=destination.latitude,
        dest_lng=destination.longitude,
        arrival_time=trip_arrival,
        buffer_minutes=get_buffer_minutes(),
    )

    save_route_cache(result, driver_name=driver_family.name, dest_name=destination.name)

    summary = format_route_summary(
        result,
        driver_label=f"{driver_family.name} (driver)",
        dest_label=destination.name,
    )
    print(summary)
    print()

    driver_phone = driver_family.guardians[0].phone
    if driver_phone:
        maps_url = build_maps_url(result)
        send_route_sms(
            to_phone=driver_phone,
            result=result,
            driver_name=driver_family.name,
            dest_name=destination.name,
            maps_url=maps_url,
        )
    else:
        print("No phone number for driver — skipping SMS.")

    log_trip(
        driver_family_id=driver_family_id,
        driver_name=driver_family.name,
        miles=result["total_miles"],
        minutes=result["total_seconds"] // 60,
        arrival=trip_arrival,
        pickup_family_ids=pickup_family_ids,
    )
    next_up = record_trip()
    print(f"Rotation advanced. Next driver: {next_up}")


if __name__ == "__main__":
    main()
