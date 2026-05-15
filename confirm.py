"""
Send nightly confirmation texts to all pickup families.

Run the night before a trip:
    python3 confirm.py

Each parent gets a text like:
    "Carpool tomorrow! Pickup at 1163 E Laurelton Parkway, Teaneck, NJ
     around 4:42 PM. Reply YES to confirm or reply with a different address."

Their replies are stored in confirmations.json by the webhook server.
Run route.py the next day — it will use confirmed addresses automatically.
"""

import json
from pathlib import Path

from config import ALL_FAMILY_IDS, arrival_time
from families import get_family
from rotation import next_driver
from sms import send_sms

CONFIRMATIONS_FILE = Path(__file__).parent / "confirmations.json"


def send_confirmations():
    trip_arrival = arrival_time()
    driver_id = next_driver()
    pickup_ids = [f for f in ALL_FAMILY_IDS if f != driver_id]

    # Clear previous confirmations
    CONFIRMATIONS_FILE.write_text(json.dumps({}))

    for fid in pickup_ids:
        family = get_family(fid)
        phone = family.guardians[0].phone
        if not phone:
            print(f"No phone for {family.name}, skipping")
            continue

        address = family.primary_address.street
        msg = (
            f"Carpool tomorrow! Pickup at {address}. "
            f"Arrive at destination by {trip_arrival.strftime('%I:%M %p')}. "
            f"Reply YES to confirm or reply with a different address."
        )
        send_sms(to_phone=phone, message=msg)
        print(f"Confirmation sent to {family.name} ({phone})")


if __name__ == "__main__":
    send_confirmations()
