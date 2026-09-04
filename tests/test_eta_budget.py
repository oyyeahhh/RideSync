"""The rule that keeps a drive from becoming a Google bill.

Every position the driver's phone reports used to trigger a Routes matrix
call, priced per stop. watchPosition fires every few seconds in a moving car,
so a single twenty-minute trip could bill hundreds of times for an answer that
barely changed. These tests pin the throttle, including the ceiling that holds
no matter how a client behaves.
"""
from datetime import datetime, timedelta

import pytest

import location
import portal
from conftest import ADMIN, csrf_header, login
from eta_budget import MIN_GAP, MOVED_METRES, metres_between, should_recompute, stamp

# Two points about 300m apart in Maplewood, and one about 40m away.
HOME = (40.9051, -74.0121)
FAR = (40.9078, -74.0121)
NEAR = (40.90546, -74.0121)
T0 = datetime(2026, 9, 8, 16, 5, 0)


def _loc(*, at=T0, frm=HOME, etas=None):
    return {"active": True, "etas": etas if etas is not None else [{"name": "Cohen", "minutes": 4}],
            "etas_at": at.isoformat(), "etas_from_lat": frm[0], "etas_from_lng": frm[1]}


# ── the distance helper ─────────────────────────────────────────────────────

def test_the_distance_helper_is_in_metres():
    assert 250 < metres_between(*HOME, *FAR) < 350
    assert metres_between(*HOME, *NEAR) < 60
    assert metres_between(*HOME, *HOME) == 0


# ── when a call is earned ───────────────────────────────────────────────────

def test_the_first_fix_of_a_ride_always_computes():
    yes, why = should_recompute({"active": True}, *HOME)
    assert yes and "first fix" in why


def test_stored_etas_with_no_timestamp_recompute():
    """A ride that started before this rule existed, or a half-written file."""
    yes, _ = should_recompute({"active": True, "etas": [{"minutes": 3}]}, *HOME)
    assert yes


def test_real_movement_earns_a_call():
    yes, why = should_recompute(_loc(), *FAR, now=T0 + timedelta(seconds=60))
    assert yes and "moved" in why


def test_sitting_in_traffic_earns_a_call_eventually():
    """The car has not moved, but the road ahead has changed."""
    no, _ = should_recompute(_loc(), *NEAR, now=T0 + timedelta(seconds=60))
    assert not no
    yes, why = should_recompute(_loc(), *NEAR, now=T0 + timedelta(seconds=160))
    assert yes and "old" in why


# ── when it is not ──────────────────────────────────────────────────────────

def test_a_fix_seconds_later_does_not_compute():
    no, why = should_recompute(_loc(), *FAR, now=T0 + timedelta(seconds=5))
    assert not no
    assert "since the last call" in why


def test_the_floor_holds_even_for_a_long_jump():
    """Movement does not buy a call inside the minimum gap. This is the line
    that makes the worst case predictable."""
    far_away = (41.5, -74.5)
    no, _ = should_recompute(_loc(), *far_away, now=T0 + MIN_GAP - timedelta(seconds=1))
    assert not no


def test_a_crawling_car_does_not_compute():
    no, why = should_recompute(_loc(), *NEAR, now=T0 + timedelta(seconds=50))
    assert not no and "moved" in why


def test_a_twenty_minute_drive_stays_in_the_tens_of_calls():
    """Simulated: a fix every 4 seconds for 20 minutes, moving steadily."""
    loc = {"active": True}
    now = T0
    lat, lng = HOME
    calls = 0
    for _ in range(300):                  # 300 fixes, one every 4 seconds
        yes, _why = should_recompute(loc, lat, lng, now=now)
        if yes:
            loc["etas"] = [{"minutes": 3}]
            stamp(loc, lat, lng, now=now)
            calls += 1
        now += timedelta(seconds=4)
        lat += 0.00025                    # roughly 28m per fix, so ~25 km/h
    assert calls <= 30, f"{calls} billable calls for one drive"
    assert calls >= 10, f"only {calls} calls, the ETA would be stale"


# ── the route that uses it ──────────────────────────────────────────────────

@pytest.fixture()
def driving(client, group, monkeypatch):
    """An admin sharing location, with the Routes call counted, not made."""
    login(client, ADMIN)
    from auth import get_user_by_email
    gid = get_user_by_email(ADMIN["email"])["group_id"]
    location.start_ride("Nadler", gid)
    calls = []

    def fake(driver_lat, driver_lng, trip_date, group_id="grp_main", driver_family_id=""):
        calls.append((driver_lat, driver_lng))
        return [{"family_id": "f1", "name": "Cohen", "minutes": 4}]

    monkeypatch.setattr("eta.compute_etas", fake)
    yield client, calls, gid
    location.stop_ride(gid)


def _post(client, lat, lng):
    return client.post("/update-location", json={"lat": lat, "lng": lng},
                       headers=csrf_header(client))


def test_a_burst_of_fixes_makes_one_routes_call(driving):
    client, calls, _gid = driving
    for _ in range(25):
        r = _post(client, *HOME)
        assert r.status_code == 200
    assert len(calls) == 1, f"{len(calls)} Routes calls for one burst"


def test_the_position_is_still_stored_on_every_post(driving):
    """The map dot must stay live even when the ETA is not recomputed."""
    client, _calls, gid = driving
    _post(client, *HOME)
    _post(client, 40.9060, -74.0130)
    loc = location.get_location(gid)
    assert loc["lat"] == 40.9060 and loc["lng"] == -74.0130


def test_the_stored_etas_survive_a_skipped_recompute(driving):
    client, _calls, gid = driving
    assert _post(client, *HOME).status_code == 200
    first = location.get_location(gid).get("etas")
    assert first, "the first fix should have computed"
    _post(client, *NEAR)
    assert location.get_location(gid)["etas"] == first


def test_the_response_says_whether_it_recomputed(driving):
    client, _calls, _gid = driving
    assert _post(client, *HOME).get_json()["etas_recomputed"] is True
    assert _post(client, *HOME).get_json()["etas_recomputed"] is False


def test_a_failing_routes_call_keeps_the_last_good_etas(driving, monkeypatch):
    """Google being down should not blank the ETAs on the bulletin."""
    client, _calls, gid = driving
    assert _post(client, *HOME).status_code == 200
    good = location.get_location(gid).get("etas")
    assert good

    def boom(*a, **k):
        raise RuntimeError("Routes API 503")

    monkeypatch.setattr("eta.compute_etas", boom)
    loc = location.get_location(gid)
    loc["etas_at"] = (datetime.now() - timedelta(minutes=5)).isoformat()
    location._save(loc, gid)
    r = _post(client, *FAR)
    assert r.status_code == 200
    assert location.get_location(gid)["etas"] == good


def test_only_the_driver_or_an_admin_can_spend_a_routes_call(client, group):
    """The cheapest throttle of all: an anonymous POST cannot bill anything."""
    r = client.post("/update-location", json={"lat": 40.9, "lng": -74.0})
    assert r.status_code in (302, 401, 403)


def test_the_numbers_are_where_the_comment_says():
    assert MIN_GAP.total_seconds() == 45
    assert MOVED_METRES == 250
