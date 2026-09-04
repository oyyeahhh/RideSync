"""The sign-in page: the parts a browser depends on, and the design's rules.

The page was rebuilt in the About page's world (yellow sky, blue globe, canal
houses, Changa One). A redesign is the easiest place to break sign-in without
noticing, because the things that make it work are invisible: the autocomplete
attributes password managers key off, the hidden email the magic-link form
posts, and the CSRF token. Those are what most of this file guards.
"""
import pytest

import portal


@pytest.fixture()
def page(client):
    r = client.get("/login")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _render(**ctx):
    """Render the template in the states only a POST would produce."""
    with portal.app.test_request_context("/login"):
        from flask import render_template
        return render_template("login.html", **ctx)


# ── what a browser and a password manager need ──────────────────────────────

def test_the_password_form_posts_to_login_with_a_csrf_token(page):
    assert 'action="/login"' in page
    assert 'name="csrf_token"' in page


def test_the_fields_keep_the_autocomplete_hooks(page):
    """Rename these and every saved password stops offering itself."""
    assert 'id="login-email"' in page
    assert 'autocomplete="username"' in page
    assert 'id="pw-input"' in page
    assert 'autocomplete="current-password"' in page


def test_the_password_can_be_shown(page):
    assert "togglePw()" in page
    assert 'id="pw-toggle"' in page


def test_keep_me_signed_in_still_posts_remember(page):
    assert 'name="remember"' in page


def test_the_magic_link_form_mirrors_the_one_email_field(page):
    """One email input on the page, so autofill has nothing to guess at. The
    magic-link form carries a hidden copy that JS keeps in sync."""
    assert 'action="/auth/magic-link"' in page
    assert 'id="magic-email-mirror"' in page
    assert "addEventListener('input'" in page
    assert page.count('type="email"') == 1


def test_the_ways_out_are_all_there(page):
    for href in ('href="/forgot-password"', 'href="/create-group"', 'href="/"'):
        assert href in page, f"{href} left the page"


def test_a_typed_email_survives_a_failed_attempt():
    body = _render(email="dana@example.com", error="Wrong password.")
    assert 'value="dana@example.com"' in body
    assert body.count("dana@example.com") >= 2, "the mirror should carry it too"
    assert "Wrong password." in body


def test_the_three_notices_render():
    assert "Password updated" in _render(success="Password updated! Please sign in.")
    assert "Check your email" in _render(email="dana@example.com", magic_sent=True)
    assert "Wrong password." in _render(error="Wrong password.")


# ── the design's own rules ──────────────────────────────────────────────────

def test_the_page_is_set_in_the_storybook_type(page):
    assert "Changa+One" in page, "the display face the About page uses"
    for old in ("Styrene", "Tiempos"):
        assert old not in page, f"{old} belongs to the page this replaced"


def test_the_page_carries_no_emoji(page):
    """The eye and the envelope were emoji. Nothing here depends on a font the
    phone may or may not have."""
    for glyph in ("👁", "🙈", "📧", "✉", "🔒", "🚗"):
        assert glyph not in page, f"{glyph} is still on the sign-in page"


def test_the_scene_is_the_about_page_scene(page):
    """Yellow sky, blue globe, houses on the curve, the mascot on the apex."""
    assert "#ffe600" in page and "#007fff" in page
    assert page.count("globe-house") >= 4
    assert "/static/tesla.png" in page


def test_the_form_sits_on_the_blue_not_in_a_card(page):
    """The old page was a white rounded card with a soft shadow. The house
    style has flat plates, 3px ink linework and no radii."""
    assert "box-shadow: 0 2px 16px" not in page
    assert "border-radius: 14px" not in page
    assert "3px solid var(--color-charcoal-ink)" in page


def test_every_tap_target_clears_44px(page):
    """Thumbs on a phone. The toggle, the checkbox row and both buttons."""
    assert "width: 44px;" in page and "height: 44px;" in page   # the eye
    assert "min-height: 44px;" in page                          # remember row
    assert "height: 56px;" in page                              # sign in
