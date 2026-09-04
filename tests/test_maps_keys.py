"""The server Maps key must never reach a browser.

The Maps JavaScript API forces a key into the page, so dashboard.html and
bulletin.html expose whatever key they are handed to every parent in the
carpool. That is unavoidable and fine, provided the exposed key is
referrer-restricted and limited to Maps JavaScript. It is not fine if it is
the same key that can run billable geocoding and route calls against the card
on the Google Cloud account.

These tests hold that line: the browser gets the browser key, the server-side
calls get the server key, and the two never cross.
"""
import pytest

import maps_keys
from conftest import ADMIN, login

BROWSER = "AIzaBROWSERkeyREFERRERrestricted"
SERVER = "AIzaSERVERkeyGEOCODINGandROUTES"
LEGACY = "AIzaLEGACYsingleKeyDoingBothJobs"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (maps_keys.BROWSER_ENV, maps_keys.SERVER_ENV, maps_keys.LEGACY_ENV):
        monkeypatch.delenv(name, raising=False)
    yield


# ── resolution ──────────────────────────────────────────────────────────────

def test_each_key_is_used_for_its_own_job(monkeypatch):
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    monkeypatch.setenv(maps_keys.SERVER_ENV, SERVER)
    assert maps_keys.browser_key() == BROWSER
    assert maps_keys.server_key() == SERVER
    assert maps_keys.keys_are_shared() is False
    assert maps_keys.status()["ready"] is True


def test_the_legacy_single_key_still_works(monkeypatch):
    """An existing deployment must not break the moment this ships."""
    monkeypatch.setenv(maps_keys.LEGACY_ENV, LEGACY)
    assert maps_keys.browser_key() == LEGACY
    assert maps_keys.server_key() == LEGACY


def test_a_shared_key_is_reported_as_a_problem(monkeypatch):
    """The state that looks fine and is not: the key in the page is the key
    that can spend money."""
    monkeypatch.setenv(maps_keys.LEGACY_ENV, LEGACY)
    assert maps_keys.keys_are_shared() is True
    status = maps_keys.status()
    assert status["ready"] is False
    assert any("same value" in p for p in status["problems"]), status["problems"]


def test_a_new_key_overrides_the_legacy_one(monkeypatch):
    """Setting the browser key alone must stop the server key leaking into
    pages, even before the server key is set."""
    monkeypatch.setenv(maps_keys.LEGACY_ENV, LEGACY)
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    assert maps_keys.browser_key() == BROWSER
    assert maps_keys.server_key() == LEGACY
    assert maps_keys.keys_are_shared() is False


def test_whitespace_does_not_create_a_phantom_key(monkeypatch):
    """A trailing space in a dashboard variable already cost a month of
    signed-out parents. Do not let one masquerade as a configured key."""
    monkeypatch.setenv(maps_keys.BROWSER_ENV, "   ")
    monkeypatch.setenv(maps_keys.SERVER_ENV, SERVER)
    assert maps_keys.browser_key() == ""
    assert maps_keys.status()["browser_set"] is False


def test_status_never_contains_a_key(monkeypatch):
    """This goes on /health, which is public."""
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    monkeypatch.setenv(maps_keys.SERVER_ENV, SERVER)
    blob = repr(maps_keys.status())
    assert BROWSER not in blob
    assert SERVER not in blob
    # Not even a fragment.
    assert "AIza" not in blob


# ── the pages ───────────────────────────────────────────────────────────────

def test_the_dashboard_sends_the_browser_key_and_not_the_server_key(app, group, monkeypatch):
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    monkeypatch.setenv(maps_keys.SERVER_ENV, SERVER)
    c = login(app.test_client(), ADMIN)
    body = c.get("/").get_data(as_text=True)
    assert BROWSER in body, "the map cannot render without the browser key"
    assert SERVER not in body, "THE SERVER KEY REACHED A PAGE"


def test_the_group_bulletin_sends_the_browser_key_and_not_the_server_key(app, group, monkeypatch):
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    monkeypatch.setenv(maps_keys.SERVER_ENV, SERVER)
    from auth import get_user_by_email
    gid = get_user_by_email(ADMIN["email"])["group_id"]
    c = login(app.test_client(), ADMIN)
    body = c.get(f"/bulletin/{gid}").get_data(as_text=True)
    assert SERVER not in body, "THE SERVER KEY REACHED A PAGE"


def test_health_does_not_leak_either_key(client, monkeypatch):
    """/health needs no login, so anyone can read it."""
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    monkeypatch.setenv(maps_keys.SERVER_ENV, SERVER)
    body = client.get("/health").get_data(as_text=True)
    assert "Maps keys" in body, "the state should be visible"
    assert BROWSER not in body
    assert SERVER not in body
    assert "AIza" not in body


def test_health_warns_when_the_keys_are_shared(client, monkeypatch):
    monkeypatch.setenv(maps_keys.LEGACY_ENV, LEGACY)
    body = client.get("/health").get_data(as_text=True)
    assert "same value" in body, body[body.find("Maps keys"):][:300]
    assert LEGACY not in body


# ── the server-side callers ─────────────────────────────────────────────────

def test_geocoding_refuses_to_run_on_the_browser_key(monkeypatch):
    """A referrer-restricted key would fail at Google anyway; failing here
    says why instead of returning a confusing REQUEST_DENIED."""
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    import geocode
    with pytest.raises(RuntimeError, match="GOOGLE_MAPS_SERVER_KEY"):
        geocode.geocode("1163 E Laurelton Pkwy, Teaneck, NJ")


def test_routing_refuses_to_run_without_a_server_key(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    import routing
    with pytest.raises(RuntimeError, match="GOOGLE_MAPS_SERVER_KEY"):
        routing.compute_optimal_route(
            driver_lat=40.9, driver_lng=-74.0, pickups=[],
            dest_lat=40.7, dest_lng=-74.2,
            arrival_time=datetime(2026, 9, 10, 16, 30, tzinfo=timezone.utc))
