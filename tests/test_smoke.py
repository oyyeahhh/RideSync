"""Every GET route, as nobody and as admin, must never 500. This is the test
that would have caught the dashboard-bricking trip before it deployed."""
import re

import pytest

import portal


def _get_routes():
    for rule in portal.app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        if rule.arguments:
            continue  # parameterised routes are covered by the flow tests
        yield rule.rule


ROUTES = sorted(set(_get_routes()))


@pytest.mark.parametrize("path", ROUTES)
def test_anonymous_never_500(client, path):
    r = client.get(path)
    assert r.status_code < 500, f"{path} -> {r.status_code}"


@pytest.mark.parametrize("path", ROUTES)
def test_admin_never_500(admin, path):
    r = admin.get(path)
    assert r.status_code < 500, f"{path} -> {r.status_code}"


def test_parent_dashboard_renders_without_driver_buttons(parent):
    html = parent.get("/").get_data(as_text=True)
    assert "is driving." in html
    assert "openLateModal()" not in html.split("<!-- Running Late Modal -->")[0]  # no button, modal markup may exist
    assert 'id="m-end-date"' not in html


def test_admin_dashboard_has_manage_section(admin):
    html = admin.get("/").get_data(as_text=True)
    assert "Manage carpool" in html
    assert "Trip Settings" in html
