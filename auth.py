"""
Authentication helpers.
Users and invites are stored globally; each carries a group_id.
"""

import json
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
from storage import DATA_DIR, atomic_write_json, read_json

USERS_FILE = DATA_DIR / "users.json"
INVITES_FILE = DATA_DIR / "invites.json"

# Invite tokens expire after 7 days (matches `generate_invite_token` doc).
INVITE_TTL_DAYS = 7


def _load_users() -> list:
    return read_json(USERS_FILE, default=[])


def _save_users(users: list) -> None:
    atomic_write_json(USERS_FILE, users)


def _load_invites() -> list:
    return read_json(INVITES_FILE, default=[])


def _save_invites(invites: list) -> None:
    atomic_write_json(INVITES_FILE, invites)


def get_user_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    for u in _load_users():
        if u.get("email", "").lower() == email:
            return u
    return None


def get_user_by_phone(phone: str) -> dict | None:
    for u in _load_users():
        if u.get("phone") == phone:
            return u
    return None


def get_user_by_id(user_id: str) -> dict | None:
    for u in _load_users():
        if u.get("id") == user_id:
            return u
    return None


def verify_password(plain: str, hashed: str) -> bool:
    # bcrypt raises ValueError on an empty/garbage hash (e.g. a partially
    # wiped user record) — that must read as "wrong password", not a 500.
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError, AttributeError):
        return False


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def create_user(*, phone: str, name: str, email: str, password: str,
                role: str = "parent", family_id: str = "",
                child_name: str = "", address: str = "",
                group_id: str = "") -> dict:
    users = _load_users()
    user = {
        "id": f"user_{uuid.uuid4().hex[:12]}",
        "phone": phone,
        "name": name.strip(),
        "email": email.strip().lower(),
        "password_hash": _hash_password(password),
        "role": role,
        "family_id": family_id,
        "group_id": group_id,
        "child_name": child_name.strip(),
        "address": address.strip(),
        "joined_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    users.append(user)
    _save_users(users)
    return user


def generate_invite_token(phone: str, group_id: str,
                           family_id: str = "", family_name: str = "") -> str:
    invites = _load_invites()
    token = str(uuid.uuid4())
    invites.append({
        "token": token,
        "phone": phone,
        "group_id": group_id,
        "family_id": family_id,
        "family_name": family_name,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "used": False,
    })
    _save_invites(invites)
    return token


def verify_invite_token(token: str) -> dict | None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=INVITE_TTL_DAYS)
    for invite in _load_invites():
        if invite["token"] != token or invite.get("used"):
            continue
        # Reject anything older than the TTL.
        try:
            created = datetime.fromisoformat(invite["created_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            return None
        if created < cutoff:
            return None
        return invite
    return None


def mark_invite_used(token: str) -> None:
    invites = _load_invites()
    for invite in invites:
        if invite["token"] == token:
            invite["used"] = True
    _save_invites(invites)


def purge_old_tokens() -> None:
    """Remove used invites older than 30 days and used/expired resets older than 7 days."""
    cutoff_invites = datetime.now(timezone.utc) - timedelta(days=30)
    cutoff_resets  = datetime.now(timezone.utc) - timedelta(days=7)

    invites = _load_invites()
    invites = [
        i for i in invites
        if not i.get("used") or
        datetime.fromisoformat(i["created_at"].replace("Z", "+00:00")) > cutoff_invites
    ]
    _save_invites(invites)

    resets = _load_resets()
    resets = [
        r for r in resets
        if not r.get("used") and
        datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) > cutoff_resets
    ]
    _save_resets(resets)


# ── Password reset tokens ─────────────────────────────────────────────────────

RESETS_FILE = DATA_DIR / "resets.json"


def _load_resets() -> list:
    return read_json(RESETS_FILE, default=[])


def _save_resets(resets: list) -> None:
    atomic_write_json(RESETS_FILE, resets)


def generate_reset_token(user_id: str) -> str:
    resets = _load_resets()
    # Invalidate any existing unused tokens for this user
    for r in resets:
        if r["user_id"] == user_id:
            r["used"] = True
    token = str(uuid.uuid4())
    resets.append({
        "token": token,
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "used": False,
    })
    _save_resets(resets)
    return token


def verify_reset_token(token: str) -> dict | None:
    from datetime import timedelta
    for r in _load_resets():
        if r["token"] == token and not r["used"]:
            created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - created < timedelta(hours=1):
                return r
    return None


def mark_reset_used(token: str) -> None:
    resets = _load_resets()
    for r in resets:
        if r["token"] == token:
            r["used"] = True
    _save_resets(resets)


def update_password(user_id: str, new_password: str) -> None:
    users = _load_users()
    for u in users:
        if u["id"] == user_id:
            u["password_hash"] = _hash_password(new_password)
    _save_users(users)


def delete_user(user_id: str) -> bool:
    """Remove a user by ID. Returns True if a user was removed, False if not found."""
    users = _load_users()
    new_users = [u for u in users if u.get("id") != user_id]
    if len(new_users) == len(users):
        return False
    _save_users(new_users)
    return True


def get_users_by_group(group_id: str) -> list:
    """Return all users belonging to the given group."""
    return [u for u in _load_users() if u.get("group_id") == group_id]
