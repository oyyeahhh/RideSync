"""
Generates an ICS calendar feed from the trip schedule.
Served at /calendar.ics by portal.py.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from icalendar import Calendar, Event, vText
import uuid

from schedule import load_schedule


def build_ics() -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Teaneck Carpool//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Teaneck Carpool")
    cal.add("x-wr-timezone", "America/New_York")

    tz = ZoneInfo("America/New_York")

    for trip in load_schedule():
        arrival = datetime.strptime(
            f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=tz)
        start = arrival - timedelta(hours=1)

        event = Event()
        event.add("uid", f"{trip['id']}@teaneck-carpool")
        event.add("summary", f"Carpool — {trip['destination_name']}")
        event.add("dtstart", start)
        event.add("dtend", arrival)
        event.add("location", vText(trip["destination_address"]))
        event.add(
            "description",
            f"Driver: {trip['driver_name']}\nDestination: {trip['destination_name']}",
        )
        event.add("dtstamp", datetime.now(tz=ZoneInfo("UTC")))
        cal.add_component(event)

    return cal.to_ical()
