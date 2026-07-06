"""
SMS sending via Twilio.
"""

import os
from datetime import timedelta
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")

# Twilio WhatsApp sandbox opt-in details (set these in .env / Railway)
# TWILIO_SANDBOX_NUMBER  – the sandbox phone number, e.g. +14155238886
# TWILIO_SANDBOX_KEYWORD – the join keyword shown in the Twilio console, e.g. "marble-apple"
SANDBOX_NUMBER = os.environ.get("TWILIO_SANDBOX_NUMBER", "+14155238886")
SANDBOX_KEYWORD = os.environ.get("TWILIO_SANDBOX_KEYWORD", "")


def send_sms(to_phone: str, message: str) -> None:
    if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER]):
        raise RuntimeError("Twilio credentials not set in .env")
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    to = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"
    from_ = FROM_NUMBER if FROM_NUMBER.startswith("whatsapp:") else f"whatsapp:{FROM_NUMBER}"
    client.messages.create(body=message, from_=from_, to=to)
    print(f"SMS sent to {to_phone}")


def send_route_sms(to_phone: str, result: dict, driver_name: str, dest_name: str,
                   maps_url: str, drive_url: str = "") -> None:
    depart = result["depart_at"]
    pickups = result["ordered_pickups"]
    legs = result["leg_durations_seconds"]

    lines = [f"Carpool route for {driver_name}"]
    lines.append(f"Leave at: {depart.strftime('%I:%M %p')}")
    lines.append("")
    lines.append("Pickup order:")

    current_time = depart
    for i, pickup in enumerate(pickups):
        current_time = current_time + timedelta(seconds=legs[i])
        lines.append(f"  {i+1}. {pickup['label']}  {current_time.strftime('%I:%M %p')}")

    lines.append(f"\nArrive at {dest_name} by {result['arrival_time'].strftime('%I:%M %p')}")
    lines.append(f"\nOpen in Maps:\n{maps_url}")
    if drive_url:
        lines.append(f"\nCheck kids in as you pick them up (parents get a ping):\n{drive_url}")
    lines.append("\nReminder: Open Google Maps -> tap your photo -> Share location -> send the link to the group.")

    send_sms(to_phone=to_phone, message="\n".join(lines))
