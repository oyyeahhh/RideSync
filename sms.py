"""
Outbound messaging via Twilio, over the WhatsApp sandbox or plain SMS.
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

# Twilio WhatsApp sandbox opt-in details. Only read while MESSAGE_CHANNEL is
# whatsapp; once the toll-free number is verified and the channel is sms these
# are unused and can be deleted from Railway.
# TWILIO_SANDBOX_NUMBER  – the sandbox phone number, e.g. +14155238886
# TWILIO_SANDBOX_KEYWORD – the join keyword from the Twilio console
SANDBOX_NUMBER = os.environ.get("TWILIO_SANDBOX_NUMBER", "+14155238886")
SANDBOX_KEYWORD = os.environ.get("TWILIO_SANDBOX_KEYWORD", "")

# MESSAGE_CHANNEL decides how the number is addressed. "whatsapp" is the
# Twilio sandbox (recipients must have texted the join keyword within 72h);
# "sms" is plain text from FROM_NUMBER once carrier registration is approved.
# Read at call time so a Railway variable change needs no code change.
#
# An unset or unrecognised value means whatsapp, deliberately. Defaulting the
# other way would start sending bare numbers through a sandbox that silently
# drops them.
_VALID_CHANNELS = ("sms", "whatsapp")

# Twilio's code for "this recipient replied STOP". Carriers enforce the
# opt-out, so there is nothing to retry and no reason to log it as an error;
# the family simply cannot be reached by message until they text START.
OPTED_OUT_CODE = 21610


class RecipientOptedOut(Exception):
    """Raised when a recipient has replied STOP. Callers should stop trying to
    reach that number and tell the admin, rather than treating it as a
    transient failure worth retrying."""

    def __init__(self, phone: str):
        super().__init__(f"{phone} has opted out of messages (replied STOP)")
        self.phone = phone


def message_channel() -> str:
    ch = os.environ.get("MESSAGE_CHANNEL", "whatsapp").strip().lower()
    return ch if ch in _VALID_CHANNELS else "whatsapp"


def uses_whatsapp() -> bool:
    return message_channel() == "whatsapp"


def _address(number: str) -> str:
    """Strip any existing prefix, then apply the one the channel needs."""
    bare = number.split(":", 1)[1] if number.startswith("whatsapp:") else number
    return f"whatsapp:{bare}" if uses_whatsapp() else bare


def app_base_url() -> str:
    """Public origin, for links and webhooks built outside a request context."""
    return os.environ.get("APP_BASE_URL", "https://carpoolsync.com").rstrip("/")


def _status_callback_url() -> str:
    """Where Twilio should report delivery. Empty disables callbacks, which is
    what we want in tests and local runs — Twilio cannot reach localhost, and
    passing an unreachable URL makes it retry and log errors for every send."""
    if os.environ.get("FLASK_TESTING") == "1":
        return ""
    base = app_base_url()
    if not base.startswith("https://") or "localhost" in base or "127.0.0.1" in base:
        return ""
    return f"{base}/twilio/status"


def config_status() -> dict:
    """Is messaging actually configured for the channel it claims to use?

    Surfaced on /health and /admin/system so a misconfiguration is visible
    before a parent fails to get a reminder, rather than after.
    """
    channel = message_channel()
    problems = []
    if not ACCOUNT_SID or not AUTH_TOKEN:
        problems.append("TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN is not set")
    if not FROM_NUMBER:
        problems.append("TWILIO_FROM_NUMBER is not set")
    elif channel == "sms" and FROM_NUMBER.startswith("whatsapp:"):
        problems.append("TWILIO_FROM_NUMBER still carries a whatsapp: prefix")
    if channel == "whatsapp" and not SANDBOX_KEYWORD:
        problems.append("TWILIO_SANDBOX_KEYWORD is not set, so signup cannot "
                        "tell a parent which keyword to send")
    if not _status_callback_url():
        problems.append("no delivery-status callback (APP_BASE_URL is not a "
                        "public https origin), so failed sends stay invisible")
    return {
        "channel": channel,
        "ready": not problems,
        "problems": problems,
    }


def send_sms(to_phone: str, message: str, *, kind: str = "",
             group_id: str = "") -> str:
    """Send one message and return the Twilio SID.

    `kind` and `group_id` are recorded with the attempt so the admin can tell a
    driver reminder from an invite when reading the log.

    Raises RecipientOptedOut when the number has replied STOP, and re-raises
    anything else. Every outcome is recorded either way — a send that fails
    unrecorded is the thing this is here to prevent.
    """
    from message_log import record_attempt

    channel = message_channel()

    if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER]):
        record_attempt(sid="", to_phone=to_phone, kind=kind, group_id=group_id,
                       channel=channel, status="send_error",
                       error="Twilio credentials not set")
        raise RuntimeError("Twilio credentials not set in .env")

    # Without an explicit timeout the Twilio SDK waits forever, and the app has
    # only two request workers.
    from twilio.http.http_client import TwilioHttpClient
    client = Client(ACCOUNT_SID, AUTH_TOKEN,
                    http_client=TwilioHttpClient(timeout=10))

    kwargs = {
        "body": message,
        "from_": _address(FROM_NUMBER),
        "to": _address(to_phone),
    }
    callback = _status_callback_url()
    if callback:
        kwargs["status_callback"] = callback

    try:
        msg = client.messages.create(**kwargs)
    except Exception as e:
        code = getattr(e, "code", None)
        if code == OPTED_OUT_CODE:
            record_attempt(sid="", to_phone=to_phone, kind=kind,
                           group_id=group_id, channel=channel,
                           status="opted_out", error=f"Twilio {code}")
            logger.warning("%s opted out of messages; not retrying",
                           to_phone[:6] + "***")
            raise RecipientOptedOut(to_phone) from e
        record_attempt(sid="", to_phone=to_phone, kind=kind, group_id=group_id,
                       channel=channel, status="send_error",
                       error=f"{type(e).__name__}: {e}"[:300])
        raise

    sid = getattr(msg, "sid", "") or ""
    record_attempt(sid=sid, to_phone=to_phone, kind=kind, group_id=group_id,
                   channel=channel, status=getattr(msg, "status", "queued") or "queued")
    logger.info("%s message %s queued to %s***", channel, sid or "(no sid)",
                to_phone[:6])
    return sid


def send_route_sms(to_phone: str, result: dict, driver_name: str, dest_name: str,
                   maps_url: str, drive_url: str = "", group_id: str = "") -> str:
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

    return send_sms(to_phone=to_phone, message="\n".join(lines),
                    kind="driver_route", group_id=group_id)
