"""The kitchen-iPad bulletin: what it shows, and what it must never leak.

This page is public. It is identified only by an opaque display token, which
gets pasted into a family group chat and lives on an iPad on a counter, so two
things matter beyond layout: the page must not carry the server Maps key, and
it must degrade to something presentable when there is no key at all, no route
yet, or nobody driving.
"""
import pytest

import location
import maps_keys
from conftest import ADMIN, login

BROWSER = "AIzaBROWSERkeyREFERRERrestricted"
SERVER = "AIzaSERVERkeyGEOCODINGandROUTES"


@pytest.fixture(autouse=True)
def clean_keys(monkeypatch):
    for name in (maps_keys.BROWSER_ENV, maps_keys.SERVER_ENV, maps_keys.LEGACY_ENV):
        monkeypatch.delenv(name, raising=False)
    yield


def _group_id():
    from auth import get_user_by_email
    return get_user_by_email(ADMIN["email"])["group_id"]


@pytest.fixture()
def display_url(app, group):
    """The real URL an admin would paste onto the iPad."""
    from groups import get_or_create_display_token
    gid = _group_id()
    token = get_or_create_display_token(gid)
    yield f"/display/{token}", gid
    location.stop_ride(gid)


# ── the board itself ────────────────────────────────────────────────────────

def test_the_board_renders_for_anyone_with_the_url(client, display_url):
    url, _ = display_url
    r = client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Today" in body
    assert "pickups" in body.lower()


def test_an_unknown_token_is_not_a_way_in(client):
    r = client.get("/display/not-a-real-token")
    assert r.status_code == 404


def test_the_quiet_board_shows_the_globe_and_no_map(client, display_url):
    """Nobody driving: the blue globe holds the bottom of the board."""
    url, _ = display_url
    body = client.get(url).get_data(as_text=True)
    assert 'class="globe-band"' in body
    assert 'id="map"' not in body
    assert "maps.googleapis.com" not in body


def test_a_ride_puts_the_map_on_the_board(client, display_url, monkeypatch):
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    url, gid = display_url
    location.start_ride("Nadler", gid)
    location.update_location(40.9051, -74.0121, gid)

    body = client.get(url).get_data(as_text=True)
    assert 'id="map"' in body, "an active ride should show the live map"
    assert "40.9051" in body and "-74.0121" in body
    assert "is on the way" in body
    # The board marks itself, which is what hides the globe behind the map.
    assert 'class="has-map"' in body
    # And the globe stays in the page as the fallback for a refused key.
    assert 'class="globe-band"' in body
    assert "gm_authFailure" in body, "a refused key must fall back, not show error tiles"


def test_no_browser_key_means_the_globe_not_a_broken_map(client, display_url, monkeypatch):
    """Google billing may not be live yet. That must not show up as a grey
    box with an error watermark on a board in someone's kitchen."""
    url, gid = display_url
    location.start_ride("Nadler", gid)
    location.update_location(40.9051, -74.0121, gid)

    body = client.get(url).get_data(as_text=True)
    assert 'id="map"' not in body
    assert 'class="globe-band"' in body
    assert "is on the way" in body, "the live line does not depend on the map"


def test_a_ride_with_no_fix_yet_does_not_render_an_empty_map(client, display_url, monkeypatch):
    """start_ride() lands before the first position update."""
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    url, gid = display_url
    location.start_ride("Nadler", gid)

    body = client.get(url).get_data(as_text=True)
    assert 'id="map"' not in body
    assert 'class="globe-band"' in body


# ── what must not reach the page ────────────────────────────────────────────

def test_the_board_never_carries_the_server_key(client, display_url, monkeypatch):
    monkeypatch.setenv(maps_keys.BROWSER_ENV, BROWSER)
    monkeypatch.setenv(maps_keys.SERVER_ENV, SERVER)
    url, gid = display_url
    location.start_ride("Nadler", gid)
    location.update_location(40.9051, -74.0121, gid)

    body = client.get(url).get_data(as_text=True)
    assert BROWSER in body, "the map cannot render without the browser key"
    assert SERVER not in body, "THE SERVER KEY REACHED THE KID BULLETIN"


def test_the_board_carries_no_phone_numbers_or_addresses(client, display_url):
    """Kids read this, and so does anyone the display URL is forwarded to."""
    url, _ = display_url
    body = client.get(url).get_data(as_text=True)
    assert "5551234567" not in body, "the admin's phone number"
    assert "12 Maple St" not in body, "a family's home address"


def test_the_board_has_no_login_or_admin_links(client, display_url):
    url, _ = display_url
    body = client.get(url).get_data(as_text=True)
    for path in ('href="/login"', 'href="/admin', 'href="/profile"'):
        assert path not in body, f"{path} does not belong on a public display"


# ── the design's own rules ──────────────────────────────────────────────────

def test_the_board_is_set_in_the_storybook_type(client, display_url):
    url, _ = display_url
    body = client.get(url).get_data(as_text=True)
    assert "Changa+One" in body, "the display face the About page uses"
    assert "Fredoka" not in body, "the old bulletin's face"


def test_the_board_carries_no_emoji(client, display_url):
    """Emoji were doing the work of icons on the old board. Nothing on this one
    depends on a font the iPad may or may not have."""
    url, _ = display_url
    body = client.get(url).get_data(as_text=True)
    for glyph in ("🚗", "🎒", "📅", "🧒", "🙅", "📍", "🕒", "🏁", "🏠", "✓"):
        assert glyph not in body, f"{glyph} is still on the bulletin"
