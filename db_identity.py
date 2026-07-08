"""
Supabase-Postgres-backed identity store: users, memberships, groups.

Enabled with USE_SUPABASE_DB=1 (plus the three SUPABASE_* env vars).
auth.py and groups.py route their _load/_save seam functions here when
enabled, so the rest of the app keeps its list-of-dicts contract:

- users:  [{id, phone, name, email, password_hash, role, family_id,
            group_id, child_name, address, joined_at, supabase_uid?,
            calendar_token?}, ...]
- groups: [{id, name, created_at, display_token?}, ...]

Mapping: one user dict = one `users` row + (at most) one `memberships`
row carrying the group-scoped fields (group_id, family_id, role). The
flattened dict can only express one group per user, which matches the
rest of the app today; when multi-group lands, this seam is what changes.

Saves are whole-list replacements — the JSON files worked the same way,
and every caller in the app does full-list read → modify → write. Rows
absent from the saved list are deleted. Failures RAISE instead of
returning defaults, same "loud, never wipe" philosophy as storage.read_json.

Prerequisite: run supabase/migration_2b_identity.sql once (drops the
memberships.family_id foreign key — families still live in JSON, so the
referenced rows don't exist in Postgres yet).
"""

import os
import logging
from datetime import datetime, timezone

from supabase_client import get_service_client, is_configured

logger = logging.getLogger(__name__)


def identity_db_enabled() -> bool:
    """True when identity (users/memberships/groups) should live in Postgres."""
    return os.environ.get("USE_SUPABASE_DB", "").strip() == "1" and is_configured()


def _z(ts) -> str:
    """Normalize a Postgres timestamptz string to the Z-suffixed ISO form the
    JSON files always used, so downstream parsing stays identical."""
    if not ts:
        return ""
    return str(ts).replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── Users ─────────────────────────────────────────────────────────────────────

def db_load_users() -> list:
    client = get_service_client()
    user_rows = client.table("users").select("*").order("joined_at").execute().data or []
    mem_rows = client.table("memberships").select("*").execute().data or []
    mem_by_user: dict = {}
    for m in mem_rows:
        mem_by_user.setdefault(m["user_id"], m)

    users = []
    for r in user_rows:
        m = mem_by_user.get(r["id"]) or {}
        u = {
            "id": r["id"],
            "phone": r.get("phone") or "",
            "name": r.get("name") or "",
            "email": r.get("email") or "",
            "password_hash": r.get("password_hash") or "",
            "role": m.get("role") or "parent",
            "family_id": m.get("family_id") or "",
            "group_id": m.get("group_id") or "",
            "child_name": r.get("child_name") or "",
            "address": r.get("address") or "",
            "joined_at": _z(r.get("joined_at")),
        }
        if r.get("supabase_uid"):
            u["supabase_uid"] = r["supabase_uid"]
        if r.get("calendar_token"):
            u["calendar_token"] = r["calendar_token"]
        users.append(u)
    return users


def db_save_users(users: list) -> None:
    client = get_service_client()
    now = _now_iso()

    user_rows, mem_rows = [], []
    for u in users:
        # PostgREST bulk upserts need identical keys on every row, and
        # NOT NULL DEFAULT columns reject explicit nulls — so always send
        # every column with a real value.
        user_rows.append({
            "id": u["id"],
            "supabase_uid": u.get("supabase_uid") or None,
            "email": (u.get("email") or "").strip().lower(),
            "password_hash": u.get("password_hash") or None,
            "name": u.get("name") or "",
            "phone": u.get("phone") or None,
            "child_name": u.get("child_name") or None,
            "address": u.get("address") or None,
            "joined_at": u.get("joined_at") or now,
            "calendar_token": u.get("calendar_token") or None,
        })
        if u.get("group_id"):
            mem_rows.append({
                "user_id": u["id"],
                "group_id": u["group_id"],
                "family_id": u.get("family_id") or None,
                "role": u.get("role") or "parent",
            })

    if user_rows:
        client.table("users").upsert(user_rows).execute()

    # Users removed from the list are deleted (memberships cascade with them).
    keep_ids = [u["id"] for u in users]
    delete_q = client.table("users").delete()
    if keep_ids:
        delete_q = delete_q.not_.in_("id", keep_ids)
    else:
        delete_q = delete_q.neq("id", "")  # empty list = wipe, same as JSON
    delete_q.execute()

    # Memberships: drop rows that no longer match, then upsert the current set.
    existing = client.table("memberships").select("user_id,group_id").execute().data or []
    desired = {(m["user_id"], m["group_id"]) for m in mem_rows}
    for row in existing:
        if (row["user_id"], row["group_id"]) not in desired:
            client.table("memberships").delete() \
                .eq("user_id", row["user_id"]).eq("group_id", row["group_id"]).execute()
    if mem_rows:
        client.table("memberships").upsert(mem_rows).execute()


# ── Groups ────────────────────────────────────────────────────────────────────

def db_load_groups() -> list:
    client = get_service_client()
    rows = client.table("groups").select("id,name,display_token,created_at") \
        .order("created_at").execute().data or []
    groups = []
    for r in rows:
        g = {
            "id": r["id"],
            "name": r.get("name") or "",
            "created_at": _z(r.get("created_at")),
        }
        if r.get("display_token"):
            g["display_token"] = r["display_token"]
        groups.append(g)
    return groups


def db_save_groups(groups: list) -> None:
    client = get_service_client()
    now = _now_iso()
    rows = [{
        "id": g["id"],
        "name": g.get("name") or "",
        "display_token": g.get("display_token") or None,
        "created_at": g.get("created_at") or now,
    } for g in groups]

    if rows:
        client.table("groups").upsert(rows).execute()

    keep_ids = [g["id"] for g in groups]
    delete_q = client.table("groups").delete()
    if keep_ids:
        delete_q = delete_q.not_.in_("id", keep_ids)
    else:
        delete_q = delete_q.neq("id", "")
    delete_q.execute()


# ── One-time JSON → Postgres migration ────────────────────────────────────────

def migrate_json_identity_if_needed() -> None:
    """If Postgres identity is enabled but its users table is empty, copy any
    existing JSON users/groups into it. Idempotent: skips once the DB has
    users, and upserts are safe to repeat. Runs at app startup so flipping
    USE_SUPABASE_DB=1 on a live deploy carries everyone over automatically.
    Uses print() because this runs before logging is configured."""
    if not identity_db_enabled():
        return

    from storage import DATA_DIR, read_json

    client = get_service_client()
    resp = client.table("users").select("id", count="exact").limit(1).execute()
    if (resp.count or 0) > 0:
        return  # DB already populated — it is the source of truth now.

    json_users = read_json(DATA_DIR / "users.json", default=[])
    json_groups = read_json(DATA_DIR / "groups.json", default=[])
    if not json_users and not json_groups:
        print("[IDENTITY MIGRATION] Postgres empty and no JSON identity to migrate — fresh start.")
        return

    # Legacy users can reference a group (grp_main) that predates groups.json.
    # Register it so the membership insert doesn't hit a missing-group FK.
    known = {g["id"] for g in json_groups}
    missing = {u.get("group_id") for u in json_users if u.get("group_id")} - known
    for gid_ in sorted(missing):
        try:
            from config import load_config
            name = load_config(gid_).get("group_name") or "Carpool"
        except Exception:
            name = "Carpool"
        json_groups.append({"id": gid_, "name": name, "created_at": _now_iso()})

    print(f"[IDENTITY MIGRATION] Copying {len(json_groups)} group(s) and "
          f"{len(json_users)} user(s) from JSON into Supabase Postgres...")
    if json_groups:
        db_save_groups(json_groups)
    if json_users:
        db_save_users(json_users)
    print("[IDENTITY MIGRATION] ✅ Done. JSON files left in place as a cold backup — "
          "the app now reads and writes identity in Postgres.")
