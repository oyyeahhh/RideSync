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
