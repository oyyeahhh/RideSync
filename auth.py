"""
Authentication helpers.
Users and invites are stored globally; each carries a group_id.
"""

import json
import uuid
from datetime import datetime, timezone

import bcrypt
from storage import DATA_DIR

USERS_FILE = DATA_DIR / "users.json"
INVITES_FILE = DATA_DIR / "invites.json"


def _load_users() -> list:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    return []


def _save_users(users: list) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2))


def _load_invites() -> list:
    if INVITES_FILE.exists():
        return json.loads(INVITES_FILE.read_text())
    return []


def _save_invites(invites: list) -> None:
    INVITES_FILE.write_text(json.dumps(invites, indent=2))


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
    return bcrypt.checkpw(plain.encode(), hashed.encode())


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
    for invite in _load_invites():
        if invite["token"] == token and not invite["used"]:
            return invite
    return None


def mark_invite_used(token: str) -> None:
    invites = _load_invites()
    for invite in invites:
        if invite["token"] == token:
            invite["used"] = True
    _save_invites(invites)
