"""When an ETA recompute is worth a billable Routes call, and when it is not.

The driver's phone posts its position through watchPosition, which fires every
few seconds while the car is moving. Recomputing the route matrix on every one
of those posts is what turns a twenty-minute drive into hundreds of billable
calls, each one priced per stop. Nothing about the answer improves at that
rate: a matrix computed four seconds ago is still correct.

So a recompute has to earn itself, on one of three grounds:

  nothing yet     no ETAs stored for this ride, so the first fix computes
  real movement   the car has covered enough ground to change the answer
  staleness       time has passed even though the car has not moved, which is
                  exactly what traffic looks like and does change the answer

and under all of them, a hard floor between calls, so no client behaviour and
no bug upstream can bill faster than that floor allows.

The numbers are deliberately conservative. A 20 minute drive lands around 15
calls instead of several hundred, and the map dot keeps updating at its own
rate because the position is stored on every post either way.
"""
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt

# No two computations for one group closer together than this, whatever else
# is true. This is the ceiling that makes the bill predictable.
MIN_GAP = timedelta(seconds=45)

# Far enough that the remaining route is materially different.
MOVED_METRES = 250

# Standing still counts as news after this long: traffic changes the ETA
# without moving the car.
STALE_AFTER = timedelta(seconds=150)


def metres_between(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance. Good to a few metres at carpool distances."""
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = p2 - p1
    dl = radians(lng2 - lng1)
    h = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(h))


def _parse(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def should_recompute(loc: dict, lat: float, lng: float, *, now: datetime = None) -> tuple[bool, str]:
    """Decide whether this position update earns a Routes call.

    loc is the stored location dict, which carries the last computation's time
    and origin. Returns (decision, reason) so the reason can be logged and
    tested rather than inferred.
    """
    now = now or datetime.now()
    last_at = _parse(loc.get("etas_at", ""))
    if not loc.get("etas") or last_at is None:
        return True, "first fix of this ride"

    since = now - last_at
    if since < MIN_GAP:
        return False, f"only {int(since.total_seconds())}s since the last call"

    from_lat, from_lng = loc.get("etas_from_lat"), loc.get("etas_from_lng")
    if from_lat is None or from_lng is None:
        return True, "no origin recorded for the last call"

    moved = metres_between(float(from_lat), float(from_lng), lat, lng)
    if moved >= MOVED_METRES:
        return True, f"moved {int(moved)}m"
    if since >= STALE_AFTER:
        return True, f"{int(since.total_seconds())}s old and only {int(moved)}m moved"
    return False, f"moved {int(moved)}m in {int(since.total_seconds())}s"


def stamp(loc: dict, lat: float, lng: float, *, now: datetime = None) -> None:
    """Record that a computation just happened from this position."""
    now = now or datetime.now()
    loc["etas_at"] = now.isoformat()
    loc["etas_from_lat"] = lat
    loc["etas_from_lng"] = lng
