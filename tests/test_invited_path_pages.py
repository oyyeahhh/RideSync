"""The three pages an invited parent and a locked-out parent land on.

All three were rebuilt in the shared design. Two of them are the only way
back into an account, and the third collects SMS consent from every family
who is not the group's admin, so the guards here are about what has to keep
working, not about how it looks.
"""
import re

import pytest

import portal
from conftest import ADMIN, _csrf

CONSENT = (
    "Text me about this carpool at the number above: who is driving, "
    "running-late and arrival alerts, and schedule changes. Message frequency "
    "varies. Message and data rates may apply. Reply STOP to stop, HELP for help."
)


def _render(template, **ctx):
    with portal.app.test_request_context("/"):
        from flask import render_template
        return render_template(template, **ctx)


# ── the invited parent's signup ─────────────────────────────────────────────

def _invite_page(client, group):
    """Walk the real invite flow so the token is a live one."""
    from conftest import login
    login(client, ADMIN)
    dash = client.get("/").get_data(as_text=True)
    tok = re.search(r'id="inviteForm".*?name="csrf_token" value="([^"]+)"', dash, re.S).group(1)
    client.post("/invite", data={"csrf_token": tok, "phone": "5557778888", "family_id": ""})
    dash = client.get("/").get_data(as_text=True)
    link = re.search(r'/signup\?token=([A-Za-z0-9_\-\.]+)', dash)
    assert link, "the invite link should be on the dashboard"
    client.get("/logout")
    r = client.get(f"/signup?token={link.group(1)}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_the_invite_page_shows_the_number_it_was_sent_to(client, group):
    page = _invite_page(client, group)
    assert "5557778888" in page or "555-777-8888" in page or "5557778888" in page.replace(" ", "")


def test_the_invite_page_carries_the_token_and_the_consent_box(client, group):
    page = _invite_page(client, group)
    assert 'name="token"' in page, "without the token the signup cannot be attached"
    assert CONSENT in page, "invited families give consent on this page"
    box = page[page.index('name="sms_consent"') - 200:page.index('name="sms_consent"') + 200]
    assert "required" in box and "checked" not in box


def test_the_invite_page_keeps_its_fields(client, group):
    page = _invite_page(client, group)
    for name in ("csrf_token", "token", "name", "family_name", "email",
                 "password", "child_name", "address", "sms_consent"):
        assert f'name="{name}"' in page, f"{name} left the form"
    assert 'action="/signup"' in page


def test_the_whatsapp_optin_steps_only_appear_on_whatsapp():
    sms = _render("signup.html", form={}, phone="5557778888", token="t",
                  on_whatsapp=False, suggested_family_name="")
    assert 'class="plate optin"' not in sms, "no keyword instructions when we send SMS"
    wa = _render("signup.html", form={}, phone="5557778888", token="t",
                 on_whatsapp=True, sandbox_number="+14155238886",
                 sandbox_keyword="carpool-sync", suggested_family_name="")
    assert "+14155238886" in wa and "join carpool-sync" in wa


# ── forgot password ─────────────────────────────────────────────────────────

def test_the_forgot_form_asks_for_an_email(client):
    page = client.get("/forgot-password").get_data(as_text=True)
    assert 'action="/forgot-password"' in page
    assert 'autocomplete="email"' in page
    assert 'name="csrf_token"' in page
    assert 'href="/login"' in page


def test_the_sent_state_says_where_to_look():
    email = _render("forgot_password.html", sent=True, via="email")
    assert "inbox" in email and "one hour" in email
    phone = _render("forgot_password.html", sent=True, via="whatsapp", channel="WhatsApp")
    assert "phone number on file" in phone
    # Neither state confirms whether the account exists.
    for body in (email, phone):
        assert "If an account exists" in body


def test_the_sent_state_hides_the_form():
    body = _render("forgot_password.html", sent=True, via="email")
    assert 'name="email"' not in body


# ── reset password ──────────────────────────────────────────────────────────

def test_the_reset_form_posts_where_it_was_told():
    body = _render("reset_password.html", action="/reset-password/abc123", error=None)
    assert 'action="/reset-password/abc123"' in body
    assert 'name="password"' in body and 'name="confirm_password"' in body
    assert body.count('minlength="8"') == 2
    assert body.count('autocomplete="new-password"') == 2


def test_both_reset_fields_can_be_revealed_separately():
    """One shared toggle would show the other field too."""
    body = _render("reset_password.html", action="/x", error=None)
    assert 'id="eye-new"' in body and 'id="eye-confirm"' in body
    assert "togglePw('new-password', 'eye-new')" in body
    assert "togglePw('confirm-password', 'eye-confirm')" in body


def test_the_reset_error_shows_in_the_coral_rule():
    body = _render("reset_password.html", action="/x", error="The two passwords do not match.")
    assert "The two passwords do not match." in body
    assert 'class="notice bad"' in body


# ── all three wear the design ───────────────────────────────────────────────

@pytest.mark.parametrize("template,ctx", [
    ("signup.html", {"form": {}, "phone": "5551112222", "token": "t",
                     "suggested_family_name": ""}),
    ("forgot_password.html", {"sent": False, "via": "email"}),
    ("reset_password.html", {"action": "/x", "error": None}),
])
def test_the_page_wears_the_shared_design(template, ctx):
    body = _render(template, **ctx)
    assert "storybook.css" in body
    assert "Changa+One" in body
    assert body.count("globe-house") >= 4, "the scene include did not render"
    for old in ("Styrene", "Tiempos", "#D4784A", "border-radius: 7px"):
        assert old not in body, f"{old} belongs to the page this replaced"
    for glyph in ("\U0001f4ac", "←", "→"):
        assert glyph not in body, f"{glyph} is still on {template}"
