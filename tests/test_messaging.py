"""Delivery tracking: does the app know whether a message actually arrived?

The bug these guard against is the one that hid a month of undelivered
messages: Twilio accepting a send was treated as the parent receiving it.
"""
import pytest
from twilio.request_validator import RequestValidator

from conftest import ADMIN, login

import message_log
import portal
import sms


@pytest.fixture(autouse=True)
def clean_log():
    """Each test starts with an empty log."""
    from storage import atomic_write_json
    atomic_write_json(message_log.LOG_FILE, [])
    yield
    atomic_write_json(message_log.LOG_FILE, [])


# ── the log itself ──────────────────────────────────────────────────────────

def test_attempt_is_recorded_and_status_applied():
    message_log.record_attempt(sid="SM123", to_phone="+15551230000",
                               kind="driver_route", group_id="grp_x",
                               channel="sms", status="queued")
    rows = message_log.recent()
    assert len(rows) == 1
    assert rows[0]["sid"] == "SM123"
    assert rows[0]["status"] == "queued"

    assert message_log.update_status("SM123", "delivered") is True
    assert message_log.recent()[0]["status"] == "delivered"
    assert message_log.summary()["delivered"] == 1


def test_unknown_sid_does_not_invent_a_row():
    assert message_log.update_status("SM-never-sent", "delivered") is False
    assert message_log.recent() == []


def test_log_is_capped():
    for i in range(message_log.MAX_ENTRIES + 25):
        message_log.record_attempt(sid=f"SM{i}", to_phone="+15551230000",
                                   kind="k", group_id="grp_x", channel="sms",
                                   status="queued")
    rows = message_log.recent(limit=10_000)
    assert len(rows) == message_log.MAX_ENTRIES
    # Newest kept, oldest dropped.
    assert rows[0]["sid"] == f"SM{message_log.MAX_ENTRIES + 24}"


def test_summary_separates_failed_from_in_flight():
    message_log.record_attempt(sid="A", to_phone="+1", kind="", group_id="g",
                               channel="sms", status="delivered")
    message_log.record_attempt(sid="B", to_phone="+1", kind="", group_id="g",
                               channel="sms", status="undelivered")
    message_log.record_attempt(sid="C", to_phone="+1", kind="", group_id="g",
                               channel="sms", status="queued")
    message_log.record_attempt(sid="", to_phone="+1", kind="", group_id="g",
                               channel="sms", status="send_error")
    s = message_log.summary()
    assert (s["delivered"], s["failed"], s["in_flight"]) == (1, 2, 1)


def test_opted_out_counts_as_failed_not_in_flight():
    """An opted-out recipient will never receive the message, so calling it
    in-flight would imply it might still land."""
    message_log.record_attempt(sid="", to_phone="+1", kind="", group_id="g",
                               channel="sms", status="opted_out",
                               error="Twilio 21610")
    s = message_log.summary()
    assert s["failed"] == 1
    assert s["in_flight"] == 0


def test_recent_is_scoped_by_group():
    message_log.record_attempt(sid="A", to_phone="+1", kind="", group_id="grp_a",
                               channel="sms", status="queued")
    message_log.record_attempt(sid="B", to_phone="+1", kind="", group_id="grp_b",
                               channel="sms", status="queued")
    assert [r["sid"] for r in message_log.recent(group_id="grp_a")] == ["A"]
    assert len(message_log.recent()) == 2


# ── the status webhook ──────────────────────────────────────────────────────

def test_status_callback_rejects_an_unsigned_request(client, monkeypatch):
    """The endpoint is public. An unsigned POST must not be able to write."""
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    message_log.record_attempt(sid="SM999", to_phone="+15551230000", kind="",
                               group_id="g", channel="sms", status="queued")
    r = client.post("/twilio/status",
                    data={"MessageSid": "SM999", "MessageStatus": "delivered"})
    assert r.status_code == 403
    assert message_log.recent()[0]["status"] == "queued", "log was written anyway"


def test_status_callback_rejects_a_wrong_signature(client, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    r = client.post("/twilio/status",
                    data={"MessageSid": "SM999", "MessageStatus": "delivered"},
                    headers={"X-Twilio-Signature": "obviously-not-valid"})
    assert r.status_code == 403


def test_status_callback_with_a_valid_signature_updates_the_log(client, monkeypatch):
    token = "test-token"
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
    message_log.record_attempt(sid="SM777", to_phone="+15551230000",
                               kind="driver_route", group_id="g",
                               channel="sms", status="queued")

    url = "http://localhost/twilio/status"
    params = {"MessageSid": "SM777", "MessageStatus": "delivered"}
    signature = RequestValidator(token).compute_signature(url, params)

    r = client.post("/twilio/status", data=params,
                    headers={"X-Twilio-Signature": signature})
    assert r.status_code == 204, r.get_data(as_text=True)
    assert message_log.recent()[0]["status"] == "delivered"


def test_status_callback_records_a_carrier_failure(client, monkeypatch):
    token = "test-token"
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
    message_log.record_attempt(sid="SM555", to_phone="+15551230000", kind="",
                               group_id="g", channel="sms", status="queued")

    url = "http://localhost/twilio/status"
    params = {"MessageSid": "SM555", "MessageStatus": "undelivered",
              "ErrorCode": "30006"}
    signature = RequestValidator(token).compute_signature(url, params)

    r = client.post("/twilio/status", data=params,
                    headers={"X-Twilio-Signature": signature})
    assert r.status_code == 204
    row = message_log.recent()[0]
    assert row["status"] == "undelivered"
    assert "30006" in row["error"]
    assert message_log.summary()["failed"] == 1


def test_status_callback_without_an_auth_token_is_refused(client, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    r = client.post("/twilio/status", data={"MessageSid": "X"})
    assert r.status_code == 403


# ── sending ─────────────────────────────────────────────────────────────────

class _FakeMessage:
    sid = "SM-from-twilio"
    status = "queued"


class _FakeMessages:
    def __init__(self, raiser=None):
        self.raiser = raiser
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raiser:
            raise self.raiser
        return _FakeMessage()


class _FakeClient:
    def __init__(self, raiser=None):
        self.messages = _FakeMessages(raiser)


def _configure(monkeypatch):
    monkeypatch.setattr(sms, "ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(sms, "AUTH_TOKEN", "test-token")
    monkeypatch.setattr(sms, "FROM_NUMBER", "+18339681976")


def test_send_records_the_attempt_and_returns_the_sid(monkeypatch):
    _configure(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(sms, "Client", lambda *a, **k: fake)

    sid = sms.send_sms("+15559876543", "hello", kind="invite", group_id="grp_z")
    assert sid == "SM-from-twilio"

    row = message_log.recent()[0]
    assert row["sid"] == "SM-from-twilio"
    assert row["kind"] == "invite"
    assert row["group_id"] == "grp_z"
    assert row["status"] == "queued"


def test_opt_out_is_its_own_outcome(monkeypatch):
    """A recipient who replied STOP is not a transient error to retry."""
    _configure(monkeypatch)
    err = Exception("blocked")
    err.code = sms.OPTED_OUT_CODE
    monkeypatch.setattr(sms, "Client", lambda *a, **k: _FakeClient(raiser=err))

    with pytest.raises(sms.RecipientOptedOut):
        sms.send_sms("+15559876543", "hello", kind="reminder", group_id="g")

    row = message_log.recent()[0]
    assert row["status"] == "opted_out"


def test_a_failed_send_is_still_recorded(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(sms, "Client",
                        lambda *a, **k: _FakeClient(raiser=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        sms.send_sms("+15559876543", "hello", kind="reminder", group_id="g")

    row = message_log.recent()[0]
    assert row["status"] == "send_error"
    assert "boom" in row["error"]


def test_missing_credentials_are_recorded_not_silent(monkeypatch):
    monkeypatch.setattr(sms, "ACCOUNT_SID", None)
    monkeypatch.setattr(sms, "AUTH_TOKEN", None)
    monkeypatch.setattr(sms, "FROM_NUMBER", None)
    with pytest.raises(RuntimeError):
        sms.send_sms("+15559876543", "hello")
    assert message_log.recent()[0]["status"] == "send_error"


def test_no_status_callback_is_requested_in_testing(monkeypatch):
    """Twilio cannot reach a test host; asking it to would make every send
    generate retries and error logs."""
    _configure(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(sms, "Client", lambda *a, **k: fake)
    sms.send_sms("+15559876543", "hello")
    assert "status_callback" not in fake.messages.kwargs


def test_status_callback_url_needs_a_public_https_origin(monkeypatch):
    monkeypatch.delenv("FLASK_TESTING", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")
    assert sms._status_callback_url() == ""
    monkeypatch.setenv("APP_BASE_URL", "https://carpoolsync.com")
    assert sms._status_callback_url() == "https://carpoolsync.com/twilio/status"
    monkeypatch.setenv("FLASK_TESTING", "1")


# ── the preflight ───────────────────────────────────────────────────────────

def test_config_status_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(sms, "ACCOUNT_SID", None)
    monkeypatch.setattr(sms, "FROM_NUMBER", None)
    status = sms.config_status()
    assert status["ready"] is False
    joined = " ".join(status["problems"])
    assert "TWILIO_ACCOUNT_SID" in joined
    assert "TWILIO_FROM_NUMBER" in joined


def test_config_status_catches_a_whatsapp_prefix_left_on_for_sms(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(sms, "FROM_NUMBER", "whatsapp:+18339681976")
    monkeypatch.setenv("MESSAGE_CHANNEL", "sms")
    problems = " ".join(sms.config_status()["problems"])
    assert "whatsapp: prefix" in problems
    monkeypatch.setenv("MESSAGE_CHANNEL", "whatsapp")


# ── the admin page ──────────────────────────────────────────────────────────

def _admin_group_id():
    from auth import get_user_by_email
    return get_user_by_email(ADMIN["email"])["group_id"]


def test_admin_messages_page_renders(app, group):
    c = login(app.test_client(), ADMIN)
    message_log.record_attempt(sid="SM1", to_phone="+15551230000",
                               kind="driver_route", group_id=_admin_group_id(),
                               channel="sms", status="delivered")
    body = c.get("/admin/messages").get_data(as_text=True)
    assert "Messages" in body
    assert "driver_route" in body


def test_admin_messages_hides_other_groups(app, group):
    """An admin who is not on OWNER_EMAILS sees only their own carpool's
    messages. Phone numbers of families in another group are not theirs."""
    c = login(app.test_client(), ADMIN)
    message_log.record_attempt(sid="MINE", to_phone="+15551110000",
                               kind="mine", group_id=_admin_group_id(),
                               channel="sms", status="delivered")
    message_log.record_attempt(sid="THEIRS", to_phone="+15559990000",
                               kind="someone_elses", group_id="grp_other",
                               channel="sms", status="delivered")
    body = c.get("/admin/messages").get_data(as_text=True)
    assert "mine" in body
    assert "someone_elses" not in body
    assert "+15559990000" not in body


def test_admin_messages_requires_login(client):
    r = client.get("/admin/messages")
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")
