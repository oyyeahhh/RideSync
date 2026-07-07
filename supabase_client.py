"""
Supabase client — singletons for the anon client (used for end-user-scoped
operations) and the service-role client (used server-side for admin work).

Env vars expected:
- SUPABASE_URL              — e.g. https://abcdefgh.supabase.co
- SUPABASE_ANON_KEY         — public "publishable" key, safe in browser
- SUPABASE_SERVICE_ROLE_KEY — secret key, bypasses RLS, never share

This module is import-safe even when env vars are missing — clients are
lazily constructed on first use, so the rest of the app can still import
this file during the migration.
"""

import os
from typing import Optional

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None  # type: ignore
    Client = None  # type: ignore


_anon_client: Optional["Client"] = None
_service_client: Optional["Client"] = None


def is_configured() -> bool:
    """Return True iff all three Supabase env vars are present and the SDK
    is installed. Lets routes/feature-flags branch cleanly during the
    migration ('use Supabase if available, else fall back to JSON')."""
    return bool(
        create_client is not None
        and os.environ.get("SUPABASE_URL")
        and os.environ.get("SUPABASE_ANON_KEY")
        and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )


def get_anon_client() -> "Client":
    """The 'anon' / publishable client — used for end-user auth flows
    (signup, signin, magic link). Subject to Row-Level Security policies."""
    global _anon_client
    if _anon_client is None:
        if create_client is None:
            raise RuntimeError("supabase-py not installed")
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_ANON_KEY"]
        _anon_client = create_client(url, key)
    return _anon_client


def get_service_client() -> "Client":
    """The 'service_role' client — bypasses Row-Level Security entirely.
    Use ONLY in server-side admin contexts (cron jobs, migrations, admin
    routes). Never expose this client's calls to untrusted input."""
    global _service_client
    if _service_client is None:
        if create_client is None:
            raise RuntimeError("supabase-py not installed")
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _service_client = create_client(url, key)
    return _service_client


def health_check() -> dict:
    """Returns a dict describing the Supabase connection status. Used by
    the /health endpoint to show whether the migration is wired up."""
    if not is_configured():
        missing = [
            v for v in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY")
            if not os.environ.get(v)
        ]
        return {
            "ok": False,
            "configured": False,
            "missing_env": missing,
            "sdk_installed": create_client is not None,
        }
    try:
        # Touch the service client; an auth.admin call confirms creds work.
        client = get_service_client()
        resp = client.auth.admin.list_users(page=1, per_page=100)
        users = resp if isinstance(resp, list) else getattr(resp, "users", []) or []
        result = {
            "ok": True,
            "configured": True,
            "url": os.environ.get("SUPABASE_URL", ""),
            "auth_users": len(users),
        }
        # Schema probe: the memberships table only exists once schema.sql has
        # been run, so its reachability tells /health whether Phase 2a is done.
        # (Service role bypasses RLS, so deny-all policies don't block this.)
        try:
            client.table("memberships").select("user_id", count="exact").limit(1).execute()
            result["schema_applied"] = True
        except Exception:
            result["schema_applied"] = False
        return result
    except Exception as e:
        return {
            "ok": False,
            "configured": True,
            "error": str(e),
        }
