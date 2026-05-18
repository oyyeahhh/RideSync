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
