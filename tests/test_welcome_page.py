"""The page a parent lands on the moment their carpool exists.

It is shown once, from a session flag, and it is the only place the four
setup steps are spelled out, so the copy matters more than it looks like it
does: a parent who skips this page has a group with no destination, no
schedule and no other families in it.
"""
import pytest

import portal


def _render(**ctx):
    ctx.setdefault("group_name", "Teaneck Sunday Carpool")
    ctx.setdefault("user_email", "dana@example.com")
    with portal.app.test_request_context("/welcome"):
        from flask import render_template
        return render_template("welcome.html", **ctx)


@pytest.fixture()
def page():
    return _render()


# ── the one-shot route ──────────────────────────────────────────────────────

def test_the_page_is_not_reachable_without_having_just_signed_up(client, group):
    """No session flag, no celebration: it redirects to the dashboard rather
    than showing a second parent someone else's welcome."""
    from conftest import ADMIN, login
    login(client, ADMIN)
    r = client.get("/welcome")
    assert r.status_code == 302
    assert r.headers["Location"] in ("/", "http://localhost/")


# ── what the page has to say ────────────────────────────────────────────────

def test_the_group_gets_named(page):
    assert "Teaneck Sunday Carpool" in page


def test_all_four_steps_are_here(page):
    for step in ("Invite the other families", "destination", "recurring schedule",
                 "kid bulletin"):
        assert step in page, f"the {step} step is missing"


def test_the_way_on_is_the_dashboard(page):
    assert 'href="/"' in page
    assert "Go to the dashboard" in page


def test_the_locked_out_note_carries_the_signin_email(page):
    assert 'href="/forgot-password"' in page
    assert "dana@example.com" in page


def test_the_password_warning_only_shows_when_it_should(page):
    assert "Reset your password" not in page
    warned = _render(persistence_warning=True)
    assert "Reset your password" in warned
    assert "Manage Users" in warned


def test_the_whatsapp_join_instructions_follow_the_channel():
    """On the sandbox a family has to message the keyword first or nothing
    reaches them. On SMS that instruction would be nonsense."""
    sms = _render(channel="text message", on_whatsapp=False)
    assert "text message" in sms
    assert 'class="aside"' not in sms, "the keyword instruction has no meaning on SMS"
    wa = _render(channel="WhatsApp", on_whatsapp=True,
                 sandbox_keyword="carpool-sync", sandbox_number="+14155238886")
    assert "carpool-sync" in wa and "+14155238886" in wa


# ── the design ──────────────────────────────────────────────────────────────

def test_the_page_wears_the_shared_design(page):
    assert "storybook.css" in page
    assert "Changa+One" in page
    assert page.count("globe-house") >= 4, "the scene include did not render"
    for old in ("Styrene", "Tiempos"):
        assert old not in page


def test_the_peach_gradient_and_confetti_are_gone(page):
    assert "linear-gradient" not in page
    assert "confetti" not in page
    assert "#D4784A" not in page


def test_the_page_carries_no_emoji(page):
    for glyph in ("\U0001f389", "⚠", "\U0001f4a1", "→", "\U0001f697"):
        assert glyph not in page, f"{glyph} is still on the welcome page"
