"""Test fixtures. DATA_DIR must be set before portal is imported, because
storage.DATA_DIR is computed at import time."""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="carpool-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["FLASK_TESTING"] = "1"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.setdefault("MESSAGE_CHANNEL", "whatsapp")
for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
          "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
          "GOOGLE_MAPS_API_KEY", "USE_SUPABASE_AUTH", "USE_SUPABASE_DB", "OWNER_EMAILS"):
    os.environ.pop(k, None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal  # noqa: E402

ADMIN = {"email": "admin@example.com", "password": "Sunflower99"}
PARENT = {"email": "dana@example.com", "password": "Sunflower99"}


def _csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ""


@pytest.fixture(scope="session")
def app():
    portal.app.config["TESTING"] = True
    return portal.app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def group(app):
    """Create one group with an admin, invite one parent, and return ids."""
    c = app.test_client()
    tok = _csrf(c, "/create-group")
    r = c.post("/create-group", data={
        "csrf_token": tok, "group_name": "Test Soccer", "name": "Test Admin",
        "family_name": "Nadler", "email": ADMIN["email"], "phone": "5551234567",
        "password": ADMIN["password"], "address": "12 Maple St, Maplewood, NJ",
        "child_name": "Avi"})
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    # invite a parent; Twilio is unconfigured so the link is in the flash
    dash = c.get("/").get_data(as_text=True)
    tok = re.search(r'id="inviteForm".*?name="csrf_token" value="([^"]+)"', dash, re.S).group(1)
    c.post("/invite", data={"csrf_token": tok, "phone": "5559876543", "family_id": ""})
    dash = c.get("/").get_data(as_text=True)
    link = re.search(r'(/signup\?token=[A-Za-z0-9_\-]+)', dash).group(1)
    c.get("/logout")
    p = app.test_client()
    tok = _csrf(p, link)
    token = link.split("token=")[1]
    r = p.post("/signup", data={
        "csrf_token": tok, "token": token, "name": "Dana Cohen", "email": PARENT["email"],
        "family_name": "Cohen", "child_name": "Noa", "password": PARENT["password"],
        "address": "48 Oak Ave, Maplewood, NJ"})
    assert r.status_code in (302, 200)
    return {"invite_link": link}


def login(client, who):
    # Every test logs in fresh; the limiter (8 per email per 5 minutes) is
    # doing its job, so reset it rather than share one client across tests.
    portal._rate_state.clear()
    tok = _csrf(client, "/login")
    r = client.post("/login", data={"csrf_token": tok, **who})
    assert r.status_code == 302, "login failed"
    return client


@pytest.fixture()
def admin(app, group):
    return login(app.test_client(), ADMIN)


@pytest.fixture()
def parent(app, group):
    return login(app.test_client(), PARENT)


def csrf_header(client):
    for cookie in client._cookies.values() if hasattr(client, "_cookies") else []:
        if cookie.key == "csrf_token":
            return {"X-CSRFToken": cookie.value}
    try:
        return {"X-CSRFToken": client.get_cookie("csrf_token").value}
    except Exception:
        return {}
