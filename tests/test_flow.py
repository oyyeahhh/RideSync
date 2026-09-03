"""The core flow and every fix on the branch, against the Flask test client."""
import json
import re

from conftest import csrf_header


def _json(client, path, payload):
    return client.post(path, data=json.dumps(payload),
                       headers={"Content-Type": "application/json", **csrf_header(client)})


def test_public_pages(client):
    for path in ("/about", "/login", "/health", "/create-group", "/forgot-password"):
        assert client.get(path).status_code == 200, path


def test_tokenless_login_establishes_no_session(app, group):
    c = app.test_client()
    r = c.post("/login", data={"email": "admin@example.com", "password": "Sunflower99"})
    assert r.status_code in (302, 400)
    assert c.get("/").status_code == 302  # still redirected to login


def test_add_trip_and_dashboard(admin):
    r = _json(admin, "/schedule/add", {
        "date": "2026-09-10", "destination_name": "Maplewood Field",
        "destination_address": "300 Valley St", "arrival_time": "16:30", "return_time": "18:00"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert admin.get("/").status_code == 200


def test_bad_trip_times_are_rejected_and_dashboard_survives(admin):
    bad = [
        {"date": "2026-09-11", "destination_name": "x", "destination_address": "y", "arrival_time": ""},
        {"date": "2026-09-12", "destination_name": "x", "destination_address": "y", "arrival_time": "4pm"},
        {"date": "2026-09-13", "destination_name": "x", "destination_address": "y", "arrival_time": "99:00"},
        {"date": "9/2/26", "destination_name": "x", "destination_address": "y", "arrival_time": "16:30"},
        {"date": "2026-09-14", "destination_name": "x", "destination_address": "y", "arrival_time": "16:30", "return_time": "nope"},
    ]
    for payload in bad:
        assert _json(admin, "/schedule/add", payload).status_code == 400, payload
    assert admin.get("/").status_code == 200


def test_recurring_series(admin):
    r = _json(admin, "/schedule/add-recurring", {
        "start_date": "2026-09-14", "end_date": "2026-09-25", "weekdays": [0, 2],
        "destination_name": "Maplewood Field", "destination_address": "300 Valley St", "arrival_time": ""})
    assert r.status_code == 400
    r = _json(admin, "/schedule/add-recurring", {
        "start_date": "2026-09-14", "end_date": "2026-09-25", "weekdays": [0, 2],
        "destination_name": "Maplewood Field", "destination_address": "300 Valley St", "arrival_time": "16:30"})
    assert r.status_code == 200 and r.get_json()["count"] == 4
    assert admin.get("/").status_code == 200


def test_trip_settings_validation(admin):
    dash = admin.get("/").get_data(as_text=True)
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', dash).group(1)
    for form in ({"arrival_date": "9/2/26", "arrival_time": "16:30"},
                 {"arrival_date": "2026-09-20", "arrival_time": "4pm"},
                 {"arrival_date": "2026-09-20", "arrival_time": "16:30", "timezone": "Mars/Olympus"}):
        r = admin.post("/save-trip", data={"csrf_token": tok, "destination_address": "1 Way", **form},
                       follow_redirects=True)
        assert "Settings were not saved" in r.get_data(as_text=True), form
    assert admin.get("/").status_code == 200


def test_attendance_any_date_and_ownership(parent, admin):
    dash = parent.get("/").get_data(as_text=True)
    fid = re.search(r'const MY_FAMILY_ID = "([^"]+)"', dash).group(1)
    r = _json(parent, "/toggle-absent", {"family_id": fid, "date": "2026-09-16"})
    assert r.status_code == 200 and r.get_json()["date"] == "2026-09-16"
    assert _json(parent, "/toggle-absent", {"family_id": fid, "date": "9/16/26"}).status_code == 400
    assert _json(parent, "/toggle-absent", {"family_id": "fam_nobody_0000", "date": "2026-09-16"}).status_code == 403
    dash = parent.get("/").get_data(as_text=True)
    absences = json.loads(re.search(r"const ABSENCES = (\{.*?\});", dash).group(1))
    assert fid in absences.get("2026-09-16", [])


def test_removed_and_gated_routes(admin, client):
    assert client.get("/health/smtp-test?email=a@b.c").status_code == 404
    assert admin.get("/admin/backup").status_code == 404  # no OWNER_EMAILS in tests
    assert admin.get("/admin/system").status_code == 200


def test_auth_callbacks_take_every_link_shape(client):
    r = client.get("/auth/callback")
    assert r.status_code == 200 and "auth/fragment" in r.get_data(as_text=True)
    r = client.get("/auth/reset-callback")
    assert r.status_code == 200 and "auth/fragment" in r.get_data(as_text=True)
    assert client.get("/auth/callback?code=bogus").status_code == 400
    assert client.get("/auth/callback?token_hash=bogus&type=recovery").status_code == 400


def test_running_late_reports_counts(admin):
    r = _json(admin, "/running-late", {"minutes": "5"})
    assert r.status_code == 200
    data = r.get_json()
    assert set(("ok", "sent", "failed")) <= set(data)


def test_health_does_not_claim_persistence_without_a_volume(client, tmp_path):
    """/health must not say data survives redeploys when DATA_DIR is just a
    directory on the container filesystem.

    This is the bug that hid a month of signed-out parents: the old check
    compared DATA_DIR to the code directory, and any other path counted as
    "PERSISTENT". Production ran DATA_DIR=/data with no volume mounted there,
    so /health reported persistence while every deploy wiped the directory.
    The test fixture's DATA_DIR is a temp dir and never a mount point, so the
    honest answer here is always EPHEMERAL.
    """
    body = client.get("/health").get_data(as_text=True)
    assert "EPHEMERAL" in body, body
    assert "no volume" in body.lower(), body
    assert "will survive redeploys" not in body, body


def test_storage_persistence_detects_a_mount_point(monkeypatch):
    """The probe reports PERSISTENT only when the kernel says it is a mount."""
    import portal
    monkeypatch.setattr(portal.os.path, "ismount", lambda p: True)
    status, note = portal._storage_persistence()
    assert status.startswith("✅"), status
    assert "survive" in note.lower(), note

    monkeypatch.setattr(portal.os.path, "ismount", lambda p: False)
    status, note = portal._storage_persistence()
    assert "EPHEMERAL" in status, status
    assert "no volume" in note.lower(), note


def test_login_copy_follows_the_message_channel(client, monkeypatch):
    """A parent on SMS must not be told to check WhatsApp.

    MESSAGE_CHANNEL decides where messages actually go. Before this, the
    templates said WhatsApp regardless, so flipping the flag would have told
    every parent to open an app the app no longer uses.
    """
    monkeypatch.setenv("MESSAGE_CHANNEL", "sms")
    body = client.get("/login").get_data(as_text=True)
    assert "WhatsApp" not in body, "login page still mentions WhatsApp on SMS"
    assert "texted you" in body, body[-1500:]

    monkeypatch.setenv("MESSAGE_CHANNEL", "whatsapp")
    body = client.get("/login").get_data(as_text=True)
    assert "WhatsApp" in body, "login page should say WhatsApp while on WhatsApp"


def test_forgot_password_copy_follows_the_message_channel(client, monkeypatch):
    monkeypatch.setenv("MESSAGE_CHANNEL", "sms")
    body = client.get("/forgot-password").get_data(as_text=True)
    assert "WhatsApp" not in body, "forgot-password still mentions WhatsApp on SMS"


def test_channel_context_processor(monkeypatch):
    """The one place the channel name is decided."""
    import portal
    with portal.app.test_request_context("/"):
        monkeypatch.setenv("MESSAGE_CHANNEL", "sms")
        ctx = portal.inject_message_channel()
        assert ctx["channel"] == "text message"
        assert ctx["on_whatsapp"] is False

        monkeypatch.setenv("MESSAGE_CHANNEL", "whatsapp")
        ctx = portal.inject_message_channel()
        assert ctx["channel"] == "WhatsApp"
        assert ctx["on_whatsapp"] is True

        # An unset or bogus value must not silently become SMS and start
        # sending bare numbers through the WhatsApp sandbox.
        monkeypatch.delenv("MESSAGE_CHANNEL", raising=False)
        assert portal.inject_message_channel()["on_whatsapp"] is True
        monkeypatch.setenv("MESSAGE_CHANNEL", "carrier-pigeon")
        assert portal.inject_message_channel()["on_whatsapp"] is True
