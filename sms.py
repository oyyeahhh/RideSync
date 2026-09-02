"""
SMS sending via Twilio.
"""

import logging
import os
from datetime import timedelta
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()
logger = logging.getLogger(__name__)

ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")

# Twilio WhatsApp sandbox opt-in details (set these in .env / Railway)
# TWILIO_SANDBOX_NUMBER  – the sandbox phone number, e.g. +14155238886
# TWILIO_SANDBOX_KEYWORD – the join keyword shown in the Twilio console, e.g. "marble-apple"
SANDBOX_NUMBER = os.environ.get("TWILIO_SANDBOX_NUMBER", "+14155238886")
SANDBOX_KEYWORD = os.environ.get("TWILIO_SANDBOX_KEYWORD", "")

# MESSAGE_CHANNEL decides how the number is addressed. "whatsapp" is the
# Twilio sandbox (recipients must have texted the join keyword within 72h);
# "sms" is plain text from FROM_NUMBER once carrier registration is approved.
# Read at call time so a Railway variable change needs no code change.
_VALID_CHANNELS = ("sms", "whatsapp")


def message_channel() -> str:
    ch = os.environ.get("MESSAGE_CHANNEL", "whatsapp").strip().lower()
    return ch if ch in _VALID_CHANNELS else "whatsapp"


def uses_whatsapp() -> bool:
    return message_channel() == "whatsapp"


def _address(number: str) -> str:
    """Strip any existing prefix, then apply the one the channel needs."""
    bare = number.split(":", 1)[1] if number.startswith("whatsapp:") else number
    return f"whatsapp:{bare}" if uses_whatsapp() else bare


def send_sms(to_phone: str, message: str) -> None:
    if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER]):
        raise RuntimeError("Twilio credentials not set in .env")
    # Without an explicit timeout the Twilio SDK waits forever, and the app has
    # only two request workers.
    from twilio.http.http_client import TwilioHttpClient
    client = Client(ACCOUNT_SID, AUTH_TOKEN,
                    http_client=TwilioHttpClient(timeout=10))
    client.messages.create(body=message, from_=_address(FROM_NUMBER), to=_address(to_phone))
    logger.info("%s message queued to %s***", message_channel(), to_phone[:6])


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
