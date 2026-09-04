"""The signup page, which is also the page a carrier audits.

Toll-free verification is checked against this page: the reviewer opens
carpoolsync.com/create-group and looks for consent language beside a box that
is not already ticked. A redesign that pre-checks it, softens the wording or
drops the privacy link puts messaging at risk, so those are asserted here
rather than left to a reading of the template.
"""
import pytest

import portal

CONSENT = (
    "Text me about this carpool at the number above: who is driving, "
    "running-late and arrival alerts, and schedule changes. Message frequency "
    "varies. Message and data rates may apply. Reply STOP to stop, HELP for help."
)


@pytest.fixture()
def page(client):
    r = client.get("/create-group")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _render(**ctx):
    with portal.app.test_request_context("/create-group"):
        from flask import render_template
        return render_template("create_group.html", form={}, **ctx)


# ── what the carrier checks ─────────────────────────────────────────────────

def test_the_consent_sentence_is_word_for_word(page):
    assert CONSENT in page, "the wording Twilio verification was filed against"


def test_the_consent_box_is_required_and_starts_empty(page):
    """A pre-checked box is not consent, and the carrier rejects it."""
    assert 'name="sms_consent"' in page
    box = page[page.index('name="sms_consent"') - 200:page.index('name="sms_consent"') + 200]
    assert "required" in box
    assert "checked" not in box, "THE CONSENT BOX IS PRE-CHECKED"


def test_a_failed_submit_keeps_the_box_ticked():
    """Re-ticking after a validation error is not pre-checking: the parent did
    tick it, and making them do it again is how consent gets lost."""
    with portal.app.test_request_context("/create-group"):
        from flask import render_template
        body = render_template("create_group.html",
                               form={"sms_consent": "yes", "email": "dana@example.com"},
                               error="That email already has an account.")
    assert "checked" in body
    assert "dana@example.com" in body, "typed answers should survive the error"


def test_the_privacy_notice_is_linked(page):
    assert 'href="/privacy"' in page


# ── what the form still has to post ─────────────────────────────────────────

def test_every_field_the_route_reads_is_still_here(page):
    for name in ("csrf_token", "group_name", "name", "family_name", "email",
                 "phone", "password", "child_name", "address", "sms_consent"):
        assert f'name="{name}"' in page, f"{name} left the form"


def test_the_form_posts_to_create_group(page):
    assert 'action="/create-group"' in page
    assert 'method="POST"' in page


def test_the_autofill_hooks_survive(page):
    for hook in ('autocomplete="name"', 'autocomplete="email"',
                 'autocomplete="tel"', 'autocomplete="new-password"'):
        assert hook in page, f"{hook} left the form"
    assert 'minlength="8"' in page


def test_signing_up_still_works(client):
    """The whole point of the page. Straight through with a fresh email."""
    import re
    html = client.get("/create-group").get_data(as_text=True)
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    r = client.post("/create-group", data={
        "csrf_token": tok, "group_name": "Redesign Test", "name": "Test Parent",
        "family_name": "Redesign", "email": "redesign@example.com",
        "phone": "5550001111", "password": "Sunflower99",
        "address": "9 Elm St, Teaneck, NJ", "child_name": "Kid",
        "sms_consent": "yes"})
    assert r.status_code == 302, r.get_data(as_text=True)[:400]


def test_a_signup_without_consent_is_refused(client):
    """The box carries `required`, but a POST can skip the browser."""
    import re
    html = client.get("/create-group").get_data(as_text=True)
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    r = client.post("/create-group", data={
        "csrf_token": tok, "group_name": "No Consent", "name": "Test Parent",
        "family_name": "NoConsent", "email": "noconsent@example.com",
        "phone": "5550002222", "password": "Sunflower99",
        "address": "9 Elm St, Teaneck, NJ", "child_name": "Kid"})
    assert r.status_code != 302, "an account was created without SMS consent"


# ── the design ──────────────────────────────────────────────────────────────

def test_the_page_wears_the_shared_design(page):
    assert "storybook.css" in page
    assert "Changa+One" in page
    assert page.count("globe-house") >= 4, "the scene include did not render"
    for old in ("Styrene", "Tiempos"):
        assert old not in page, f"{old} belongs to the page this replaced"


def test_the_orange_gradient_button_is_gone(page):
    """A 135-degree gradient was the loudest thing on the old page."""
    assert "linear-gradient" not in page
    assert "#D4784A" not in page and "#C4633A" not in page


def test_the_page_carries_no_emoji(page):
    for glyph in ("\U0001f441", "\U0001f648", "\U0001f4e7", "\U0001f697", "✅"):
        assert glyph not in page, f"{glyph} is on the signup page"


def test_the_shared_stylesheet_is_served(client):
    r = client.get("/static/storybook.css")
    assert r.status_code == 200
    css = r.get_data(as_text=True)
    assert "#007fff" in css and "#ffe600" in css
    assert "3px solid var(--color-charcoal-ink)" in css, "the ink linework"
    assert "height: 44px" in css and "min-height: 44px" in css, "thumb targets"
    assert "border-radius: 0" in css, "no radii in this design"
