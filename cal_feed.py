"""
Per-group ICS calendar feed.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from icalendar import Calendar, Event, vText
import uuid

from schedule import load_schedule
from config import get_group_name


def build_ics(group_id: str) -> bytes:
    name = get_group_name(group_id)
    cal = Calendar()
    cal.add("prodid", f"-//{name}//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", "America/New_York")

    tz = ZoneInfo("America/New_York")

    for trip in load_schedule(group_id):
        arrival = datetime.strptime(
            f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=tz)
        start = arrival - timedelta(hours=1)

        event = Event()
        event.add("uid", f"{trip['id']}@carpoolsync")
        event.add("summary", f"Carpool — {trip['destination_name']}")
        event.add("dtstart", start)
        event.add("dtend", arrival)
        event.add("location", vText(trip["destination_address"]))
        event.add("description",
            f"Driver: {trip['driver_name']}\nDestination: {trip['destination_name']}")
        event.add("dtstamp", datetime.now(tz=ZoneInfo("UTC")))
        cal.add_component(event)

    return cal.to_ical()
