"""A capped record of every message the app tried to send.

Until now a send was fire-and-forget: send_sms either raised or it didn't, and
"didn't raise" was treated as "the parent got it". That is not what it means.
Twilio accepting a message means it was queued. It can still fail at the
carrier minutes later, and on the WhatsApp sandbox it silently goes nowhere if
the recipient's 72-hour window has lapsed. Neither shows up as an exception.

So every attempt gets a row here, keyed by the Twilio message SID, and the
status-callback webhook updates that row as the carrier reports back. The
result is that "did the 4:30 reminder reach the Cohens" has an answer.

Storage rides the existing seam in storage.py: message_log.json is listed in
_GLOBAL_PG_FILES, so with USE_SUPABASE_DB=1 it lives in the group_files table
under the reserved "_global" group and needs no new schema. Without it, it is
a JSON file on the volume.

The log is capped. It is an operational record for the last few days of
sending, not an archive.
"""

import logging
from datetime import datetime, timezone

from storage import DATA_DIR, update_json, read_json

logger = logging.getLogger(__name__)

LOG_FILE = DATA_DIR / "message_log.json"

# Roughly a fortnight of sending for a single carpool, and small enough that
# the whole file stays cheap to read and rewrite on every send.
MAX_ENTRIES = 600

# Twilio's terminal states, plus the two the app records itself. Anything not
# listed here is still in flight.
DELIVERED = ("delivered", "read")
# "opted_out" belongs with the failures: the message did not arrive, and the
# admin has to reach that family another way. Counting it as in-flight would
# quietly imply it might still land, which it never will.
FAILED = ("undelivered", "failed", "send_error", "opted_out")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record_attempt(*, sid: str, to_phone: str, kind: str, group_id: str,
                   channel: str, status: str, error: str = "") -> dict:
    """Store one outbound attempt. `sid` is empty when the send raised before
    Twilio issued one, which is itself worth recording."""
    entry = {
        "sid": sid or "",
        "to": to_phone or "",
        "kind": kind or "",
        "group_id": group_id or "",
        "channel": channel or "",
        "status": status or "unknown",
        "error": error or "",
        "created_at": _now(),
        "updated_at": _now(),
    }

    def _mutate(log):
        if not isinstance(log, list):
            log = []
        log.append(entry)
        # Trim the oldest. The list is append-ordered, so slicing the tail
        # keeps the newest MAX_ENTRIES.
        if len(log) > MAX_ENTRIES:
            del log[:len(log) - MAX_ENTRIES]
        return log

    try:
        update_json(LOG_FILE, _mutate, default=[])
    except Exception as e:
        # A message that sent but failed to log is better than a send that
        # raised because logging failed.
        logger.error("Could not record message attempt: %s", e)
    return entry


def update_status(sid: str, status: str, error: str = "") -> bool:
    """Apply a status callback to the matching row. Returns False when the SID
    is unknown, which happens for messages sent before this log existed, or
    from another environment pointed at the same Twilio account."""
    if not sid:
        return False
    found = {"hit": False}

    def _mutate(log):
        if not isinstance(log, list):
            return []
        for entry in reversed(log):
            if entry.get("sid") == sid:
                entry["status"] = status or entry.get("status", "")
                if error:
                    entry["error"] = error
                entry["updated_at"] = _now()
                found["hit"] = True
                break
        return log

    try:
        update_json(LOG_FILE, _mutate, default=[])
    except Exception as e:
        logger.error("Could not update message status for %s: %s", sid, e)
        return False
    return found["hit"]


def recent(limit: int = 100, group_id: str = "") -> list:
    """Newest first. Scoped to one group when group_id is given."""
    try:
        log = read_json(LOG_FILE, default=[])
    except Exception as e:
        logger.error("Could not read message log: %s", e)
        return []
    if not isinstance(log, list):
        return []
    if group_id:
        log = [e for e in log if e.get("group_id") == group_id]
    return list(reversed(log))[:max(0, int(limit))]


def summary(group_id: str = "") -> dict:
    """Counts by outcome, for the admin page and for answering "is messaging
    actually working" without reading every row."""
    rows = recent(limit=MAX_ENTRIES, group_id=group_id)
    out = {"total": len(rows), "delivered": 0, "failed": 0, "in_flight": 0}
    for r in rows:
        status = (r.get("status") or "").lower()
        if status in DELIVERED:
            out["delivered"] += 1
        elif status in FAILED:
            out["failed"] += 1
        else:
            out["in_flight"] += 1
    return out
