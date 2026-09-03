"""A parent whose family record vanished must not be stranded.

This reproduces a real production state. Family records were a per-group file
on the Railway volume while user records were Postgres rows. The volume was
never actually mounted, so for accounts created before USE_SUPABASE_DB was
switched on, the family file was wiped on the next deploy and the user row
survived. The owner's account pointed at fam_nadler_7f86 and the families list
for her carpool was empty: claiming a ride failed, and the profile page could
not repair it because update_family() only updates a record that already
exists.
"""
import pytest

import families
import portal
from conftest import ADMIN, login


def _admin_user():
    from auth import get_user_by_email
    return get_user_by_email(ADMIN["email"])


def _wipe_families(group_id):
    """Recreate the exact broken state: user points at a family, list is empty."""
    families._save_families_json([], group_id)


@pytest.fixture()
def broken_family(group):
    """Wipe the families list, then put it back however the test left it."""
    u = _admin_user()
    gid = u["group_id"]
    before = families._load_families_json(gid)
    _wipe_families(gid)
    yield u, gid
    families._save_families_json(before, gid)


# ── the repair itself ───────────────────────────────────────────────────────

def test_missing_family_is_rebuilt_from_the_user_record(broken_family):
    user, gid = broken_family
    assert families._load_families_json(gid) == [], "precondition"

    fam = portal._family_for_user(user, gid)

    assert fam is not None, "should have rebuilt rather than returning nothing"
    assert fam.id == user["family_id"], "must keep the original id"
    assert fam.name == "Admin", "surname comes off the user's name"
    assert fam.primary_address.street == user["address"]
    assert [k.name for k in fam.kids] == [user["child_name"]]


def test_the_original_id_is_preserved(broken_family):
    """A fresh id would orphan the rotation order, trip history and karma,
    which all reference the family by id."""
    user, gid = broken_family
    portal._family_for_user(user, gid)
    assert families.get_all_family_ids(gid) == [user["family_id"]]


def test_repair_is_idempotent(broken_family):
    user, gid = broken_family
    portal._family_for_user(user, gid)
    portal._family_for_user(user, gid)
    portal._family_for_user(user, gid)
    assert len(families._load_families_json(gid)) == 1, "duplicated the record"


def test_an_existing_family_is_left_alone(group):
    """The repair must not overwrite a good record with account-derived values,
    since the family may have been edited since signup."""
    user = _admin_user()
    gid = user["group_id"]
    families.restore_family(user["family_id"], gid, name="Edited Name",
                            address="99 Somewhere Else", phone="+15550001111",
                            children=["Ari", "Tali"])
    fam = portal._family_for_user(user, gid)
    assert fam.name == "Edited Name"
    assert [k.name for k in fam.kids] == ["Ari", "Tali"]


def test_no_family_id_returns_none_rather_than_inventing_one(group):
    """An account with no family at all is a different problem, and guessing
    would attach a stranger to the carpool."""
    user = dict(_admin_user())
    user["family_id"] = ""
    assert portal._family_for_user(user, user["group_id"]) is None


# ── the routes that used to strand people ───────────────────────────────────

def test_claiming_a_ride_works_after_the_record_vanished(app, broken_family):
    """The original symptom: 'your family record no longer exists'."""
    user, gid = broken_family
    c = login(app.test_client(), ADMIN)

    from schedule import add_trip
    # Unclaimed: no driver assigned, which is what "claim" is for.
    trip = add_trip(date="2026-09-10", arrival_time="16:30",
                    destination_name="Maplewood Field",
                    destination_address="300 Valley St, Maplewood, NJ",
                    driver_family_id="", driver_name="", group_id=gid)

    from conftest import csrf_header
    r = c.post(f"/schedule/claim/{trip['id']}/outbound", headers=csrf_header(c))
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body.get("ok") is True, body
    assert body["trip"]["driver_family_id"] == user["family_id"]


def test_the_dashboard_repairs_it_before_anyone_sees_an_error(app, broken_family):
    user, gid = broken_family
    c = login(app.test_client(), ADMIN)
    assert c.get("/").status_code == 200
    assert families.get_all_family_ids(gid) == [user["family_id"]], \
        "dashboard should have healed the record on load"


def test_the_profile_page_shows_real_values_after_a_repair(app, broken_family):
    user, gid = broken_family
    c = login(app.test_client(), ADMIN)
    body = c.get("/profile").get_data(as_text=True)
    assert user["address"] in body
    assert user["child_name"] in body
