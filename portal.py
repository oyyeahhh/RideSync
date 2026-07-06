"""
Web dashboard. Run with:
    python3 portal.py

Then open http://localhost:3000 in your browser.
"""

import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, Response, session, flash,
)
from flask_session import Session
from twilio.request_validator import RequestValidator

from config import (
    ADMIN_PHONE,
    arrival_time, get_destination_id, load_config, save_config,
    get_group_name, get_assignment_mode, set_assignment_mode,
)
from storage import DATA_DIR, CODE_DIR, group_dir, migrate_legacy_data, atomic_write_json, read_json
from families import get_family, get_destination, get_all_family_ids, add_family
from rotation import _load as _load_rotation, add_to_rotation, set_driver as _set_driver, advance as advance_rotation, set_index as _set_rotation_index
from trips import get_stats, load_trips, record_trip
from schedule import load_schedule, save_schedule, add_trip, update_trip, remove_trip, remove_series, add_recurring_trips, claim_trip
from karma import get_karma, record_swap_request as _record_swap_req, record_swap_cover as _record_swap_cover
from cal_feed import build_ics
from auth import (
    get_user_by_email, get_user_by_id,
    verify_password, create_user,
    generate_invite_token, verify_invite_token, mark_invite_used,
    generate_reset_token, verify_reset_token, mark_reset_used, update_password,
    purge_old_tokens, delete_user, get_users_by_group,
)
from groups import create_group as _create_group, get_group, list_groups, get_or_create_display_token, regenerate_display_token, find_group_by_display_token
from sms import send_sms, send_route_sms, SANDBOX_NUMBER, SANDBOX_KEYWORD
from absences import toggle_absent, get_absences
from route_cache import load as load_route_cache, save as save_route_cache
from routing import compute_optimal_route, build_maps_url
from geocode import geocode_address
from location import start_ride, stop_ride, update_location, get_location

load_dotenv()

# Run legacy data migration on startup so existing single-group data is accessible.
# Only seeds grp_main if there is legacy flat data in DATA_DIR — fresh deploys skip this.
_legacy_data_present = any((DATA_DIR / fname).exists() for fname in [
    "families.json", "rotation.json", "schedule.json", "trip_config.json",
])
if _legacy_data_present:
    migrate_legacy_data("grp_main")


def _bootstrap_legacy_group() -> None:
    """
    One-time bootstrap for deployments upgrading from single-group to multi-group.
    Only runs if there are legacy users without a group_id, OR if legacy flat data
    files exist. Fresh deploys skip this entirely so they don't get a stale grp_main.
    """
    from groups import _load_groups, _save_groups
    from auth import _load_users, _save_users
    from config import load_config

    users = _load_users()
    needs_user_backfill = any(u.get("group_id") in (None, "") for u in users)

    if not needs_user_backfill and not _legacy_data_present:
        return  # Fresh deploy with no legacy state — don't create grp_main.

    # 1. Ensure grp_main is registered in groups.json
    groups = _load_groups()
    if not any(g["id"] == "grp_main" for g in groups):
        try:
            cfg = load_config("grp_main")
            name = cfg.get("group_name", "Carpool")
        except Exception:
            name = "Carpool"
        groups.insert(0, {
            "id": "grp_main",
            "name": name,
            "created_at": "2026-01-01T00:00:00Z",
        })
        _save_groups(groups)

    # 2. Backfill group_id on all legacy users that don't have one
    patched = False
    for u in users:
        if not u.get("group_id"):
            u["group_id"] = "grp_main"
            patched = True
    if patched:
        _save_users(users)


_bootstrap_legacy_group()


def _emergency_password_reset() -> None:
    """
    Emergency recovery path: if both EMERGENCY_RESET_EMAIL and
    EMERGENCY_RESET_PASSWORD are set as Railway env vars, reset that user's
    password on app startup and log the action. Then DELETE the env vars and
    redeploy.

    Now also lists ALL users in the system (with masked emails) so we can
    see exactly what's stored — useful when "I can't log in" turns out to
    be an email typo / wrong account.
    """
    target_email = os.environ.get("EMERGENCY_RESET_EMAIL", "").strip().lower()
    new_password = os.environ.get("EMERGENCY_RESET_PASSWORD", "")
    if not target_email and not new_password:
        return  # no env vars set → silent skip

    try:
        from auth import _load_users, get_user_by_email, update_password, verify_password
        all_users = _load_users()

        # Always log a redacted summary of what's in the user file.
        print(f"[EMERGENCY RESET] {len(all_users)} user(s) in users.json:")
        for u in all_users:
            em = u.get("email", "")
            masked = (em[:3] + "***" + em[em.find("@"):]) if "@" in em else em
            print(f"   - id={u.get('id', '?')} email={masked} "
                  f"name={u.get('name', '')!r} group={u.get('group_id', '')}")

        if not target_email or not new_password:
            print("[EMERGENCY RESET] Missing email or password env var. Skipping reset.")
            return
        if len(new_password) < 8:
            print("[EMERGENCY RESET] Password too short (need 8+ chars). Skipping.")
            return

        user = get_user_by_email(target_email)
        if not user:
            print(f"[EMERGENCY RESET] ❌ No user with email {target_email!r}.")
            print(f"[EMERGENCY RESET]    Compare to the list above — possibly a typo or wrong case.")
            return

        update_password(user["id"], new_password)

        # Verify the new password actually works (catches save-vs-read inconsistencies).
        reloaded = get_user_by_email(target_email)
        ok = reloaded and verify_password(new_password, reloaded.get("password_hash", ""))
        if ok:
            print(f"[EMERGENCY RESET] ✅ Password reset and verified for {target_email}.")
            print(f"[EMERGENCY RESET]    user_id={user['id']}  group_id={user.get('group_id', '')}")
        else:
            print(f"[EMERGENCY RESET] ⚠️  Password reset but VERIFICATION FAILED. Possible disk/save issue.")

        print("[EMERGENCY RESET] IMPORTANT: remove EMERGENCY_RESET_EMAIL and "
              "EMERGENCY_RESET_PASSWORD from Railway env vars and redeploy.")
    except Exception as e:
        import traceback
        print(f"[EMERGENCY RESET] Failed: {e}")
        traceback.print_exc()


_emergency_password_reset()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Startup diagnostics ───────────────────────────────────────────────────────
def _startup_check() -> None:
    import stat
    data_dir_env = os.environ.get("DATA_DIR", "NOT SET")
    logger.info("=" * 60)
    logger.info("DATA_DIR env var : %s", data_dir_env)
    logger.info("DATA_DIR resolved: %s", str(DATA_DIR))
    logger.info("DATA_DIR exists  : %s", DATA_DIR.exists())

    # Check if it's writable
    try:
        test_file = DATA_DIR / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        logger.info("DATA_DIR writable: YES")
    except Exception as e:
        logger.error("DATA_DIR writable: NO — %s", e)

    # Warn if falling back to code directory (ephemeral on Railway)
    if str(DATA_DIR) == str(CODE_DIR):
        logger.warning("⚠️  DATA_DIR is the CODE directory — data will be lost on redeploy!")
        logger.warning("⚠️  Set DATA_DIR=/data and mount a Railway volume at /data.")
    else:
        logger.info("✅ DATA_DIR is separate from code dir — data should persist.")

    logger.info("=" * 60)

_startup_check()

app = Flask(__name__)
_secret = os.environ.get("SECRET_KEY", "")
# Refuse to start in production without a real SECRET_KEY.
# Treat "Railway-like" (DATA_DIR set + not the code dir) as production.
_is_prod = bool(os.environ.get("DATA_DIR")) and str(DATA_DIR) != str(CODE_DIR)
if not _secret:
    if _is_prod:
        raise RuntimeError(
            "SECRET_KEY is not set. Refusing to start in production. "
            "Set SECRET_KEY in your Railway environment variables."
        )
    logger.warning("SECRET_KEY is not set — using an insecure default (dev only).")
    _secret = "dev-secret-change-me"
app.secret_key = _secret

# Harden session cookies. SameSite=Lax lets normal links work; Secure means
# cookies only travel over HTTPS (Railway terminates TLS for us); HttpOnly
# blocks JS from reading the cookie.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = _is_prod  # only require HTTPS in prod
# Limit POST body size (defense against runaway form uploads bloating JSON).
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB

# CSRF protection. Flask-WTF reads tokens from a hidden form field OR an
# X-CSRFToken header (used by our fetch()-based dashboard endpoints).
from flask_wtf.csrf import CSRFProtect, generate_csrf
csrf = CSRFProtect(app)


@app.context_processor
def _inject_csrf_token():
    """Expose csrf_token() to every template."""
    return {"csrf_token": generate_csrf}


@app.after_request
def _csrf_cookie(response):
    """Mirror the CSRF token into a readable cookie so JS can pull it for fetch().
    HttpOnly is intentionally False here; the token is meant to be readable by
    same-origin JS, and Lax SameSite blocks cross-site reads."""
    # Skip cookieless/public endpoints — /calendar/<token>.ics is served with
    # Cache-Control: public, and a Set-Cookie header must not end up in shared
    # caches (nor does a calendar app or kitchen iPad need a CSRF token).
    if request.path.startswith(("/calendar/", "/display/", "/static/", "/health")):
        return response
    try:
        response.set_cookie(
            "csrf_token", generate_csrf(),
            secure=_is_prod, samesite="Lax", httponly=False,
        )
    except Exception:
        pass
    return response


@app.errorhandler(404)
def _not_found(e):
    """Return JSON for API-ish paths, friendly mascot page otherwise."""
    if request.path.startswith(("/admin/", "/api/", "/send-route", "/save-trip", "/schedule/")):
        return jsonify({"ok": False, "error": "Not found"}), 404
    return render_template("404.html"), 404


@app.errorhandler(500)
def _server_error(e):
    logger.exception("Internal server error on %s", request.path)
    if request.path.startswith(("/admin/", "/api/", "/send-route", "/save-trip", "/schedule/")):
        return jsonify({"ok": False, "error": "Server error"}), 500
    return ("<h2>Something went wrong</h2>"
            "<p>The error has been logged. "
            "<a href='/dashboard'>Back to dashboard</a></p>"), 500


@app.errorhandler(413)
def _too_large(e):
    return jsonify({"ok": False, "error": "Request too large (1MB limit)"}), 413


# Handle stale-CSRF gracefully: rather than the default ugly "Bad Request"
# from Flask-WTF, redirect users back to the page they came from so their
# browser picks up a fresh token. Without this, anyone with a cached old
# form is hard-stuck.
try:
    from flask_wtf.csrf import CSRFError as _CSRFError
except Exception:
    _CSRFError = None

if _CSRFError is not None:
    @app.errorhandler(_CSRFError)
    def _csrf_error(e):
        # JSON for API/admin routes, HTML redirect for normal forms.
        if request.path.startswith(("/admin/", "/api/")) or \
           request.headers.get("Accept", "").startswith("application/json") or \
           request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Session expired — please refresh and try again."}), 400
        # For login/signup/etc, send them back to the same page with a fresh
        # token. Only trust a same-host referrer — anything else could bounce
        # the user to an attacker-chosen site.
        from urllib.parse import urlparse
        target = request.path
        if request.referrer:
            ref = urlparse(request.referrer)
            if ref.netloc == request.host:
                target = request.referrer
        return redirect(target)


@app.template_filter("fmt_time")
def fmt_time(t: str) -> str:
    """Convert '17:00' → '5:00 PM'."""
    if not t:
        return ""
    try:
        return datetime.strptime(t, "%H:%M").strftime("%-I:%M %p")
    except ValueError:
        return t


@app.template_filter("fmt_date")
def fmt_date(d: str) -> str:
    """Convert '2026-05-17' → 'Sunday, May 17, 2026'."""
    if not d:
        return ""
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    except ValueError:
        return d

# Filesystem sessions (no cookie size limit)
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = str(DATA_DIR / ".flask_sessions")
app.config["SESSION_PERMANENT"] = False
# Lifetime for sessions that opt in via session.permanent = True ("Keep me
# signed in"). Set once here — mutating it per-request applied one user's
# choice to every session app-wide.
app.permanent_session_lifetime = timedelta(days=30)
Session(app)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def current_user() -> dict | None:
    uid = session.get("user_id")
    if not uid:
        return None
    return get_user_by_id(uid)


def gid() -> str | None:
    """Return the current user's group_id from the session."""
    return session.get("group_id")


def _group_tz(group_id: str) -> ZoneInfo:
    return ZoneInfo(load_config(group_id).get("timezone", "America/New_York"))


def _today_iso(group_id: str) -> str:
    """Today's date in the group's timezone. The server clock is UTC, so a
    bare date.today() drifts one day ahead of US timezones every evening —
    making today's trip vanish from the dashboard/bulletin after ~7-8pm."""
    return datetime.now(_group_tz(group_id)).strftime("%Y-%m-%d")


# ── Rate limiting ─────────────────────────────────────────────────────────────
# Simple in-memory limiter: enough for the small-scale Railway deployment.
# Tracks (key, action) → list of timestamps within the window. NOT shared across
# workers; with the scheduler lock most apps run single-worker anyway. For
# multi-worker, swap in a redis-backed bucket later.
import time as _time
import threading as _threading
_rate_state: dict = {}
_rate_lock = _threading.Lock()


def _rate_limited(key: str, max_hits: int, window_seconds: int) -> bool:
    """Returns True if `key` has already hit `max_hits` in `window_seconds`.
    Otherwise records the hit and returns False."""
    now = _time.time()
    cutoff = now - window_seconds
    with _rate_lock:
        bucket = _rate_state.get(key, [])
        bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= max_hits:
            _rate_state[key] = bucket
            return True
        bucket.append(now)
        _rate_state[key] = bucket
        # Opportunistic cleanup so the dict doesn't grow unbounded.
        if len(_rate_state) > 5000:
            for k in list(_rate_state.keys()):
                _rate_state[k] = [t for t in _rate_state[k] if t > cutoff]
                if not _rate_state[k]:
                    del _rate_state[k]
        return False


def _client_ip() -> str:
    """Best-effort IP behind Railway's proxy."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user or user.get("role") != "admin":
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


def _next_trip_info(group_id: str) -> dict:
    """Return date, driver_id and driver_name for the next upcoming scheduled trip.
    Falls back to config/rotation if no schedule entries exist."""
    today_str = _today_iso(group_id)
    schedule = load_schedule(group_id)
    upcoming = sorted(
        [t for t in schedule if t.get("date", "") >= today_str],
        key=lambda t: (t["date"], t.get("arrival_time", "")),
    )
    if upcoming:
        t = upcoming[0]
        driver_id   = t.get("driver_family_id") or ""
        driver_name = t.get("driver_name") or ""
        return {"date": t["date"], "driver_id": driver_id, "driver_name": driver_name, "trip": t}

    # Fallback: rotation + config date
    rotation_data = _load_rotation(group_id)
    order = rotation_data.get("order", [])
    idx   = rotation_data.get("current_index", 0)
    driver_id = order[idx] if order else ""
    try:
        driver_name = get_family(driver_id, group_id).name if driver_id else ""
    except Exception:
        driver_name = ""
    trip_date = arrival_time(group_id).strftime("%Y-%m-%d")
    return {"date": trip_date, "driver_id": driver_id, "driver_name": driver_name, "trip": None}


# ── Utility ───────────────────────────────────────────────────────────────────

def gcal_url(trip: dict, group_id: str) -> str:
    """Build a Google Calendar 'add event' URL for the outbound leg."""
    tz = ZoneInfo(load_config(group_id).get("timezone", "America/New_York"))
    arrival = datetime.strptime(f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)

    start = None
    route_cache = load_route_cache(group_id)
    if route_cache and route_cache.get("date") == trip["date"]:
        try:
            depart_str = route_cache["depart_at"]
            depart_naive = datetime.strptime(f"{trip['date']} {depart_str}", "%Y-%m-%d %I:%M %p")
            start = depart_naive.replace(tzinfo=tz)
        except (ValueError, KeyError):
            pass

    if start is None:
        cfg = load_config(group_id)
        start = arrival - timedelta(minutes=cfg.get("buffer_minutes", 10))

    pickup_start = start.strftime("%-I:%M %p")
    fmt = "%Y%m%dT%H%M%S"
    group_name = get_group_name(group_id)
    title = quote_plus(f"{group_name} — {trip['destination_name']} (There)")
    details = quote_plus(
        f"Driver: {trip['driver_name']}\n"
        f"Pickup starts: {pickup_start}\n"
        f"Arrive by: {arrival.strftime('%-I:%M %p')}"
    )
    # Location must be a clean address only — Google Calendar autocompletes
    # this against Google Maps, so any non-address text (like our group name
    # in parens) makes it fall back to weird-looking suggestions.
    location = quote_plus((trip.get("destination_address") or "").strip())
    dates = f"{start.strftime(fmt)}/{arrival.strftime(fmt)}"
    return f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={title}&dates={dates}&details={details}&location={location}"


def gcal_url_return(trip: dict, group_id: str) -> str:
    """Build a Google Calendar 'add event' URL for the return leg."""
    if not trip.get("return_time"):
        return ""
    tz = ZoneInfo(load_config(group_id).get("timezone", "America/New_York"))
    fmt = "%Y%m%dT%H%M%S"
    cfg = load_config(group_id)
    pickup = datetime.strptime(f"{trip['date']} {trip['return_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    arrive_home = pickup + timedelta(minutes=cfg.get("buffer_minutes", 10) + 20)
    driver = trip.get("return_driver_name") or trip.get("driver_name", "")
    group_name = get_group_name(group_id)
    title = quote_plus(f"{group_name} — {trip['destination_name']} (Return)")
    details = quote_plus(
        f"Driver: {driver}\n"
        f"Pickup from {trip['destination_name']}: {pickup.strftime('%-I:%M %p')}"
    )
    # Clean address only — see note in gcal_url().
    location = quote_plus((trip.get("destination_address") or "").strip())
    dates = f"{pickup.strftime(fmt)}/{arrive_home.strftime(fmt)}"
    return f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={title}&dates={dates}&details={details}&location={location}"


# ── Auth routes ───────────────────────────────────────────────────────────────

@csrf.exempt
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    error = None
    email = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        # Rate limit: 10 attempts per 5 minutes per IP, 8 per email.
        ip = _client_ip()
        if _rate_limited(f"login:ip:{ip}", max_hits=10, window_seconds=300) or \
           _rate_limited(f"login:email:{email.lower()}", max_hits=8, window_seconds=300):
            error = "Too many login attempts. Please wait a few minutes and try again."
            logger.warning("Login rate-limited for email=%s ip=%s", email[:3] + "***", ip)
            return render_template("login.html", error=error, email=email), 429

        # Optional Supabase Auth path — gated by USE_SUPABASE_AUTH=1 env var.
        # When enabled, password verification happens against Supabase Auth;
        # we then look up the internal user record by supabase_uid or email.
        use_supabase = os.environ.get("USE_SUPABASE_AUTH", "").strip() == "1"
        login_ok = False
        user = None
        if use_supabase:
            from auth_supabase import signin_with_password, find_or_link_internal_user
            result = signin_with_password(email, password)
            if result["ok"]:
                user = find_or_link_internal_user(result["supabase_uid"], email)
                login_ok = user is not None
            if not login_ok and not result["ok"] and "configured" in (result.get("error") or "").lower():
                # Supabase wasn't actually wired up — fall through to legacy.
                use_supabase = False

        if not use_supabase:
            user = get_user_by_email(email)
            login_ok = bool(user and verify_password(password, user.get("password_hash", "")))

        if login_ok and user:
            remember = request.form.get("remember") == "on"
            # Rotate the session on privilege change (anti-fixation).
            session.clear()
            session["user_id"] = user["id"]
            session["group_id"] = user.get("group_id", "")
            if remember:
                # "Remember me" → 30-day session (lifetime set at startup).
                session.permanent = True
            try:
                purge_old_tokens()
            except Exception as e:
                logger.error("Token purge failed: %s", e)
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid email or password."
            # Detailed (but redacted) diagnostic so we can tell email-not-found
            # from password-mismatch in the logs without leaking secrets.
            email_masked = (email[:3] + "***") if email else "(empty)"
            if not user:
                logger.warning("Login fail: NO USER for email=%s ip=%s", email_masked, ip)
            else:
                hash_len = len(user.get("password_hash", "") or "")
                logger.warning("Login fail: BAD PW for email=%s ip=%s (user_id=%s, hash_len=%d, pw_len=%d)",
                               email_masked, ip, user.get("id", "?"), hash_len, len(password))

    return render_template("login.html", error=error, email=email)


# ── Magic-link sign-in (Supabase) ─────────────────────────────────────────────
# Additive to the existing password login. The user enters their email, we ask
# Supabase to mail them a one-time link; clicking it lands on /auth/callback
# where we set the Flask session.

@csrf.exempt
@app.route("/auth/magic-link", methods=["POST"])
def auth_magic_link_send():
    """Send a magic-link email via Supabase. Rate-limited per IP + email."""
    from auth_supabase import send_magic_link
    email = (request.form.get("email", "") or "").strip().lower()
    ip = _client_ip()

    if _rate_limited(f"magic:ip:{ip}", max_hits=5, window_seconds=300) or \
       _rate_limited(f"magic:email:{email}", max_hits=3, window_seconds=300):
        return render_template("login.html",
                               error="Too many requests. Please wait a few minutes.",
                               email=email), 429

    redirect_url = url_for("auth_callback", _external=True)
    result = send_magic_link(email, redirect_url)
    if not result["ok"]:
        return render_template("login.html",
                               error=result["error"] or "Could not send the sign-in link.",
                               email=email), 400

    return render_template("login.html",
                           email=email,
                           magic_sent=True)


@app.route("/auth/callback")
def auth_callback():
    """Receive the Supabase magic-link callback. Exchange the code for a
    session, find or attach the internal user record, and log them in."""
    from auth_supabase import exchange_code_for_session, find_or_link_internal_user

    code = request.args.get("code", "")
    if not code:
        return render_template("login.html",
                               error="Sign-in link is missing or expired. Please request a new one."), 400

    supa_user = exchange_code_for_session(code)
    if not supa_user:
        return render_template("login.html",
                               error="Sign-in link is invalid or expired. Please request a new one."), 400

    internal = find_or_link_internal_user(supa_user["id"], supa_user["email"])
    if not internal:
        # First-time magic-link signer with no internal account yet.
        # Stash the verified email so /create-group can pre-fill it and
        # the user finishes signup without re-typing.
        session["pending_supabase_uid"] = supa_user["id"]
        session["pending_email"] = supa_user["email"]
        return redirect(url_for("create_group_route"))

    # Existing user → set the session and we're in.
    session.clear()
    session["user_id"] = internal["id"]
    session["group_id"] = internal.get("group_id", "")
    session.permanent = True
    logger.info("Magic-link login OK for user_id=%s", internal["id"])
    return redirect(url_for("dashboard"))


@app.route("/emergency-login")
def emergency_login():
    """
    Recovery: if EMERGENCY_LOGIN_TOKEN is set in Railway env vars, anyone who
    visits /emergency-login?token=<that-value> gets logged in as the user whose
    email matches EMERGENCY_RESET_EMAIL (or the first user in the system if
    that's not set). Skips bcrypt entirely — pure session bypass.

    Use ONLY when normal login is broken. Delete the env var right after.
    """
    import hmac as _hmac
    expected = os.environ.get("EMERGENCY_LOGIN_TOKEN", "")
    if not expected:
        return "Emergency login is disabled. Set EMERGENCY_LOGIN_TOKEN in Railway.", 403
    if not _hmac.compare_digest(request.args.get("token", ""), expected):
        logger.warning("Emergency login: bad token from %s", _client_ip())
        return "Bad token.", 403

    from auth import _load_users, get_user_by_email
    target_email = os.environ.get("EMERGENCY_RESET_EMAIL", "").strip().lower()
    user = get_user_by_email(target_email) if target_email else None
    if not user:
        users = _load_users()
        if not users:
            return ("No users exist on this app yet. "
                    "Go to <a href='/create-group'>/create-group</a> to make an account."), 200
        user = users[0]  # fall back to first user

    session.clear()
    session["user_id"] = user["id"]
    session["group_id"] = user.get("group_id", "")
    session.permanent = True
    logger.warning("EMERGENCY LOGIN granted: user_id=%s email=%s",
                   user["id"], user.get("email", "")[:3] + "***")
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/about")
def about():
    """Public landing/marketing page. No login required."""
    return render_template("about.html")


# ── Kid bulletin (public, token-based) ────────────────────────────────────────

@app.route("/display/<token>")
def kid_display(token):
    """Public read-only bulletin for the kitchen iPad. Identified by an
    opaque per-group token in the URL — no login, no cookies needed.
    Anyone with the URL can view; the admin can rotate the token to revoke."""
    group = find_group_by_display_token(token)
    if not group:
        return "Display URL not recognized. Ask your carpool admin for a current link.", 404
    group_id = group["id"]

    cfg = load_config(group_id)
    tz_name = cfg.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)

    # Determine the next upcoming trip (today preferred).
    today_iso = _today_iso(group_id)
    schedule_data = load_schedule(group_id)
    upcoming_all = sorted(
        [t for t in schedule_data if t.get("date", "") >= today_iso],
        key=lambda t: (t["date"], t.get("arrival_time", "")),
    )
    trip = upcoming_all[0] if upcoming_all else None
    is_today = bool(trip and trip["date"] == today_iso)

    # Resolve driver/destination from trip + config + rotation fallback.
    rotation_data = _load_rotation(group_id)
    order = rotation_data.get("order", [])
    cur_idx = rotation_data.get("current_index", 0)
    rot_driver_id = order[cur_idx] if order else None

    driver_id = (trip.get("driver_family_id") if trip else "") or rot_driver_id
    driver_name = ""
    if driver_id:
        try:
            driver_name = get_family(driver_id, group_id).name
        except Exception:
            driver_name = trip.get("driver_name", "") if trip else ""
    driver_name = driver_name or (trip.get("driver_name") if trip else "TBD") or "TBD"

    dest_name = (trip.get("destination_name") if trip else "") or cfg.get("destination_name", "the destination")
    arrive_by_str = ""
    arrive_by_iso = ""
    if trip:
        try:
            arrive_dt = datetime.strptime(
                f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=tz)
            arrive_by_str = arrive_dt.strftime("%-I:%M %p")
            arrive_by_iso = arrive_dt.isoformat()
        except Exception:
            arrive_by_str = trip.get("arrival_time", "")

    return_time_str = ""
    if trip and trip.get("return_time"):
        try:
            rt = datetime.strptime(trip["return_time"], "%H:%M").strftime("%-I:%M %p")
            return_time_str = rt
        except Exception:
            return_time_str = trip["return_time"]
    return_driver_name = ""
    if trip:
        rdid = trip.get("return_driver_family_id")
        if rdid:
            try:
                return_driver_name = get_family(rdid, group_id).name
            except Exception:
                return_driver_name = trip.get("return_driver_name", "")
        else:
            return_driver_name = trip.get("return_driver_name", "")

    # Today's pickup list — prefer the route cache (real pickup times),
    # otherwise fall back to "everyone except driver/absent".
    cache = load_route_cache(group_id) if trip else None
    absent_set = set(get_absences(trip["date"], group_id)) if trip else set()
    pickups = []
    if cache and trip and cache.get("date") == trip["date"]:
        for entry in cache.get("schedule", []):
            fid = entry.get("family_id", "")
            is_absent = fid in absent_set
            # Convert pickup_time ("4:42 PM") + trip date into an ISO timestamp
            # the page's JS can use for a live countdown.
            iso = ""
            pt = entry.get("pickup_time", "")
            if pt:
                try:
                    dt = datetime.strptime(f"{trip['date']} {pt}", "%Y-%m-%d %I:%M %p").replace(tzinfo=tz)
                    iso = dt.isoformat()
                except Exception:
                    iso = ""
            pickups.append({
                "name": entry.get("label") or "Family",
                "pickup_time": pt,
                "pickup_iso": iso,
                "absent": is_absent,
            })
    elif trip:
        for fid in get_all_family_ids(group_id):
            if fid == driver_id:
                continue
            try:
                fam = get_family(fid, group_id)
                pickups.append({
                    "name": fam.name,
                    "pickup_time": "",
                    "pickup_iso": "",
                    "absent": fid in absent_set,
                })
            except Exception:
                continue

    # Coming-up list (next 5 trips after the current one).
    upcoming_view = []
    for t in upcoming_all[1:6]:
        try:
            ddate = datetime.strptime(t["date"], "%Y-%m-%d")
            d_friendly = ddate.strftime("%a, %b %-d")
        except Exception:
            d_friendly = t["date"]
        d_name = t.get("driver_name", "")
        if not d_name and t.get("driver_family_id"):
            try:
                d_name = get_family(t["driver_family_id"], group_id).name
            except Exception:
                pass
        upcoming_view.append({
            "date_friendly": d_friendly,
            "driver": d_name or "TBD",
        })

    live_location = get_location(group_id)

    next_trip_date_friendly = ""
    if trip:
        try:
            next_trip_date_friendly = datetime.strptime(trip["date"], "%Y-%m-%d").strftime("%A, %b %-d")
        except Exception:
            next_trip_date_friendly = trip["date"]

    return render_template(
        "kid_bulletin.html",
        group_name=get_group_name(group_id) or "Carpool",
        next_trip={
            "is_today": is_today,
            "date": next_trip_date_friendly,
            "driver": driver_name,
            "destination": dest_name,
            "arrive_by": arrive_by_str or "soon",
            "arrive_by_iso": arrive_by_iso,
            "return_time": return_time_str,
            "return_driver": return_driver_name,
        },
        pickups=pickups,
        upcoming=upcoming_view,
        live_location=live_location,
    )


@app.route("/admin/display-url")
@login_required
@admin_required
def admin_display_url():
    """Return the kid-bulletin URL for the admin's group (generating a token
    on first call). The URL is shareable to any iPad/screen without login."""
    group_id = gid()
    token = get_or_create_display_token(group_id)
    url = url_for("kid_display", token=token, _external=True)
    return jsonify({"url": url})


@app.route("/admin/display-url/regenerate", methods=["POST"])
@login_required
@admin_required
def admin_display_url_regenerate():
    """Rotate the display token — any tablets using the old URL will need
    to be re-pointed at the new one."""
    group_id = gid()
    token = regenerate_display_token(group_id)
    url = url_for("kid_display", token=token, _external=True)
    return jsonify({"url": url})


@app.route("/health")
def health():
    data_dir_env = os.environ.get("DATA_DIR", "NOT SET")
    is_ephemeral = str(DATA_DIR) == str(CODE_DIR)
    try:
        test = DATA_DIR / ".write_test"
        test.write_text("ok")
        test.unlink()
        writable = True
    except Exception:
        writable = False
    from groups import list_groups
    groups = list_groups()
    status = "⚠️ EPHEMERAL" if is_ephemeral else "✅ PERSISTENT"

    # Supabase migration status
    from supabase_client import health_check as _supa_health
    supa = _supa_health()
    if not supa["configured"]:
        supa_line = "❌ NOT CONFIGURED — missing env: " + ", ".join(supa.get("missing_env", []))
        if not supa.get("sdk_installed"):
            supa_line += " (supabase-py not installed)"
    elif supa["ok"]:
        supa_line = f"✅ CONNECTED ({supa.get('url', '')})"
    else:
        supa_line = f"⚠️  CONFIGURED but connection failed: {supa.get('error', 'unknown')}"

    return f"""<pre>
CarpoolSync Health Check
========================
DATA_DIR env : {data_dir_env}
DATA_DIR path: {DATA_DIR}
Writable     : {writable}
Storage      : {status}
Groups       : {len(groups)} ({', '.join(g['id'] for g in groups) or 'none'})

Supabase     : {supa_line}

{"⚠️  WARNING: DATA_DIR is the code directory." if is_ephemeral else "✅  Data will survive redeploys."}
{"Set DATA_DIR=/data and mount a Railway volume at /data." if is_ephemeral else ""}
</pre>"""


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    sent = False
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ip = _client_ip()
        # Throttle: 3 reset requests per IP per 10 min, 2 per email per 10 min,
        # plus a hard daily cap so nobody runs up Twilio cost or spams a victim.
        if _rate_limited(f"reset:ip:{ip}", max_hits=3, window_seconds=600) or \
           _rate_limited(f"reset:email:{email}", max_hits=2, window_seconds=600) or \
           _rate_limited(f"reset:email_day:{email}", max_hits=5, window_seconds=86400):
            logger.warning("Password reset rate-limited for email=%s ip=%s", email[:3] + "***", ip)
            # Still show success — we don't want to reveal whether limiting kicked in.
            return render_template("forgot_password.html", sent=True, error=None)

        user = get_user_by_email(email)
        if user and user.get("phone"):
            token = generate_reset_token(user["id"])
            reset_url = url_for("reset_password", token=token, _external=True)
            try:
                send_sms(
                    to_phone=user["phone"],
                    message=f"Reset your CarpoolSync password:\n{reset_url}\n\nThis link expires in 1 hour.",
                )
            except Exception as e:
                logger.error("Password reset SMS failed: %s", e)
        # Always show "sent" to avoid revealing whether an email exists
        sent = True
    return render_template("forgot_password.html", sent=sent, error=error)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    reset = verify_reset_token(token)
    if not reset:
        return render_template("forgot_password.html", sent=False,
                               error="This reset link is invalid or has expired.")
    error = None
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords don't match."
        else:
            update_password(reset["user_id"], password)
            mark_reset_used(token)
            return render_template("login.html", error=None,
                                   success="Password updated! Please sign in.")
    return render_template("reset_password.html", token=token, error=error)


@app.route("/create-group", methods=["GET", "POST"])
def create_group_route():
    """New admin flow: create a brand-new carpool group."""
    # If already logged in AND already has a group → go to dashboard.
    # If logged in but no group → fall through so they can create one.
    # (Redirecting all logged-in users causes an infinite loop with dashboard.)
    if session.get("user_id") and session.get("group_id"):
        return redirect(url_for("dashboard"))

    error = None
    form = {}
    # A first-time magic-link signer lands here with a verified email stashed
    # in the session (see auth_callback) — pre-fill it so they don't retype.
    if request.method == "GET" and session.get("pending_email"):
        form = {"email": session["pending_email"]}

    if request.method == "POST":
        form = {
            "group_name":  request.form.get("group_name", "").strip(),
            "name":        request.form.get("name", "").strip(),
            "family_name": request.form.get("family_name", "").strip(),
            "email":       request.form.get("email", "").strip(),
            "phone":       request.form.get("phone", "").strip(),
            "child_name":  request.form.get("child_name", "").strip(),
            "address":     request.form.get("address", "").strip(),
        }
        password = request.form.get("password", "").strip()

        # Normalize phone to E.164
        digits = re.sub(r'\D', '', form["phone"])
        if digits:
            form["phone"] = f"+1{digits}" if len(digits) == 10 else f"+{digits}"

        if not form["group_name"]:
            error = "Please name your carpool group."
        elif not form["name"]:
            error = "Please enter your name."
        elif not form["family_name"]:
            error = "Please enter your family name."
        elif not form["email"]:
            error = "Please enter your email."
        elif not form["address"]:
            error = "Please enter your home address — it's needed for route planning."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif get_user_by_email(form["email"]):
            error = "An account with that email already exists."
        else:
            # If Supabase Auth is enabled, create the Supabase user FIRST.
            # If that fails (duplicate email, password too weak, Supabase down),
            # bail out before creating any local records.
            supabase_uid = None
            if os.environ.get("USE_SUPABASE_AUTH", "").strip() == "1":
                pending_uid = session.get("pending_supabase_uid")
                pending_email = (session.get("pending_email") or "").strip().lower()
                if pending_uid and pending_email == form["email"].strip().lower():
                    # Magic-link signer: their Supabase user already exists —
                    # attach the chosen password instead of creating a
                    # duplicate (which would fail with 'already exists').
                    from auth_supabase import admin_set_password
                    s = admin_set_password(pending_uid, password)
                    if not s["ok"]:
                        error = s["error"] or "Could not create your account. Please try again."
                    else:
                        supabase_uid = pending_uid
                else:
                    from auth_supabase import signup_with_password
                    s = signup_with_password(form["email"], password)
                    if not s["ok"]:
                        error = s["error"] or "Could not create your account. Please try again."
                    else:
                        supabase_uid = s["supabase_uid"]

            if error:
                # Re-render the form with the error and the prior input.
                return render_template("create_group.html", error=error, form=form)

            group = None
            try:
                group = _create_group(form["group_name"])
                # Create the admin's family and add to rotation
                family = add_family(
                    name=form["family_name"],
                    address=form["address"],
                    phone=form["phone"],
                    children=[form["child_name"]] if form["child_name"] else [],
                    group_id=group["id"],
                )
                add_to_rotation(family["id"], group["id"])
                user = create_user(
                    phone=form["phone"],
                    name=form["name"],
                    email=form["email"],
                    password=password,
                    role="admin",
                    family_id=family["id"],
                    child_name=form["child_name"],
                    address=form["address"],
                    group_id=group["id"],
                )
            except Exception:
                # Roll back the half-created group so a mid-flow failure
                # doesn't leave an orphan group with no users.
                logger.exception("Create-group failed mid-flow; rolling back")
                if group:
                    try:
                        import shutil as _shutil
                        from groups import _load_groups, _save_groups
                        _save_groups([g for g in _load_groups() if g["id"] != group["id"]])
                        gdir = DATA_DIR / "groups" / group["id"]
                        if gdir.exists():
                            _shutil.rmtree(gdir)
                    except Exception as e:
                        logger.error("Create-group rollback failed: %s", e)
                return render_template(
                    "create_group.html", form=form,
                    error="Something went wrong creating your group. Nothing was saved — please try again.",
                )

            # If we created a Supabase Auth user above, link its UID into the
            # local user record so the next login can find this user even if
            # the password hash on disk gets corrupted/wiped.
            if supabase_uid:
                from auth import _load_users, _save_users
                _users = _load_users()
                for _u in _users:
                    if _u.get("id") == user["id"]:
                        _u["supabase_uid"] = supabase_uid
                        break
                _save_users(_users)
                user["supabase_uid"] = supabase_uid
                logger.info("Linked supabase_uid=%s to internal user_id=%s", supabase_uid, user["id"])

            # CRITICAL: verify the bcrypt hash actually works when read back
            # from disk. Catches atomic-write or volume-sync issues that
            # would otherwise produce a "ghost" account you can't log into.
            from auth import verify_password as _verify
            reloaded = get_user_by_email(form["email"])
            if not reloaded or not _verify(password, reloaded.get("password_hash", "")):
                logger.error("CRITICAL: signup persistence check failed for %s "
                             "(user_id=%s). Account may not be loginable.",
                             form["email"][:3] + "***", user.get("id"))
                # Don't block the signup — log them in via the session we
                # already set. Show a clear warning on the welcome page.
                session["signup_persistence_warning"] = True
            else:
                logger.info("Signup OK + verified for %s (user_id=%s)",
                            form["email"][:3] + "***", user.get("id"))

            session.pop("pending_supabase_uid", None)
            session.pop("pending_email", None)
            session["user_id"] = user["id"]
            session["group_id"] = group["id"]
            session["just_created_group"] = group["name"]
            return redirect(url_for("welcome"))

    return render_template("create_group.html", error=error, form=form)


@app.route("/welcome")
@login_required
def welcome():
    """One-shot celebratory landing page shown right after creating a group."""
    group_name = session.pop("just_created_group", None)
    persistence_warning = session.pop("signup_persistence_warning", False)
    if not group_name:
        return redirect(url_for("dashboard"))
    user = current_user()
    return render_template(
        "welcome.html",
        group_name=group_name,
        user_email=user.get("email", "") if user else "",
        persistence_warning=persistence_warning,
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    token = request.args.get("token") or request.form.get("token", "")
    invite = verify_invite_token(token)

    if not invite:
        return render_template("login.html", error="This invite link is invalid or has already been used.")

    error = None
    form = {}

    if request.method == "POST":
        form = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip(),
            "family_name": request.form.get("family_name", "").strip(),
            "child_name": request.form.get("child_name", "").strip(),
            "address": request.form.get("address", "").strip(),
        }
        password = request.form.get("password", "").strip()
        invite_group_id = invite.get("group_id", "")

        if not form["name"]:
            error = "Please enter your name."
        elif not form["family_name"]:
            error = "Please enter your family name."
        elif not form["email"]:
            error = "Please enter your email."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif get_user_by_email(form["email"]):
            error = "An account with that email already exists."
        elif not form["address"]:
            error = "Please enter your home address — it's needed for route planning."
        else:
            existing_family_id = invite.get("family_id", "")
            if existing_family_id:
                # Joining an existing family — no new rotation slot
                assigned_family_id = existing_family_id
            else:
                # New family — create entry and add to rotation
                if not form["family_name"]:
                    error = "Please enter your family name."
                else:
                    family = add_family(
                        name=form["family_name"],
                        address=form["address"],
                        phone=invite["phone"],
                        children=[form["child_name"]] if form["child_name"] else [],
                        group_id=invite_group_id,
                    )
                    add_to_rotation(family["id"], invite_group_id)
                    assigned_family_id = family["id"]

            if not error:
                user = create_user(
                    phone=invite["phone"],
                    name=form["name"],
                    email=form["email"],
                    password=password,
                    role="parent",
                    family_id=assigned_family_id,
                    child_name=form["child_name"],
                    address=form["address"],
                    group_id=invite_group_id,
                )
                mark_invite_used(token)
                session["user_id"] = user["id"]
                session["group_id"] = invite_group_id
                return redirect(url_for("dashboard"))

    # Pre-fill family name suggestion from invite if available
    suggested_family_name = invite.get("family_name", "")
    return render_template(
        "signup.html",
        token=token,
        phone=invite["phone"],
        error=error,
        form=form,
        suggested_family_name=suggested_family_name,
        sandbox_number=SANDBOX_NUMBER,
        sandbox_keyword=SANDBOX_KEYWORD,
    )


# ── Invite route (admin only) ─────────────────────────────────────────────────

@app.route("/invite", methods=["POST"])
@login_required
@admin_required
def invite():
    group_id = gid()
    group_name = get_group_name(group_id)
    if not group_name or group_name == "Carpool":
        return redirect(url_for("dashboard"))

    phone = request.form.get("phone", "").strip()
    if not phone:
        session["invite_status"] = "Please enter a phone number to invite."
        return redirect(url_for("dashboard"))

    # Normalize to E.164 format
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        phone = f"+1{digits}"          # assume US
    elif len(digits) == 11 and digits.startswith('1'):
        phone = f"+{digits}"
    else:
        session["invite_status"] = (
            f"“{phone}” doesn't look like a valid US phone number — "
            "no invite was sent. Enter a 10-digit number."
        )
        return redirect(url_for("dashboard"))

    family_id = request.form.get("family_id", "").strip()
    family_name = ""
    if family_id:
        try:
            family_name = get_family(family_id, group_id).name
        except ValueError:
            family_id = ""

    token = generate_invite_token(phone, group_id=group_id, family_id=family_id, family_name=family_name)
    signup_url = url_for("signup", token=token, _external=True)
    message = (
        f"You've been invited to join {group_name}!\n\n"
        f"Click the link below to create your account:\n{signup_url}"
    )
    whatsapp_ok = True
    try:
        send_sms(to_phone=phone, message=message)
        logger.info("Invite WhatsApp sent to %s", phone)
    except Exception as e:
        logger.error("Invite WhatsApp failed for %s: %s", phone, e)
        whatsapp_ok = False

    # Store invite result in session flash instead of URL params
    if whatsapp_ok:
        session["invite_status"] = f"Invite sent to {phone} via WhatsApp."
    else:
        session["invite_status"] = f"WhatsApp failed. Share this link manually with {phone}: {signup_url}"
    session["invite_link"] = signup_url

    return redirect(url_for("dashboard"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    group_id = gid()
    if not group_id:
        # Session missing group_id — try to recover from user record
        group_id = user.get("group_id", "") if user else ""
        if group_id:
            session["group_id"] = group_id
        else:
            return redirect(url_for("create_group_route"))

    cfg = load_config(group_id)
    trip_time = arrival_time(group_id)
    rotation_data = _load_rotation(group_id)
    order = rotation_data.get("order", get_all_family_ids(group_id))
    current_index = rotation_data.get("current_index", 0)
    next_driver_id = order[current_index] if order else None

    rotation = []
    for fid in order:
        try:
            family = get_family(fid, group_id)
        except ValueError:
            # Stale rotation entry (family record deleted) — skip rather than
            # taking down the whole dashboard.
            continue
        rotation.append({"name": family.name, "id": fid, "is_next": fid == next_driver_id})

    try:
        next_driver_name = get_family(next_driver_id, group_id).name if next_driver_id else "—"
    except ValueError:
        next_driver_name = "—"
    default_dest = get_destination(get_destination_id(group_id))
    dest_name = cfg.get("destination_name") or default_dest.name
    if not cfg.get("destination_name"):
        cfg["destination_name"] = default_dest.name
    if not cfg.get("destination_address"):
        cfg["destination_address"] = default_dest.street

    stats = get_stats(group_id)
    max_minutes = max((s["minutes"] for s in stats.values()), default=1) or 1
    for s in stats.values():
        s["hours"] = s["minutes"] // 60
        s["mins"] = s["minutes"] % 60
        s["bar_pct"] = round(s["minutes"] / max_minutes * 100)

    raw_trips = load_trips(group_id)
    history = []
    for t in reversed(raw_trips):
        mins = t.get("minutes", 0)
        history.append({
            "date": t["date"],
            "driver": t["driver_name"],
            "duration": f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m",
            "pickups": len(t.get("pickups", [])),
        })

    schedule = load_schedule(group_id)
    for trip in schedule:
        trip["gcal_url"] = gcal_url(trip, group_id)
        trip["gcal_url_return"] = gcal_url_return(trip, group_id)

    families = [{"id": fid, "name": get_family(fid, group_id).name} for fid in get_all_family_ids(group_id)]
    raw_karma = {k["family_id"]: k for k in get_karma(group_id)}
    karma = []
    for fid in get_all_family_ids(group_id):
        if fid in raw_karma:
            karma.append(raw_karma[fid])
        else:
            karma.append({
                "family_id": fid,
                "name": get_family(fid, group_id).name,
                "requested": 0,
                "covered": 0,
                "score": 0,
            })

    # ── Next trip: prefer the schedule over rotation/config ───────────────────
    today_str = _today_iso(group_id)
    upcoming = sorted(
        [t for t in schedule if t["date"] >= today_str],
        key=lambda t: (t["date"], t.get("arrival_time", "")),
    )

    if upcoming:
        st = upcoming[0]
        try:
            st_date_fmt = datetime.strptime(st["date"], "%Y-%m-%d").strftime("%A, %B %d, %Y")
            st_arrive = datetime.strptime(st["arrival_time"], "%H:%M").strftime("%-I:%M %p") if st.get("arrival_time") else ""
        except ValueError:
            st_date_fmt = st["date"]
            st_arrive = st.get("arrival_time", "")
        next_trip_data = {
            "date": st_date_fmt,
            "driver": st.get("driver_name") or "—",
            "destination": st.get("destination_name") or dest_name,
            "arrive_by": st_arrive,
            "return_time": st.get("return_time", ""),
            "return_driver": st.get("return_driver_name", ""),
        }
        # Use the scheduled trip's driver for "who's driving today" logic
        active_driver_id = st.get("driver_family_id") or next_driver_id
        trip_date = st["date"]
    else:
        # No scheduled trips — fall back to rotation + config
        next_trip_data = {
            "date": trip_time.strftime("%A, %B %d, %Y"),
            "driver": next_driver_name,
            "destination": dest_name,
            "arrive_by": trip_time.strftime("%-I:%M %p"),
            "return_time": cfg.get("return_time", ""),
            "return_driver": cfg.get("return_driver_name", ""),
        }
        active_driver_id = next_driver_id
        trip_date = trip_time.strftime("%Y-%m-%d")

    absences = get_absences(trip_date, group_id)
    pickup_families = []
    for fid in get_all_family_ids(group_id):
        if fid == active_driver_id:
            continue
        family = get_family(fid, group_id)
        pickup_families.append({
            "id": fid,
            "name": family.name,
            "absent": fid in absences,
        })

    invite_status = session.pop("invite_status", None)
    invite_link = session.pop("invite_link", None)
    settings_error = session.pop("settings_error", None)

    group_name = get_group_name(group_id)

    # URL to the cookieless kid-bulletin display (no login required).
    # Used for "Live location is being shared on the Kids Bulletin" links so
    # parents/kids on other devices don't get bounced to the login page.
    try:
        kid_bulletin_url = url_for("kid_display", token=get_or_create_display_token(group_id))
    except Exception:
        kid_bulletin_url = ""

    return render_template(
        "dashboard.html",
        group_name=group_name,
        group_id=group_id,
        current_user=user,
        next_trip=next_trip_data,
        rotation=rotation,
        stats=stats,
        history=history,
        cfg=cfg,
        schedule=schedule,
        families=families,
        karma=karma,
        invite_status=invite_status,
        settings_error=settings_error,
        pickup_families=pickup_families,
        next_driver_id=next_driver_id,
        trip_date=trip_date,
        live_location=get_location(group_id),
        maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        assignment_mode=get_assignment_mode(group_id),
        kid_bulletin_url=kid_bulletin_url,
    )


# ── Trip config (admin only) ──────────────────────────────────────────────────

@app.route("/save-trip", methods=["POST"])
@login_required
@admin_required
def save_trip():
    group_id = gid()
    cfg = load_config(group_id)

    arrival_date        = request.form.get("arrival_date", "").strip()
    arrival_time        = request.form.get("arrival_time", "").strip()
    return_time         = request.form.get("return_time", "").strip()
    driver_family_id    = request.form.get("driver_family_id", "").strip()
    driver_name         = request.form.get("driver_name", "").strip()
    return_driver_fid   = request.form.get("return_driver_family_id", "").strip()
    return_driver_name  = request.form.get("return_driver_name", "").strip()
    destination_name    = request.form.get("destination_name", "").strip()
    destination_address = request.form.get("destination_address", "").strip()
    group_name          = request.form.get("group_name", "").strip()
    try:
        buffer_minutes = int(request.form.get("buffer_minutes") or cfg.get("buffer_minutes", 10))
    except (TypeError, ValueError):
        buffer_minutes = cfg.get("buffer_minutes", 10)

    if not arrival_date or not arrival_time:
        session["settings_error"] = "Date and arrival time are required — settings were not saved."
        return redirect(url_for("dashboard"))
    if not destination_address:
        session["settings_error"] = "Destination address is required — settings were not saved."
        return redirect(url_for("dashboard"))

    # Persist defaults to config
    cfg.update({
        "arrival_date": arrival_date,
        "arrival_time": arrival_time,
        "return_time": return_time,
        "driver_family_id": driver_family_id,
        "driver_name": driver_name,
        "return_driver_family_id": return_driver_fid,
        "return_driver_name": return_driver_name,
        "destination_name": destination_name,
        "destination_address": destination_address,
        "buffer_minutes": buffer_minutes,
    })
    if group_name:
        cfg["group_name"] = group_name
    timezone_val = request.form.get("timezone", "").strip()
    if timezone_val:
        cfg["timezone"] = timezone_val

    # Sync to schedule: update the linked trip or create a new one
    trip_fields = dict(
        date=arrival_date,
        arrival_time=arrival_time,
        return_time=return_time,
        driver_family_id=driver_family_id,
        driver_name=driver_name,
        return_driver_family_id=return_driver_fid,
        return_driver_name=return_driver_name,
        destination_name=destination_name,
        destination_address=destination_address,
    )
    linked_id = cfg.get("linked_trip_id", "")
    updated = update_trip(linked_id, group_id, **trip_fields) if linked_id else None

    # fix #4: if linked trip was deleted, look for an existing trip on the same date
    # before creating a brand-new one (prevents duplicates on every save)
    if not updated:
        existing = next(
            (t for t in load_schedule(group_id) if t["date"] == arrival_date),
            None
        )
        if existing:
            updated = update_trip(existing["id"], group_id, **trip_fields)
            cfg["linked_trip_id"] = existing["id"]

    if not updated:
        new_trip = add_trip(
            date=arrival_date,
            arrival_time=arrival_time,
            destination_name=destination_name,
            destination_address=destination_address,
            driver_family_id=driver_family_id,
            driver_name=driver_name,
            group_id=group_id,
            return_time=return_time,
            return_driver_family_id=return_driver_fid,
            return_driver_name=return_driver_name,
        )
        cfg["linked_trip_id"] = new_trip["id"]

    save_config(cfg, group_id)
    return redirect(url_for("dashboard"))


# ── Calendar ──────────────────────────────────────────────────────────────────

@app.route("/calendar.ics")
@login_required
def calendar_feed():
    """In-browser ICS download (for the logged-in user only)."""
    group_id = gid()
    ics = build_ics(group_id)
    return Response(ics, mimetype="text/calendar", headers={
        "Content-Disposition": "inline; filename=carpool.ics"
    })


def _get_or_create_calendar_token(user: dict) -> str:
    """Return the user's persistent calendar feed token, creating one if needed.
    The token is opaque, per-user, and revocable (regenerated by saving a new one)."""
    import secrets as _secrets
    from auth import _load_users, _save_users
    if user.get("calendar_token"):
        return user["calendar_token"]
    new_token = _secrets.token_urlsafe(24)
    users = _load_users()
    for u in users:
        if u["id"] == user["id"]:
            u["calendar_token"] = new_token
            user["calendar_token"] = new_token
            break
    _save_users(users)
    return new_token


@app.route("/calendar/subscribe-url")
@login_required
def calendar_subscribe_url():
    """Return a cookieless URL that Google/Apple Calendar can subscribe to."""
    user = current_user()
    token = _get_or_create_calendar_token(user)
    url = url_for("calendar_feed_public", token=token, _external=True)
    return jsonify({"url": url})


@app.route("/calendar/<token>.ics")
def calendar_feed_public(token):
    """Cookieless ICS endpoint for calendar app subscriptions. Identifies the
    user via an opaque per-user token in the URL — no session required."""
    from auth import _load_users
    if not token or len(token) < 16:
        return "Not found", 404
    user = next((u for u in _load_users() if u.get("calendar_token") == token), None)
    if not user or not user.get("group_id"):
        return "Not found", 404
    ics = build_ics(user["group_id"])
    return Response(ics, mimetype="text/calendar", headers={
        "Content-Disposition": "inline; filename=carpool.ics",
        # Calendar apps may poll frequently — let them cache for 10 minutes.
        "Cache-Control": "public, max-age=600",
    })


# ── Schedule ──────────────────────────────────────────────────────────────────

def _rotation_pairs_from(start_index: int, group_id: str) -> list[tuple[str, str]]:
    """Return (family_id, name) pairs for the rotation, starting at start_index."""
    rot = _load_rotation(group_id)
    order = rot.get("order", [])
    if not order:
        return []
    pairs = []
    for fid in order:
        try:
            pairs.append((fid, get_family(fid, group_id).name))
        except ValueError:
            continue  # stale rotation entry
    return pairs[start_index:] + pairs[:start_index]


@app.route("/schedule/add", methods=["POST"])
@login_required
@admin_required
def schedule_add():
    group_id = gid()
    data = request.json
    driver_fid = data.get("driver_family_id", "")
    driver_name = data.get("driver_name", "")

    # Auto mode: if driver not supplied, pull from rotation
    if get_assignment_mode(group_id) == "auto" and not driver_fid:
        rot = _load_rotation(group_id)
        order = rot.get("order", [])
        idx = rot.get("current_index", 0)
        if order:
            driver_fid = order[idx]
            driver_name = get_family(driver_fid, group_id).name

    trip = add_trip(
        date=data["date"],
        arrival_time=data["arrival_time"],
        return_time=data.get("return_time", ""),
        return_driver_family_id=data.get("return_driver_family_id", ""),
        return_driver_name=data.get("return_driver_name", ""),
        destination_name=data["destination_name"],
        destination_address=data["destination_address"],
        driver_family_id=driver_fid,
        driver_name=driver_name,
        group_id=group_id,
    )
    trip["gcal_url"] = gcal_url(trip, group_id)
    trip["gcal_url_return"] = gcal_url_return(trip, group_id)
    return jsonify(trip)


@app.route("/schedule/update/<trip_id>", methods=["POST"])
@login_required
@admin_required
def schedule_update(trip_id):
    group_id = gid()
    data = request.json or {}
    driverEl_id = data.get("driver_family_id", "")
    rdEl_id = data.get("return_driver_family_id", "")
    trip = update_trip(
        trip_id, group_id,
        arrival_time=data["arrival_time"],
        return_time=data.get("return_time", ""),
        driver_family_id=driverEl_id,
        driver_name=data.get("driver_name", ""),
        return_driver_family_id=rdEl_id,
        return_driver_name=data.get("return_driver_name", ""),
        destination_name=data.get("destination_name", ""),
        destination_address=data.get("destination_address", ""),
    )
    if not trip:
        return jsonify({"error": "trip not found"}), 404
    trip["gcal_url"] = gcal_url(trip, group_id)
    trip["gcal_url_return"] = gcal_url_return(trip, group_id)
    return jsonify(trip)


@app.route("/schedule/remove/<trip_id>", methods=["POST"])
@login_required
@admin_required
def schedule_remove(trip_id):
    group_id = gid()
    remove_trip(trip_id, group_id)
    return jsonify({"ok": True})


@app.route("/schedule/remove-series/<series_id>", methods=["POST"])
@login_required
@admin_required
def schedule_remove_series(series_id):
    group_id = gid()
    count = remove_series(series_id, group_id)
    return jsonify({"ok": True, "removed": count})


@app.route("/schedule/set-assignment-mode", methods=["POST"])
@login_required
@admin_required
def schedule_set_assignment_mode():
    group_id = gid()
    mode = request.json.get("mode", "auto")
    if mode not in ("auto", "manual"):
        return jsonify({"error": "invalid mode"}), 400
    set_assignment_mode(mode, group_id)
    return jsonify({"ok": True, "mode": mode})


@app.route("/schedule/claim/<trip_id>/<leg>", methods=["POST"])
@login_required
def schedule_claim(trip_id, leg):
    group_id = gid()
    if leg not in ("outbound", "return"):
        return jsonify({"error": "invalid leg"}), 400
    user = current_user()
    family_id = user.get("family_id")
    if not family_id:
        return jsonify({"error": "no family linked to your account"}), 400
    try:
        family = get_family(family_id, group_id)
    except ValueError:
        return jsonify({"error": "your family record no longer exists — ask your admin"}), 400
    trip = claim_trip(trip_id, leg, family_id, family.name, group_id)
    if not trip:
        return jsonify({"error": "trip not found"}), 404
    trip["gcal_url"] = gcal_url(trip, group_id)
    trip["gcal_url_return"] = gcal_url_return(trip, group_id)
    return jsonify({"ok": True, "trip": trip})


@app.route("/schedule/add-recurring", methods=["POST"])
@login_required
@admin_required
def schedule_add_recurring():
    group_id = gid()
    data = request.json
    driver_fid = data.get("driver_family_id", "")
    driver_name = data.get("driver_name", "")
    auto_mode = get_assignment_mode(group_id) == "auto"

    # Build a sequence of drivers for auto mode (cycles through rotation)
    rot_sequence: list[tuple[str, str]] = []
    if auto_mode and not driver_fid:
        rot = _load_rotation(group_id)
        idx = rot.get("current_index", 0)
        base = _rotation_pairs_from(idx, group_id)
        if base:
            rot_sequence = base  # will be indexed with modulo in loop below

    # Pre-compute per-trip drivers when using rotation sequence
    # (add_recurring_trips needs a single driver; we handle cycling here manually)
    if rot_sequence:
        weekdays = [int(d) for d in data.get("weekdays", [])]
        cur = date.fromisoformat(data["start_date"])
        end = date.fromisoformat(data["end_date"])
        series_id = str(uuid.uuid4())[:12]
        rot_n = len(rot_sequence)
        trips_list = load_schedule(group_id)
        created = []
        rot_idx = 0
        while cur <= end:
            if cur.weekday() in weekdays:
                fid, fname = rot_sequence[rot_idx % rot_n]
                rot_idx += 1
                trip = {
                    "id": str(uuid.uuid4())[:8],
                    "series_id": series_id,
                    "date": cur.isoformat(),
                    "arrival_time": data["arrival_time"],
                    "return_time": data.get("return_time", ""),
                    "return_driver_family_id": data.get("return_driver_family_id", ""),
                    "return_driver_name": data.get("return_driver_name", ""),
                    "destination_name": data["destination_name"],
                    "destination_address": data["destination_address"],
                    "driver_family_id": fid,
                    "driver_name": fname,
                }
                trips_list.append(trip)
                created.append(trip)
            cur += timedelta(days=1)
        trips_list.sort(key=lambda t: t["date"])
        save_schedule(trips_list, group_id)
        # Persist the rotation cursor forward so the next manual trip continues
        # from where this recurring series left off (previously the in-memory
        # rot_idx was thrown away).
        if rot_sequence and created:
            try:
                _set_rotation_index((idx + rot_idx) % rot_n, group_id)
            except Exception as e:
                logger.error("Failed to persist rotation index after recurring add: %s", e)
    else:
        created = add_recurring_trips(
            start_date=data["start_date"],
            end_date=data["end_date"],
            weekdays=[int(d) for d in data.get("weekdays", [])],
            arrival_time=data["arrival_time"],
            return_time=data.get("return_time", ""),
            return_driver_family_id=data.get("return_driver_family_id", ""),
            return_driver_name=data.get("return_driver_name", ""),
            destination_name=data["destination_name"],
            destination_address=data["destination_address"],
            driver_family_id=driver_fid,
            driver_name=driver_name,
            group_id=group_id,
        )

    for trip in created:
        trip["gcal_url"] = gcal_url(trip, group_id)
        trip["gcal_url_return"] = gcal_url_return(trip, group_id)
    return jsonify({"ok": True, "trips": created, "count": len(created)})


@app.route("/toggle-absent", methods=["POST"])
@login_required
def toggle_absent_route():
    group_id = gid()
    data = request.json or {}
    family_id = data.get("family_id")
    user = current_user()
    if user.get("role") != "admin" and user.get("family_id") != family_id:
        return jsonify({"error": "forbidden"}), 403
    trip_date = _next_trip_info(group_id)["date"]   # fix #2
    now_absent = toggle_absent(trip_date, family_id, group_id)
    return jsonify({"absent": now_absent})


@app.route("/running-late", methods=["POST"])
@login_required
def running_late():
    group_id = gid()
    user = current_user()
    info = _next_trip_info(group_id)
    # fix #3: only admin or the active driver can send this
    if user.get("role") != "admin" and user.get("family_id") != info["driver_id"]:
        return jsonify({"error": "Only the driver or an admin can send this."}), 403

    data = request.json or {}
    minutes  = data.get("minutes", "")
    trip_date   = info["date"]                          # fix #2
    driver_id   = info["driver_id"]
    driver_name = info["driver_name"] or "The driver"
    absences = get_absences(trip_date, group_id)

    msg = f"⏰ {driver_name} is running late"
    if minutes:
        msg += f" (~{minutes} min)"
    msg += ". Hang tight!"

    for fid in get_all_family_ids(group_id):
        if fid == driver_id or fid in absences:
            continue
        try:
            family = get_family(fid, group_id)
            if not family.guardians:
                continue
            send_sms(to_phone=family.guardians[0].phone, message=msg)
        except Exception as e:
            logger.error("Failed to send running-late SMS to family %s: %s", fid, e)

    return jsonify({"ok": True})


@app.route("/arrived", methods=["POST"])
@login_required
def arrived():
    group_id = gid()
    user = current_user()
    info = _next_trip_info(group_id)
    # fix #3: only admin or the active driver can send this
    if user.get("role") != "admin" and user.get("family_id") != info["driver_id"]:
        return jsonify({"error": "Only the driver or an admin can send this."}), 403

    cfg = load_config(group_id)
    dest_name   = info["trip"].get("destination_name") if info["trip"] else None
    dest_name   = dest_name or cfg.get("destination_name", "the destination")
    trip_date   = info["date"]                          # fix #2
    driver_id   = info["driver_id"]
    driver_name = info["driver_name"] or "The driver"
    absences    = get_absences(trip_date, group_id)

    # Idempotence: a double tap (or two parents tapping) must not re-SMS every
    # family and advance the rotation a second time.
    if info.get("trip") and info["trip"].get("rotation_advanced"):
        return jsonify({"ok": True, "already": True})
    if not info.get("trip"):
        # No scheduled trip (rotation/config fallback) — treat an existing
        # history entry for this date + driver as "already recorded".
        if any(t.get("date") == trip_date and t.get("driver_family_id") == (driver_id or "")
               for t in load_trips(group_id)):
            return jsonify({"ok": True, "already": True})

    msg = f"✅ Kids have arrived safely at {dest_name}! Thanks {driver_name}!"

    pickup_ids = []
    for fid in get_all_family_ids(group_id):
        if fid == driver_id or fid in absences:
            continue
        try:
            family = get_family(fid, group_id)
            if not family.guardians:          # fix #5
                continue
            pickup_ids.append(fid)
            send_sms(to_phone=family.guardians[0].phone, message=msg)
        except Exception as e:
            logger.error("Failed to send arrival SMS to family %s: %s", fid, e)

    # Advance rotation after trip completes, skipping anyone marked absent.
    try:
        advance_rotation(group_id, absent_ids=set(absences))
    except TypeError:
        # Older callers may not accept the absent_ids kwarg.
        advance_rotation(group_id)
    except Exception as e:
        logger.error("Failed to advance rotation: %s", e)

    # Mark today's trip(s) as rotation_advanced so the nightly cron skips it.
    if info.get("trip"):
        try:
            update_trip(info["trip"]["id"], group_id, rotation_advanced=True)
        except Exception as e:
            logger.error("Failed to mark trip rotation_advanced: %s", e)

    # Pull real miles/minutes from the route cache if it covers this trip.
    miles = 0.0
    minutes = 0
    try:
        rc = load_route_cache(group_id)
        if rc and rc.get("date") == trip_date:
            # legs are stored in seconds; total miles can be approximated from
            # leg distances if present, else leave at 0.
            minutes = int(round(sum(rc.get("leg_durations_seconds", [])) / 60)) \
                if rc.get("leg_durations_seconds") else 0
            miles = float(rc.get("total_miles", 0)) or 0.0
    except Exception as e:
        logger.warning("Could not pull miles/minutes from route cache: %s", e)

    # Record trip in history
    try:
        record_trip(
            driver_family_id=driver_id or "",
            driver_name=driver_name,
            miles=miles,
            minutes=minutes,
            # Group-local time — a bare now() is UTC on Railway and would
            # date evening trips on the following day.
            arrival=datetime.now(_group_tz(group_id)),
            pickup_family_ids=pickup_ids,
            group_id=group_id,
        )
    except Exception as e:
        logger.error("Failed to record trip history: %s", e)

    return jsonify({"ok": True})


@app.route("/send-route", methods=["POST"])
@login_required
def send_route():
    """Compute optimal pickup route and SMS it to the driver.
    Admins can always trigger this; non-admin parents can only trigger it
    when they ARE the next assigned driver (so they can pre-fetch their own route)."""
    group_id = gid()
    user = current_user()
    cfg = load_config(group_id)
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))

    # Get next scheduled trip
    today_str = _today_iso(group_id)
    schedule = load_schedule(group_id)
    upcoming = sorted(
        [t for t in schedule if t["date"] >= today_str],
        key=lambda t: (t["date"], t.get("arrival_time", "")),
    )
    if not upcoming:
        return jsonify({"ok": False, "error": "No upcoming trips scheduled."})

    # Pick the first trip whose arrival hasn't already passed — routing to a
    # past arrival time makes the Routes API reject the request.
    now = datetime.now(tz)
    trip = None
    arrival_dt = None
    for t in upcoming:
        try:
            dt = datetime.strptime(
                f"{t['date']} {t['arrival_time']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=tz)
        except (KeyError, ValueError):
            continue
        if dt >= now:
            trip, arrival_dt = t, dt
            break
    if not trip:
        return jsonify({"ok": False, "error": "Today's trip has already passed and no future trips are scheduled."})

    # Determine driver
    driver_family_id = trip.get("driver_family_id") or ""
    if not driver_family_id:
        rotation_data = _load_rotation(group_id)
        order = rotation_data.get("order", [])
        idx = rotation_data.get("current_index", 0)
        driver_family_id = order[idx] if order else ""
    if not driver_family_id:
        return jsonify({"ok": False, "error": "No driver assigned for this trip."})

    # Authorization: admins always allowed; otherwise the caller must be the
    # active driver for the next trip (so they can fetch their own route).
    if user.get("role") != "admin" and user.get("family_id") != driver_family_id:
        return jsonify({
            "ok": False,
            "error": "Only an admin or the assigned driver can send this route.",
        }), 403

    try:
        driver = get_family(driver_family_id, group_id)
    except Exception:
        return jsonify({"ok": False, "error": "Driver family not found."})

    # Geocode driver
    try:
        geocode_address(driver.primary_address)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not geocode driver address: {e}"})

    # Build pickups
    absences = get_absences(trip["date"], group_id)
    pickups = []
    for fid in get_all_family_ids(group_id):
        if fid == driver_family_id or fid in absences:
            continue
        try:
            f = get_family(fid, group_id)
            addr = f.primary_address
            geocode_address(addr)
            pickups.append({"id": fid, "lat": addr.latitude, "lng": addr.longitude, "label": f.name})
        except Exception as e:
            logger.warning("Skipping family %s in route: %s", fid, e)

    # Destination
    dest_name = trip.get("destination_name") or cfg.get("destination_name", "destination")
    dest_address = trip.get("destination_address") or cfg.get("destination_address", "")
    if not dest_address:
        return jsonify({"ok": False, "error": "No destination address set."})

    from models import Destination as _Dest
    dest = _Dest(id="custom", group_id=group_id, name=dest_name, street=dest_address)
    try:
        geocode_address(dest)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not geocode destination: {e}"})

    # Compute route
    driver_addr = driver.primary_address
    result = compute_optimal_route(
        driver_lat=driver_addr.latitude,
        driver_lng=driver_addr.longitude,
        pickups=pickups,
        dest_lat=dest.latitude,
        dest_lng=dest.longitude,
        arrival_time=arrival_dt,
        buffer_minutes=cfg.get("buffer_minutes", 15),
    )
    save_route_cache(result, driver_name=driver.name, dest_name=dest_name, group_id=group_id)

    # SMS the driver
    driver_phone = driver.guardians[0].phone if driver.guardians else ""
    if driver_phone:
        maps_url = build_maps_url(result)
        try:
            send_route_sms(
                to_phone=driver_phone,
                result=result,
                driver_name=driver.name,
                dest_name=dest_name,
                maps_url=maps_url,
            )
        except Exception as e:
            logger.error("Route SMS failed: %s", e)
            return jsonify({"ok": False, "error": f"Route computed but SMS failed: {e}"})
    else:
        return jsonify({"ok": False, "error": "Driver has no phone number on file."})

    return jsonify({"ok": True, "driver": driver.name, "pickups": len(pickups)})


def _can_share_location(user: dict, group_id: str) -> bool:
    """Only admins and the next trip's drivers (outbound or return) may start
    rides / publish live location — otherwise any member could overwrite the
    position kids are watching on the bulletin."""
    if user.get("role") == "admin":
        return True
    fam = user.get("family_id") or ""
    if not fam:
        return False
    info = _next_trip_info(group_id)
    allowed = {info.get("driver_id") or ""}
    if info.get("trip"):
        allowed.add(info["trip"].get("return_driver_family_id") or "")
    return fam in allowed


@app.route("/start-ride", methods=["POST"])
@login_required
def start_ride_route():
    group_id = gid()
    user = current_user()
    if not _can_share_location(user, group_id):
        return jsonify({"ok": False, "error": "Only the driver or an admin can start a ride."}), 403
    start_ride(driver_name=user.get("name", "Driver"), group_id=group_id)
    return jsonify({"ok": True})


@app.route("/start-return", methods=["POST"])
@login_required
def start_return_route():
    group_id = gid()
    user = current_user()
    if not _can_share_location(user, group_id):
        return jsonify({"ok": False, "error": "Only the driver or an admin can start a ride."}), 403
    start_ride(driver_name=user.get("name", "Driver"), group_id=group_id, trip_leg="return")
    return jsonify({"ok": True})


@app.route("/stop-ride", methods=["POST"])
@login_required
def stop_ride_route():
    group_id = gid()
    stop_ride(group_id)
    return jsonify({"ok": True})


@app.route("/update-location", methods=["POST"])
@login_required
def update_location_route():
    from eta import compute_etas
    group_id = gid()
    data = request.json or {}
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "lat/lng required"}), 400
    if not _can_share_location(current_user(), group_id):
        return jsonify({"ok": False, "error": "Only the driver or an admin can share location."}), 403
    update_location(lat=lat, lng=lng, group_id=group_id)
    # ETAs are for the next actual trip (schedule-aware), not the config date.
    info = _next_trip_info(group_id)
    try:
        etas = compute_etas(lat, lng, info["date"], group_id,
                            driver_family_id=info.get("driver_id") or "")
    except Exception:
        etas = []
    loc = get_location(group_id)
    loc["etas"] = etas
    from location import _save as _save_location
    _save_location(loc, group_id)
    return jsonify({"ok": True})


@app.route("/get-location")
@login_required
def get_location_route():
    """Return live driver location. Only members of the requested group may view it."""
    group_id = request.args.get("group_id", "") or gid() or ""
    if not group_id:
        return jsonify({"active": False})
    # Group-isolation check: the caller must belong to the group they're asking about.
    if group_id != gid():
        return jsonify({"active": False}), 403
    return jsonify(get_location(group_id))


@app.route("/bulletin")
@login_required
def bulletin_legacy():
    """Backward-compat redirect — old bookmarks/links without group_id."""
    group_id = gid()
    if not group_id:
        return redirect(url_for("login"))
    return redirect(url_for("bulletin", group_id=group_id))


@app.route("/bulletin/<group_id>")
@login_required
def bulletin(group_id):
    if not get_group(group_id):
        return "Group not found", 404
    # Only members of this group can view its bulletin.
    if group_id != gid():
        return "Forbidden", 403

    cfg = load_config(group_id)
    trip_time = arrival_time(group_id)
    rotation_data = _load_rotation(group_id)
    order = rotation_data.get("order", get_all_family_ids(group_id))
    current_index = rotation_data.get("current_index", 0)
    next_driver_id = order[current_index] if order else None
    try:
        next_driver_name = get_family(next_driver_id, group_id).name if next_driver_id else "—"
    except ValueError:
        next_driver_name = "—"
    default_dest = get_destination(get_destination_id(group_id))
    dest_name = cfg.get("destination_name") or default_dest.name

    schedule = load_schedule(group_id)
    rotation = []
    for i, fid in enumerate(order):
        try:
            family = get_family(fid, group_id)
        except ValueError:
            continue
        rotation.append({
            "name": family.name,
            "is_next": fid == next_driver_id,
            "position": i + 1,
        })

    route_cache = load_route_cache(group_id)
    live_location = get_location(group_id)

    return render_template(
        "bulletin.html",
        maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        live_location=live_location,
        group_name=get_group_name(group_id),
        group_id=group_id,
        route_cache=route_cache,
        next_trip={
            "date": trip_time.strftime("%A, %B %d, %Y"),
            "date_raw": trip_time.strftime("%Y-%m-%d"),
            "driver": next_driver_name,
            "destination": dest_name,
            "arrive_by": trip_time.strftime("%-I:%M %p"),
            "return_time": cfg.get("return_time", ""),
            "return_driver": cfg.get("return_driver_name", ""),
        },
        schedule=schedule,
        rotation=rotation,
    )


# ── SMS Webhook (Twilio) ──────────────────────────────────────────────────────
# Handles inbound SMS replies: YES confirmations, SWAP requests, swap volunteers.

def _confirmations_file(group_id: str):
    return group_dir(group_id) / "confirmations.json"

def _swap_file(group_id: str):
    return group_dir(group_id) / "swap_state.json"


def _load_confirmations(group_id: str) -> dict:
    return read_json(_confirmations_file(group_id), default={})

def _save_confirmations(data: dict, group_id: str) -> None:
    atomic_write_json(_confirmations_file(group_id), data)

def _load_swap(group_id: str) -> dict:
    return read_json(_swap_file(group_id), default={})

def _save_swap(data: dict, group_id: str) -> None:
    atomic_write_json(_swap_file(group_id), data)


def _norm_phone(phone: str) -> str:
    """Twilio WhatsApp webhooks send numbers as 'whatsapp:+1201...' while we
    store bare E.164 ('+1201...'). Normalize before comparing."""
    return (phone or "").removeprefix("whatsapp:").strip()


def _phone_to_family_and_group(phone: str):
    """Find a family across all groups by phone number."""
    phone = _norm_phone(phone)
    for group in list_groups():
        gid_val = group["id"]
        for fid in get_all_family_ids(gid_val):
            try:
                fam = get_family(fid, gid_val)
                if fam.guardians and _norm_phone(fam.guardians[0].phone) == phone:
                    return fam, gid_val
            except Exception:
                continue
    return None, None


def _handle_swap(from_phone: str, reason: str) -> str:
    from datetime import timezone, timedelta
    family, group_id = _phone_to_family_and_group(from_phone)
    if not family:
        return "Sorry, your number isn't registered in this carpool."
    # Check against the next trip's actual driver (schedule first, rotation
    # fallback) — the rotation pointer alone misses schedule-assigned drivers.
    info = _next_trip_info(group_id)
    if family.id != info["driver_id"]:
        return "You're not the driver for the next trip, so no swap needed."
    trip = info.get("trip")
    tz = ZoneInfo(load_config(group_id).get("timezone", "America/New_York"))
    trip_time = None
    if trip:
        try:
            trip_time = datetime.strptime(
                f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=tz)
        except (KeyError, ValueError):
            trip_time = None
    if trip_time is None:
        trip_time = arrival_time(group_id)
    now = datetime.now(timezone.utc)
    if now >= trip_time - timedelta(days=1):
        return "It's less than 1 day before the trip — too late to swap automatically. Please contact the admin directly."
    date_label = trip_time.strftime("%a %b %d")
    others = [get_family(fid, group_id) for fid in get_all_family_ids(group_id) if fid != family.id]
    asked = []
    for other in others:
        phone = other.guardians[0].phone if other.guardians else ""
        if phone:
            try:
                send_sms(to_phone=phone, message=(
                    f"{family.name} can't drive on {date_label} ({reason}). "
                    f"Can you take over? Reply YES to volunteer."
                ))
                asked.append(phone)
            except Exception as e:
                logger.error("Failed to send swap request SMS to %s: %s", phone, e)
    _record_swap_req(family.id, family.name, group_id)
    _save_swap({"pending": True, "original_driver_id": family.id,
                "original_driver_phone": _norm_phone(from_phone), "reason": reason,
                "asked_phones": asked, "confirmed_driver_id": None,
                "trip_id": trip["id"] if trip else "",
                "trip_date_label": date_label,
                "group_id": group_id}, group_id)
    return f"Got it! Asked {len(asked)} other drivers. We'll let you know who takes over."


def _handle_yes_for_swap(from_phone: str) -> str | None:
    # Check across all groups for a pending swap that asked this phone
    from_norm = _norm_phone(from_phone)
    for group in list_groups():
        gid_val = group["id"]
        swap = _load_swap(gid_val)
        if not swap.get("pending"):
            continue
        if from_norm not in [_norm_phone(p) for p in swap.get("asked_phones", [])]:
            continue
        if swap.get("confirmed_driver_id"):
            return "Thanks, but someone already volunteered to drive!"
        family, _ = _phone_to_family_and_group(from_phone)
        if not family:
            return None
        try:
            _set_driver(family.id, gid_val)
        except ValueError:
            logger.warning("Swap volunteer %s not in rotation for group %s", family.id, gid_val)
        # Reassign the scheduled trip itself — the auto-route sender and the
        # dashboards read the trip's driver, not the rotation pointer.
        if swap.get("trip_id"):
            try:
                update_trip(swap["trip_id"], gid_val,
                            driver_family_id=family.id, driver_name=family.name)
            except Exception as e:
                logger.error("Swap: failed to reassign trip %s: %s", swap["trip_id"], e)
        swap["pending"] = False
        swap["confirmed_driver_id"] = family.id
        _save_swap(swap, gid_val)
        _record_swap_cover(family.id, family.name, gid_val)
        date_label = swap.get("trip_date_label") or arrival_time(gid_val).strftime("%a %b %d")
        send_sms(to_phone=swap["original_driver_phone"],
                 message=f"Great news! {family.name} will drive on {date_label}. You're off the hook!")
        for phone in swap["asked_phones"]:
            if _norm_phone(phone) != from_norm:
                send_sms(to_phone=phone,
                         message=f"{family.name} has volunteered to drive on {date_label}. No need to respond.")
        return f"You're confirmed as driver on {date_label}! You'll get route details closer to the trip."
    return None


@csrf.exempt
@app.route("/sms", methods=["POST"])
def sms_webhook():
    # Verify the request genuinely came from Twilio.
    # Railway sits behind a TLS-terminating proxy, so request.url may arrive
    # as http:// — we reconstruct the https:// URL that Twilio signed.
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        if _is_prod:
            # In production we require signature verification — otherwise anyone
            # on the internet can POST fake SMS bodies and trigger sends/state changes.
            logger.error("TWILIO_AUTH_TOKEN not set in production; rejecting webhook.")
            return Response("Webhook misconfigured", status=503)
        logger.warning("TWILIO_AUTH_TOKEN not set (dev mode) — skipping signature check.")
    else:
        validator = RequestValidator(auth_token)
        url = request.url.replace("http://", "https://", 1)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validator.validate(url, request.form, signature):
            logger.warning("Rejected SMS webhook: invalid Twilio signature from %s", request.remote_addr)
            return Response("Forbidden", status=403)

    from_number = request.form.get("From", "")
    raw_body = request.form.get("Body", "").strip()
    upper = raw_body.upper()
    # Don't log full phone numbers or message bodies at INFO — they're PII.
    # Mask the phone to last 4 digits and trim body to a length only.
    _masked = (from_number[-4:] if len(from_number) >= 4 else "????")
    logger.info("SMS received (from ...%s, %d chars)", _masked, len(raw_body))
    logger.debug("SMS body: %r", raw_body)

    reply = None
    if upper == "YES":
        reply = _handle_yes_for_swap(from_number)
        if reply is None:
            # Store confirmation in whatever group this phone belongs to
            _, group_id = _phone_to_family_and_group(from_number)
            if group_id:
                confs = _load_confirmations(group_id)
                confs[_norm_phone(from_number)] = raw_body
                _save_confirmations(confs, group_id)
            reply = "Got it, confirmed!"
    elif upper.startswith("SWAP"):
        reason = raw_body[4:].strip() or "no reason given"
        reply = _handle_swap(from_number, reason)
    else:
        # Only treat free-text replies as pickup-address confirmations if they
        # actually look like an address (number + street word, or contains a comma).
        # Otherwise "Thanks!" or "ok" would silently overwrite their address.
        _looks_like_address = bool(re.search(r"\d+\s+\w", raw_body)) or ("," in raw_body)
        if _looks_like_address:
            _, group_id = _phone_to_family_and_group(from_number)
            if group_id:
                confs = _load_confirmations(group_id)
                confs[_norm_phone(from_number)] = raw_body
                _save_confirmations(confs, group_id)
            reply = f"Got it! We'll pick you up at: {raw_body}"
        else:
            reply = (
                "Sorry, I didn't recognize that. Reply YES to confirm pickup, "
                "SWAP <reason> to request a swap, or send your pickup address."
            )

    if reply:
        try:
            send_sms(to_phone=from_number, message=reply)
        except Exception as e:
            logger.error("Failed to send SMS reply to %s: %s", from_number, e)

    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(twiml, mimetype="text/xml")


# ── Auto route sender ─────────────────────────────────────────────────────────

def _auto_send_route_for_group(group_id: str) -> None:
    """Send route to driver if a trip starts within the next 2 hours and hasn't been sent yet."""
    try:
        cfg = load_config(group_id)
        tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
        now = datetime.now(tz)
        window_start = now
        window_end   = now + timedelta(hours=2)

        schedule = load_schedule(group_id)
        for trip in schedule:
            if trip.get("route_sent"):
                continue
            try:
                trip_dt = datetime.strptime(
                    f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=tz)
            except (ValueError, KeyError):
                continue

            if not (window_start <= trip_dt <= window_end):
                continue

            # Isolate per-trip failures (e.g. one driver's un-geocodable
            # address) so they don't abort routing for the group's remaining
            # trips in this window.
            try:
                _auto_send_route_for_trip(trip, trip_dt, cfg, group_id)
            except Exception as e:
                logger.error("Auto-route failed for trip %s in group %s: %s",
                             trip.get("id"), group_id, e)

    except Exception as e:
        logger.error("Auto-route error for group %s: %s", group_id, e)


def _auto_send_route_for_trip(trip: dict, trip_dt: datetime, cfg: dict, group_id: str) -> None:
    """Compute and SMS the route for a single trip. Raises on failure; the
    caller logs and moves on to the group's next trip."""
    # Determine driver
    driver_family_id = trip.get("driver_family_id") or ""
    if not driver_family_id:
        rotation_data = _load_rotation(group_id)
        order = rotation_data.get("order", [])
        idx   = rotation_data.get("current_index", 0)
        driver_family_id = order[idx] if order else ""
    if not driver_family_id:
        logger.warning("Auto-route: no driver for trip %s in group %s", trip["id"], group_id)
        return

    driver = get_family(driver_family_id, group_id)
    geocode_address(driver.primary_address)

    absences = get_absences(trip["date"], group_id)
    pickups  = []
    for fid in get_all_family_ids(group_id):
        if fid == driver_family_id or fid in absences:
            continue
        try:
            f = get_family(fid, group_id)
            geocode_address(f.primary_address)
            addr = f.primary_address
            pickups.append({"id": fid, "lat": addr.latitude, "lng": addr.longitude, "label": f.name})
        except Exception as e:
            logger.warning("Auto-route: skipping family %s: %s", fid, e)

    dest_name    = trip.get("destination_name") or cfg.get("destination_name", "destination")
    dest_address = trip.get("destination_address") or cfg.get("destination_address", "")
    if not dest_address:
        logger.warning("Auto-route: no destination address for trip %s", trip["id"])
        return

    from models import Destination as _Dest
    dest = _Dest(id="custom", group_id=group_id, name=dest_name, street=dest_address)
    geocode_address(dest)

    driver_addr = driver.primary_address
    result = compute_optimal_route(
        driver_lat=driver_addr.latitude,
        driver_lng=driver_addr.longitude,
        pickups=pickups,
        dest_lat=dest.latitude,
        dest_lng=dest.longitude,
        arrival_time=trip_dt,
        buffer_minutes=cfg.get("buffer_minutes", 15),
    )
    save_route_cache(result, driver_name=driver.name, dest_name=dest_name, group_id=group_id)

    driver_phone = driver.guardians[0].phone if driver.guardians else ""
    if not driver_phone:
        logger.warning("Auto-route: driver %s has no phone", driver.name)
        return

    # Mark as sent BEFORE the SMS to prevent retry storms if SMS fails or is slow.
    # An occasional missed SMS is better than 8 duplicate SMSes per trip.
    update_trip(trip["id"], group_id, route_sent=True)

    try:
        maps_url = build_maps_url(result)
        send_route_sms(
            to_phone=driver_phone,
            result=result,
            driver_name=driver.name,
            dest_name=dest_name,
            maps_url=maps_url,
        )
        logger.info("Auto-route sent to %s for trip %s", driver.name, trip["id"])
    except Exception as e:
        logger.error("Auto-route SMS failed for trip %s: %s", trip["id"], e)


def _auto_send_routes_all_groups() -> None:
    from groups import list_groups
    for g in list_groups():
        _auto_send_route_for_group(g["id"])


def _nightly_advance_rotations() -> None:
    """Hourly job: for each group where it's currently 11pm LOCAL time, if
    today had a scheduled trip and the rotation pointer wasn't advanced
    (driver never tapped 'Arrived'), roll it forward — skipping any drivers
    marked absent for that date. Runs hourly (not at a fixed server hour)
    because the server clock is UTC and groups carry their own timezone —
    a fixed 23:00 server cron would fire at 6-7pm Eastern, advancing the
    rotation before evening trips even happen."""
    from groups import list_groups
    from rotation import _load as _load_rot, advance as _advance_rot
    for g in list_groups():
        gid_ = g["id"]
        try:
            now_local = datetime.now(_group_tz(gid_))
            if now_local.hour != 23:
                continue
            today_iso = now_local.strftime("%Y-%m-%d")
            schedule = load_schedule(gid_)
            todays_trips = [t for t in schedule if t.get("date") == today_iso]
            if not todays_trips:
                continue
            # If any of today's trips are already marked rotation_advanced, skip.
            if any(t.get("rotation_advanced") for t in todays_trips):
                continue
            absent = set(get_absences(today_iso, gid_))
            rot = _load_rot(gid_)
            if not rot.get("order"):
                continue
            _advance_rot(gid_, absent_ids=absent)
            # Mark today's trips so we don't re-advance on the next run.
            for t in todays_trips:
                update_trip(t["id"], gid_, rotation_advanced=True)
            logger.info("Nightly rotation advance for group %s", gid_)
        except Exception as e:
            logger.error("Nightly rotation advance failed for %s: %s", gid_, e)


def _start_scheduler() -> None:
    """Start the background job runner. We guard against duplicate scheduling
    in multi-worker gunicorn setups by using a filesystem lock — only the
    worker that acquires it starts the scheduler. Other workers no-op."""
    import fcntl as _fcntl
    from apscheduler.schedulers.background import BackgroundScheduler

    lock_path = DATA_DIR / ".scheduler.lock"
    try:
        # Hold this file lock for the lifetime of the process. If another
        # worker already has it, this raises and we silently return.
        lock_fh = open(lock_path, "w")
        _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        logger.info("Scheduler lock held by another worker — skipping start.")
        return

    # Keep a module-global reference so the file handle (and thus the lock)
    # outlives this function.
    global _scheduler_lock_fh
    _scheduler_lock_fh = lock_fh  # noqa: F841

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _auto_send_routes_all_groups,
        trigger="interval",
        minutes=15,
        id="auto_route",
        replace_existing=True,
    )
    scheduler.add_job(
        _nightly_advance_rotations,
        # Hourly on the hour; the job itself only acts on groups where the
        # local time is 11pm (see _nightly_advance_rotations docstring).
        trigger="cron",
        minute=0,
        id="nightly_rotation",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Background scheduler started (this worker is the leader).")


_scheduler_lock_fh = None  # holds the worker-leader lock if we won it

# Start scheduler (skip during pytest / flask test runs)
import sys as _sys
if "pytest" not in _sys.modules and os.environ.get("FLASK_TESTING") != "1":
    _start_scheduler()


# ── Admin: User Management ────────────────────────────────────────────────────

def _cleanup_family_if_orphaned(family_id: str, group_id_: str) -> None:
    """If no users remain for this family in this group, scrub it from the
    rotation and clear it off future scheduled trips — otherwise deletes
    leave a phantom driver who can't log in."""
    if not family_id or not group_id_:
        return
    from auth import _load_users
    remaining = [u for u in _load_users()
                 if u.get("family_id") == family_id and u.get("group_id") == group_id_]
    if remaining:
        return
    try:
        from rotation import remove_from_rotation
        remove_from_rotation(family_id, group_id_)
    except Exception as e:
        logger.error("Cascade: remove_from_rotation failed: %s", e)
    try:
        today_iso = _today_iso(group_id_)
        sched = load_schedule(group_id_)
        changed = False
        for t in sched:
            if t.get("date", "") < today_iso:
                continue
            if t.get("driver_family_id") == family_id:
                t["driver_family_id"] = ""
                t["driver_name"] = ""
                changed = True
            if t.get("return_driver_family_id") == family_id:
                t["return_driver_family_id"] = ""
                t["return_driver_name"] = ""
                changed = True
        if changed:
            save_schedule(sched, group_id_)
    except Exception as e:
        logger.error("Cascade: schedule cleanup failed: %s", e)


def _stale_groups(current_group_id: str) -> list:
    """Return groups that have no users assigned and aren't the admin's own group.
    These are safe candidates for deletion (typically leftover seed groups)."""
    from auth import _load_users
    users = _load_users()
    groups_with_users = {u.get("group_id") for u in users if u.get("group_id")}
    stale = []
    for g in list_groups():
        if g["id"] == current_group_id:
            continue
        if g["id"] in groups_with_users:
            continue
        stale.append(g)
    return stale


@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    group_id = gid()
    users = get_users_by_group(group_id)
    user = current_user()
    flash_msg = session.pop("admin_flash", None)
    flash_error = session.pop("admin_flash_error", None)
    stale_groups = _stale_groups(group_id)

    # Find duplicate accounts across ALL groups that share the admin's email
    # (case-insensitive). Useful when accounts were created in earlier deploys
    # that ended up in different groups.
    from auth import _load_users
    my_email = (user.get("email") or "").lower()
    duplicates = []
    if my_email:
        matching = sorted(
            [u for u in _load_users() if (u.get("email") or "").lower() == my_email],
            key=lambda u: u.get("joined_at", ""),
            reverse=True,
        )
        if len(matching) > 1:
            duplicates = matching  # newest first; first one is the "keeper"

    return render_template(
        "admin_users.html",
        users=users,
        current_user=user,
        flash_msg=flash_msg,
        flash_error=flash_error,
        stale_groups=stale_groups,
        duplicates=duplicates,
    )


@app.route("/admin/users/dedupe", methods=["POST"])
@login_required
@admin_required
def admin_dedupe_self():
    """Delete every account sharing the admin's email except the newest one.
    The newest one becomes the canonical account; the session is updated to
    point at it so the admin doesn't get logged out."""
    from auth import _load_users
    user = current_user()
    my_email = (user.get("email") or "").lower()
    if not my_email:
        session["admin_flash_error"] = "Your account has no email on file."
        return redirect(url_for("admin_users"))

    matching = sorted(
        [u for u in _load_users() if (u.get("email") or "").lower() == my_email],
        key=lambda u: u.get("joined_at", ""),
        reverse=True,
    )
    if len(matching) <= 1:
        session["admin_flash"] = "No duplicates found for your email."
        return redirect(url_for("admin_users"))

    keeper = matching[0]
    to_delete = matching[1:]
    deleted = 0
    for u in to_delete:
        try:
            delete_user(u["id"])
            deleted += 1
            # Same cascade as single-user delete — deleted accounts may live
            # in OTHER groups; without this their families stay in those
            # groups' rotations/schedules as phantom drivers.
            _cleanup_family_if_orphaned(u.get("family_id", ""), u.get("group_id", ""))
        except Exception as e:
            logger.error("Dedupe: failed to delete user %s: %s", u["id"], e)

    # Make sure the session points at the keeper, so the admin stays logged in
    # even if their previous user_id was one of the deleted rows.
    session["user_id"] = keeper["id"]
    session["group_id"] = keeper.get("group_id", "")

    session["admin_flash"] = (
        f"Deleted {deleted} duplicate account(s) for {my_email}. "
        f"Kept the newest (joined {keeper.get('joined_at', '')[:10]})."
    )
    return redirect(url_for("admin_users"))


@app.route("/admin/users/delete/<user_id>", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id):
    user = current_user()
    if user and user.get("id") == user_id:
        session["admin_flash_error"] = "You cannot delete your own account."
        return redirect(url_for("admin_users"))
    # Security: only allow deleting users that belong to the admin's own group.
    target = get_user_by_id(user_id)
    if not target:
        session["admin_flash_error"] = "User not found."
        return redirect(url_for("admin_users"))
    if target.get("group_id") != gid():
        session["admin_flash_error"] = "You can only manage users in your own group."
        return redirect(url_for("admin_users"))

    # Cascade: if this user is the last one in their family, scrub the family
    # from rotation and clear them out of future scheduled trips so we don't
    # leave a phantom driver who can't log in.
    family_id = target.get("family_id", "")
    group_id_ = target.get("group_id", "")
    deleted = delete_user(user_id)
    if not deleted:
        session["admin_flash_error"] = "User not found."
        return redirect(url_for("admin_users"))

    try:
        _cleanup_family_if_orphaned(family_id, group_id_)
    except Exception as e:
        logger.error("Cascade cleanup error: %s", e)

    session["admin_flash"] = "User deleted (and family scrubbed from rotation if applicable)."
    return redirect(url_for("admin_users"))


@app.route("/admin/users/reset-password/<user_id>", methods=["POST"])
@login_required
@admin_required
def admin_reset_password(user_id):
    new_password = request.form.get("new_password", "").strip()
    if len(new_password) < 8:
        session["admin_flash_error"] = "Password must be at least 8 characters."
        return redirect(url_for("admin_users"))
    # Security: only allow resetting passwords of users in the admin's own group.
    target = get_user_by_id(user_id)
    if not target:
        session["admin_flash_error"] = "User not found."
        return redirect(url_for("admin_users"))
    if target.get("group_id") != gid():
        session["admin_flash_error"] = "You can only manage users in your own group."
        return redirect(url_for("admin_users"))
    update_password(user_id, new_password)
    session["admin_flash"] = "Password updated successfully."
    return redirect(url_for("admin_users"))


@app.route("/admin/system")
@login_required
@admin_required
def admin_system():
    """System-wide view of all users + groups + storage, for the admin.
    Useful for debugging and seeing what's actually in the database."""
    from auth import _load_users
    from groups import _load_groups
    users = _load_users()
    groups = _load_groups()
    # Volume / storage info
    data_dir_env = os.environ.get("DATA_DIR", "NOT SET")
    is_ephemeral = str(DATA_DIR) == str(CODE_DIR)
    try:
        test = DATA_DIR / ".write_test"
        test.write_text("ok")
        test.unlink()
        writable = True
    except Exception:
        writable = False
    # Per-group counts (trips, schedule entries)
    group_info = []
    for g in groups:
        gid_ = g["id"]
        try:
            from schedule import load_schedule
            sched = load_schedule(gid_)
        except Exception:
            sched = []
        group_info.append({
            **g,
            "user_count": sum(1 for u in users if u.get("group_id") == gid_),
            "trip_count": len(sched),
        })
    return render_template(
        "admin_system.html",
        users=users,
        groups=group_info,
        data_dir=str(DATA_DIR),
        data_dir_env=data_dir_env,
        is_ephemeral=is_ephemeral,
        writable=writable,
    )


@app.route("/admin/groups/delete/<group_id>", methods=["POST"])
@login_required
@admin_required
def admin_delete_group(group_id):
    """Delete a stale group (one with no users). Refuses to delete the admin's
    own group or any group that still has users assigned."""
    import shutil
    from groups import _load_groups, _save_groups
    from auth import _load_users

    # Never delete your own group from here.
    if group_id == gid():
        session["admin_flash_error"] = "You can't delete your own group."
        return redirect(url_for("admin_users"))

    # Validate id shape (defense-in-depth against path traversal).
    if not re.fullmatch(r"grp_[a-zA-Z0-9_]+", group_id):
        session["admin_flash_error"] = "Invalid group id."
        return redirect(url_for("admin_users"))

    # Refuse if the group still has users.
    users = _load_users()
    if any(u.get("group_id") == group_id for u in users):
        session["admin_flash_error"] = "That group still has users assigned. Delete them first."
        return redirect(url_for("admin_users"))

    # Remove from groups.json
    groups = _load_groups()
    new_groups = [g for g in groups if g["id"] != group_id]
    if len(new_groups) == len(groups):
        session["admin_flash_error"] = "Group not found."
        return redirect(url_for("admin_users"))
    _save_groups(new_groups)

    # Remove the group's data directory
    try:
        gdir = DATA_DIR / "groups" / group_id
        if gdir.exists():
            shutil.rmtree(gdir)
    except Exception as e:
        logger.error("Failed to remove group dir %s: %s", group_id, e)

    session["admin_flash"] = f"Group {group_id} deleted."
    return redirect(url_for("admin_users"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
