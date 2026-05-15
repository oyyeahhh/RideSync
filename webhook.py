"""
Webhook server that receives SMS replies from parents.

Handles:
- YES / address replies (nightly confirmations)
- SWAP <reason> (driver swap requests)
- YES replies to swap requests (first YES wins)

Run with:
    python3 webhook.py
"""

import json
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

from config import ALL_FAMILY_IDS, ADMIN_PHONE, arrival_time
from families import get_family
from rotation import next_driver, set_driver
from sms import send_sms
from karma import record_swap_request, record_swap_cover

CONFIRMATIONS_FILE = Path(__file__).parent / "confirmations.json"
SWAP_FILE = Path(__file__).parent / "swap_state.json"


def load_confirmations() -> dict:
    if CONFIRMATIONS_FILE.exists():
        return json.loads(CONFIRMATIONS_FILE.read_text())
    return {}


def save_confirmations(data: dict) -> None:
    CONFIRMATIONS_FILE.write_text(json.dumps(data, indent=2))


def load_swap() -> dict:
    if SWAP_FILE.exists():
        return json.loads(SWAP_FILE.read_text())
    return {}


def save_swap(data: dict) -> None:
    SWAP_FILE.write_text(json.dumps(data, indent=2))


def phone_to_family(phone: str):
    """Find which family a phone number belongs to."""
    for fid in ALL_FAMILY_IDS:
        family = get_family(fid)
        if family.guardians[0].phone == phone:
            return family
    return None


def handle_swap(from_phone: str, reason: str) -> str:
    """Handle a SWAP request. Returns the reply to send back."""
    family = phone_to_family(from_phone)
    if not family:
        return "Sorry, your number isn't registered in this carpool."

    # Check if this person is the current driver
    current_driver_id = next_driver()
    if family.id != current_driver_id:
        return "You're not the driver for the next trip, so no swap needed."

    # Check if it's more than 1 full day before the trip
    now = datetime.now(timezone.utc)
    trip_time = arrival_time()
    cutoff = trip_time - timedelta(days=1)

    if now >= cutoff:
        admin_family = phone_to_family(ADMIN_PHONE)
        admin_name = admin_family.name if admin_family else "the admin"
        return (
            f"It's less than 1 day before the trip — too late to swap automatically. "
            f"Please contact {admin_name} directly."
        )

    # Ask all other drivers
    other_families = [get_family(fid) for fid in ALL_FAMILY_IDS if fid != family.id]
    asked_phones = []
    for other in other_families:
        phone = other.guardians[0].phone
        if phone:
            msg = (
                f"{family.name} can't drive on "
                f"{trip_time.strftime('%a %b %d')} ({reason}). "
                f"Can you take over? Reply YES to volunteer."
            )
            send_sms(to_phone=phone, message=msg)
            asked_phones.append(phone)

    # Log karma
    record_swap_request(family.id, family.name)

    # Save swap state
    save_swap({
        "pending": True,
        "original_driver_id": family.id,
        "original_driver_phone": from_phone,
        "reason": reason,
        "asked_phones": asked_phones,
        "confirmed_driver_id": None,
    })

    return f"Got it! Asked {len(asked_phones)} other drivers. We'll let you know who takes over."


def handle_yes_for_swap(from_phone: str) -> str:
    """Handle a YES reply to a swap request. First YES wins."""
    swap = load_swap()
    if not swap.get("pending"):
        return None  # not a swap reply, handle as confirmation

    if from_phone not in swap.get("asked_phones", []):
        return None  # not someone we asked

    if swap.get("confirmed_driver_id"):
        return "Thanks, but someone already volunteered to drive!"

    # First YES — assign them as driver
    family = phone_to_family(from_phone)
    if not family:
        return None

    set_driver(family.id)
    swap["pending"] = False
    swap["confirmed_driver_id"] = family.id
    save_swap(swap)

    # Log karma
    record_swap_cover(family.id, family.name)

    # Notify original driver
    trip_time = arrival_time()
    send_sms(
        to_phone=swap["original_driver_phone"],
        message=f"Great news! {family.name} will drive on {trip_time.strftime('%a %b %d')}. You're off the hook!",
    )

    # Notify everyone else who was asked
    for phone in swap["asked_phones"]:
        if phone != from_phone:
            send_sms(
                to_phone=phone,
                message=f"{family.name} has volunteered to drive on {trip_time.strftime('%a %b %d')}. No need to respond.",
            )

    return f"You're confirmed as driver on {trip_time.strftime('%a %b %d')}! You'll get the route details closer to the trip."


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/sms":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            params = parse_qs(body)

            from_number = params.get("From", [""])[0]
            raw_body = params.get("Body", [""])[0].strip()
            upper = raw_body.upper()

            print(f"Reply from {from_number}: {raw_body}")

            reply = None

            if upper == "YES":
                # Could be a swap volunteer or a confirmation
                reply = handle_yes_for_swap(from_number)
                if reply is None:
                    # It's a nightly confirmation
                    confirmations = load_confirmations()
                    confirmations[from_number] = raw_body
                    save_confirmations(confirmations)
                    reply = "Got it, confirmed!"

            elif upper.startswith("SWAP"):
                reason = raw_body[4:].strip() or "no reason given"
                reply = handle_swap(from_number, reason)

            else:
                # Treat as a new address confirmation
                confirmations = load_confirmations()
                confirmations[from_number] = raw_body
                save_confirmations(confirmations)
                reply = f"Got it! We'll pick you up at: {raw_body}"

            if reply:
                send_sms(to_phone=from_number, message=reply)

            twiml = b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.end_headers()
            self.wfile.write(twiml)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8080), WebhookHandler)
    print("Webhook server running on port 8080...")
    server.serve_forever()
