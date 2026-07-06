"""
Supabase-backed auth helpers, used alongside the existing bcrypt auth.py
during Phase 1 of the migration.

The contract:
- Magic-link sign-in: caller passes an email + a redirect URL. We ask
  Supabase to email a one-time link. When the user clicks it, they land
  on the redirect URL with a `code` query param.
- We exchange that code for a Supabase session. The Supabase user has
  a stable UUID (`supabase_uid`).
- We mirror that user into our existing users.json (or look up the
  existing record by email). Our internal `user_id` and `group_id`
  remain the source of truth for the rest of the app.
"""

import logging
from typing import Optional

from supabase_client import get_anon_client, get_service_client, is_configured
from auth import _load_users, _save_users, create_user as _create_internal_user, get_user_by_email

logger = logging.getLogger(__name__)


def send_magic_link(email: str, redirect_to: str) -> dict:
    """Ask Supabase to email a one-time sign-in link to `email`. The link
    will redirect to `redirect_to?code=...` when clicked.

    Returns {ok: bool, error: str | None}.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "error": "Please enter a valid email."}
    if not is_configured():
        return {"ok": False, "error": "Supabase is not configured on the server."}

    try:
        client = get_anon_client()
        # `sign_in_with_otp` with email = magic link by default.
        # `should_create_user` lets first-timers sign up via magic link too.
        client.auth.sign_in_with_otp({
            "email": email,
            "options": {
                "email_redirect_to": redirect_to,
                "should_create_user": True,
            },
        })
        logger.info("Magic link sent to %s***", email[:3])
        return {"ok": True, "error": None}
    except Exception as e:
        logger.error("Magic-link send failed for %s***: %s", email[:3], e)
        return {"ok": False, "error": str(e)}


def exchange_code_for_session(code: str) -> Optional[dict]:
    """Exchange a magic-link callback `code` for a Supabase session.
    Returns the Supabase user dict (with id, email) on success, or None.
    """
    if not code:
        return None
    if not is_configured():
        return None
    try:
        client = get_anon_client()
        resp = client.auth.exchange_code_for_session({"auth_code": code})
        # supabase-py returns an AuthResponse; .user is the User object.
        user = resp.user if hasattr(resp, "user") else None
        if user is None and hasattr(resp, "data"):
            user = resp.data.user if hasattr(resp.data, "user") else None
        if user is None:
            return None
        return {
            "id": user.id,
            "email": (user.email or "").lower(),
        }
    except Exception as e:
        logger.error("exchange_code_for_session failed: %s", e)
        return None


def signup_with_password(email: str, password: str) -> dict:
    """Create a Supabase Auth user with email + password. Returns
    {ok: bool, supabase_uid: str | None, error: str | None}.

    Requires 'Confirm email' to be OFF in Supabase Auth settings, otherwise
    the user can't sign in until they click an email confirmation link.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "supabase_uid": None, "error": "Invalid email."}
    if not password or len(password) < 8:
        return {"ok": False, "supabase_uid": None, "error": "Password must be at least 8 characters."}
    if not is_configured():
        return {"ok": False, "supabase_uid": None, "error": "Supabase is not configured."}

    try:
        # Use service client + admin API so we don't need email confirmation
        # AND we skip the email-confirm round trip entirely.
        client = get_service_client()
        resp = client.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,  # mark as confirmed without sending an email
        })
        user = resp.user if hasattr(resp, "user") else resp
        uid = user.id if hasattr(user, "id") else (user.get("id") if isinstance(user, dict) else None)
        if not uid:
            return {"ok": False, "supabase_uid": None, "error": "Supabase didn't return a user id."}
        logger.info("Supabase signup created uid=%s for %s***", uid, email[:3])
        return {"ok": True, "supabase_uid": uid, "error": None}
    except Exception as e:
        msg = str(e)
        if "already" in msg.lower() or "duplicate" in msg.lower():
            return {"ok": False, "supabase_uid": None, "error": "An account with that email already exists."}
        logger.error("Supabase signup failed for %s***: %s", email[:3], e)
        return {"ok": False, "supabase_uid": None, "error": f"Signup failed: {msg}"}


def admin_set_password(supabase_uid: str, password: str) -> dict:
    """Set a password on an EXISTING Supabase Auth user (service-role admin
    API). Used when a first-time magic-link signer completes signup at
    /create-group — their Supabase user already exists, so creating a new one
    would fail with 'already exists' and dead-end the flow.

    Returns {ok: bool, error: str | None}."""
    if not supabase_uid:
        return {"ok": False, "error": "Missing Supabase user id."}
    if not password or len(password) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters."}
    if not is_configured():
        return {"ok": False, "error": "Supabase is not configured."}
    try:
        client = get_service_client()
        client.auth.admin.update_user_by_id(supabase_uid, {"password": password})
        logger.info("Set password for existing Supabase uid=%s", supabase_uid)
        return {"ok": True, "error": None}
    except Exception as e:
        logger.error("admin_set_password failed for uid=%s: %s", supabase_uid, e)
        return {"ok": False, "error": "Could not attach a password to your account. Please try again."}


def signin_with_password(email: str, password: str) -> dict:
    """Verify email+password against Supabase Auth. Returns
    {ok: bool, supabase_uid: str | None, error: str | None}.
    """
    email = (email or "").strip().lower()
    if not email or not password:
        return {"ok": False, "supabase_uid": None, "error": "Email and password required."}
    if not is_configured():
        return {"ok": False, "supabase_uid": None, "error": "Supabase is not configured."}

    try:
        client = get_anon_client()
        resp = client.auth.sign_in_with_password({"email": email, "password": password})
        user = resp.user if hasattr(resp, "user") else None
        if user is None and hasattr(resp, "data"):
            user = resp.data.user if hasattr(resp.data, "user") else None
        uid = user.id if user and hasattr(user, "id") else None
        if not uid:
            return {"ok": False, "supabase_uid": None, "error": "Invalid email or password."}
        return {"ok": True, "supabase_uid": uid, "error": None}
    except Exception as e:
        # Supabase returns a specific error for bad credentials; normalize.
        msg = str(e).lower()
        if "invalid" in msg or "credential" in msg or "password" in msg:
            return {"ok": False, "supabase_uid": None, "error": "Invalid email or password."}
        logger.error("Supabase signin failed for %s***: %s", email[:3], e)
        return {"ok": False, "supabase_uid": None, "error": "Could not sign in. Please try again."}


def find_or_link_internal_user(supabase_uid: str, email: str) -> Optional[dict]:
    """Look up our internal user record by either supabase_uid or email.
    If found by email but no supabase_uid yet, attach it. Returns the
    internal user dict, or None if no internal user exists for this email
    (which means the magic-link signer is brand new and needs to complete
    signup at /create-group or via an invite).
    """
    email = (email or "").strip().lower()
    users = _load_users()

    # 1. Match by stored supabase_uid (fastest path for returning users).
    for u in users:
        if u.get("supabase_uid") == supabase_uid:
            return u

    # 2. Match by email — attach the supabase_uid for future logins.
    for u in users:
        if u.get("email", "").lower() == email:
            u["supabase_uid"] = supabase_uid
            _save_users(users)
            return u

    # 3. No internal record yet. Caller decides whether to create one
    #    (e.g. via create-group flow) or reject the login.
    return None
