"""
Per-group ICS calendar feed.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from icalendar import Calendar, Event, vText

from schedule import load_schedule
from config import get_group_name, load_config


def build_ics(group_id: str) -> bytes:
    name = get_group_name(group_id)
    cfg = load_config(group_id)
    tz_name = cfg.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)
    buffer_min = int(cfg.get("buffer_minutes", 60))

    cal = Calendar()
    cal.add("prodid", f"-//{name}//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", tz_name)

    for trip in load_schedule(group_id):
        try:
            arrival = datetime.strptime(
                f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=tz)
        except (KeyError, ValueError):
            continue
        # Use the group's pickup buffer instead of a hardcoded 60 minutes.
        start = arrival - timedelta(minutes=max(buffer_min, 15))

        event = Event()
        event.add("uid", f"{trip['id']}@carpoolsync")
        event.add("summary", f"Carpool — {trip.get('destination_name', '')}".rstrip(" —"))
        event.add("dtstart", start)
        event.add("dtend", arrival)
        if trip.get("destination_address"):
            event.add("location", vText(trip["destination_address"]))
        event.add("description",
            f"Driver: {trip.get('driver_name', 'TBD')}\n"
            f"Destination: {trip.get('destination_name', '')}")
        event.add("dtstamp", datetime.now(tz=ZoneInfo("UTC")))
        cal.add_component(event)

    return cal.to_ical()
