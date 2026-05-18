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
from urllib.parse import quote
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
from storage import DATA_DIR, CODE_DIR, group_dir, migrate_legacy_data
from families import get_family, get_destination, get_all_family_ids, add_family
from rotation import _load as _load_rotation, add_to_rotation, set_driver as _set_driver
from trips import get_stats, load_trips, record_trip
from schedule import load_schedule, save_schedule, add_trip, update_trip, remove_trip, remove_series, add_recurring_trips, claim_trip
from karma import get_karma, record_swap_request as _record_swap_req, record_swap_cover as _record_swap_cover
from cal_feed import build_ics
from auth import (
    get_user_by_email, get_user_by_id,
    verify_password, create_user,
    generate_invite_token, verify_invite_token, mark_invite_used,
    generate_reset_token, verify_reset_token, mark_reset_used, update_password,
    purge_old_tokens,
)
from groups import create_group as _create_group, get_group, list_groups
from sms import send_sms, send_route_sms, SANDBOX_NUMBER, SANDBOX_KEYWORD
from absences import toggle_absent, get_absences
from route_cache import load as load_route_cache, save as save_route_cache
from routing import compute_optimal_route, build_maps_url
from geocode import geocode_address
from location import start_ride, stop_ride, update_location, get_location

load_dotenv()

# Run legacy data migration on startup so existing single-group data is accessible
migrate_legacy_data("grp_main")


def _bootstrap_legacy_group() -> None:
    """
    One-time bootstrap for deployments upgrading from single-group to multi-group.
    Ensures grp_main exists in groups.json and all users without group_id are
    assigned to grp_main so existing accounts keep working after the upgrade.
    """
    from groups import _load_groups, _save_groups
    from auth import _load_users, _save_users
    from config import load_config

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
    users = _load_users()
    patched = False
    for u in users:
        if not u.get("group_id"):
            u["group_id"] = "grp_main"
            patched = True
    if patched:
        _save_users(users)


_bootstrap_legacy_group()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_secret = os.environ.get("SECRET_KEY", "")
if not _secret:
    logger.error("SECRET_KEY is not set — using an insecure default. Set SECRET_KEY in your environment.")
    _secret = "dev-secret-change-me"
app.secret_key = _secret


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
    title = quote(f"{group_name} — {trip['destination_name']} (There)")
    details = quote(
        f"Driver: {trip['driver_name']}\n"
        f"Pickup starts: {pickup_start}\n"
        f"Arrive by: {arrival.strftime('%-I:%M %p')}"
    )
    location = quote(f"{trip['destination_address']} ({group_name})")
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
    title = quote(f"{group_name} — {trip['destination_name']} (Return)")
    details = quote(
        f"Driver: {driver}\n"
        f"Pickup from {trip['destination_name']}: {pickup.strftime('%-I:%M %p')}"
    )
    location = quote(f"{trip['destination_address']} ({group_name})")
    dates = f"{pickup.strftime(fmt)}/{arrive_home.strftime(fmt)}"
    return f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={title}&dates={dates}&details={details}&location={location}"


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    error = None
    email = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = get_user_by_email(email)
        if user and verify_password(password, user["password_hash"]):
            session["user_id"] = user["id"]
            session["group_id"] = user.get("group_id", "")
            try:
                purge_old_tokens()
            except Exception as e:
                logger.error("Token purge failed: %s", e)
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid email or password."

    return render_template("login.html", error=error, email=email)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    sent = False
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
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
                logger.error("Password reset SMS failed for %s: %s", email, e)
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
        password = request.form.get("password", "")
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
        password = request.form.get("password", "")

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
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif get_user_by_email(form["email"]):
            error = "An account with that email already exists."
        else:
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
            session["user_id"] = user["id"]
            session["group_id"] = group["id"]
            return redirect(url_for("dashboard"))

    return render_template("create_group.html", error=error, form=form)


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
        password = request.form.get("password", "")
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
        return redirect(url_for("dashboard"))

    # Normalize to E.164 format
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        phone = f"+1{digits}"          # assume US
    elif len(digits) == 11 and digits.startswith('1'):
        phone = f"+{digits}"
    else:
        phone = f"+{digits}"

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
        family = get_family(fid, group_id)
        rotation.append({"name": family.name, "id": fid, "is_next": fid == next_driver_id})

    next_driver_name = get_family(next_driver_id, group_id).name if next_driver_id else "—"
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
    today_str = date.today().isoformat()
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

    group_name = get_group_name(group_id)

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
        schedule_json=json.dumps(schedule),
        families=families,
        karma=karma,
        invite_status=invite_status,
        pickup_families=pickup_families,
        next_driver_id=next_driver_id,
        trip_date=trip_date,
        live_location=get_location(group_id),
        maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        assignment_mode=get_assignment_mode(group_id),
    )


# ── Trip config (admin only) ──────────────────────────────────────────────────

@app.route("/save-trip", methods=["POST"])
@login_required
@admin_required
def save_trip():
    group_id = gid()
    cfg = load_config(group_id)

    arrival_date        = request.form["arrival_date"]
    arrival_time        = request.form["arrival_time"]
    return_time         = request.form.get("return_time", "").strip()
    driver_family_id    = request.form.get("driver_family_id", "").strip()
    driver_name         = request.form.get("driver_name", "").strip()
    return_driver_fid   = request.form.get("return_driver_family_id", "").strip()
    return_driver_name  = request.form.get("return_driver_name", "").strip()
    destination_name    = request.form.get("destination_name", "").strip()
    destination_address = request.form.get("destination_address", "").strip()
    buffer_minutes      = int(request.form.get("buffer_minutes", 5))
    group_name          = request.form.get("group_name", "").strip()

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
    group_id = gid()
    ics = build_ics(group_id)
    return Response(ics, mimetype="text/calendar", headers={
        "Content-Disposition": "inline; filename=carpool.ics"
    })


# ── Schedule ──────────────────────────────────────────────────────────────────

def _rotation_pairs_from(start_index: int, group_id: str) -> list[tuple[str, str]]:
    """Return (family_id, name) pairs for the rotation, starting at start_index."""
    rot = _load_rotation(group_id)
    order = rot.get("order", [])
    if not order:
        return []
    pairs = [(fid, get_family(fid, group_id).name) for fid in order]
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
    family = get_family(family_id, group_id)
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
    trip_date = arrival_time(group_id).strftime("%Y-%m-%d")
    now_absent = toggle_absent(trip_date, family_id, group_id)
    return jsonify({"absent": now_absent})


@app.route("/running-late", methods=["POST"])
@login_required
def running_late():
    group_id = gid()
    data = request.json or {}
    minutes = data.get("minutes", "")
    cfg = load_config(group_id)
    trip_date = arrival_time(group_id).strftime("%Y-%m-%d")
    absences = get_absences(trip_date, group_id)
    rotation_data = _load_rotation(group_id)
    order = rotation_data.get("order", get_all_family_ids(group_id))
    current_index = rotation_data.get("current_index", 0)
    driver_id = order[current_index] if order else None
    driver_name = get_family(driver_id, group_id).name if driver_id else "The driver"

    msg = f"⏰ {driver_name} is running late"
    if minutes:
        msg += f" (~{minutes} min)"
    msg += ". Hang tight!"

    for fid in get_all_family_ids(group_id):
        if fid == driver_id or fid in absences:
            continue
        family = get_family(fid, group_id)
        try:
            send_sms(to_phone=family.guardians[0].phone, message=msg)
        except Exception as e:
            logger.error("Failed to send running-late SMS to family %s: %s", fid, e)

    return jsonify({"ok": True})


@app.route("/arrived", methods=["POST"])
@login_required
def arrived():
    group_id = gid()
    cfg = load_config(group_id)
    dest_name = cfg.get("destination_name", "the destination")
    trip_date = arrival_time(group_id).strftime("%Y-%m-%d")
    absences = get_absences(trip_date, group_id)
    rotation_data = _load_rotation(group_id)
    order = rotation_data.get("order", get_all_family_ids(group_id))
    current_index = rotation_data.get("current_index", 0)
    driver_id = order[current_index] if order else None
    driver_name = get_family(driver_id, group_id).name if driver_id else "The driver"

    msg = f"✅ Kids have arrived safely at {dest_name}! Thanks {driver_name}!"

    pickup_ids = []
    for fid in get_all_family_ids(group_id):
        if fid == driver_id or fid in absences:
            continue
        family = get_family(fid, group_id)
        pickup_ids.append(fid)
        try:
            send_sms(to_phone=family.guardians[0].phone, message=msg)
        except Exception as e:
            logger.error("Failed to send arrival SMS to family %s: %s", fid, e)

    # Record trip in history
    try:
        record_trip(
            driver_family_id=driver_id or "",
            driver_name=driver_name,
            miles=0.0,
            minutes=0,
            arrival=datetime.now(),
            pickup_family_ids=pickup_ids,
            group_id=group_id,
        )
    except Exception as e:
        logger.error("Failed to record trip history: %s", e)

    return jsonify({"ok": True})


@app.route("/send-route", methods=["POST"])
@login_required
@admin_required
def send_route():
    """Compute optimal pickup route and SMS it to the driver."""
    group_id = gid()
    cfg = load_config(group_id)
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))

    # Get next scheduled trip
    today_str = date.today().isoformat()
    schedule = load_schedule(group_id)
    upcoming = sorted([t for t in schedule if t["date"] >= today_str], key=lambda t: t["date"])
    if not upcoming:
        return jsonify({"ok": False, "error": "No upcoming trips scheduled."})

    trip = upcoming[0]
    arrival_dt = datetime.strptime(
        f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=tz)

    # Determine driver
    driver_family_id = trip.get("driver_family_id") or ""
    if not driver_family_id:
        rotation_data = _load_rotation(group_id)
        order = rotation_data.get("order", [])
        idx = rotation_data.get("current_index", 0)
        driver_family_id = order[idx] if order else ""
    if not driver_family_id:
        return jsonify({"ok": False, "error": "No driver assigned for this trip."})

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
    save_route_cache(result, driver_name=driver.name, dest_name=dest_name)

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


@app.route("/start-ride", methods=["POST"])
@login_required
def start_ride_route():
    group_id = gid()
    user = current_user()
    start_ride(driver_name=user.get("name", "Driver"), group_id=group_id)
    return jsonify({"ok": True})


@app.route("/start-return", methods=["POST"])
@login_required
def start_return_route():
    group_id = gid()
    user = current_user()
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
    lat, lng = data["lat"], data["lng"]
    update_location(lat=lat, lng=lng, group_id=group_id)
    trip_date = arrival_time(group_id).strftime("%Y-%m-%d")
    try:
        etas = compute_etas(lat, lng, trip_date, group_id)
    except Exception:
        etas = []
    loc = get_location(group_id)
    loc["etas"] = etas
    from location import _save as _save_location
    _save_location(loc, group_id)
    return jsonify({"ok": True})


@app.route("/get-location")
def get_location_route():
    group_id = request.args.get("group_id", "")
    if not group_id:
        return jsonify({"active": False})
    return jsonify(get_location(group_id))


@app.route("/bulletin")
def bulletin_legacy():
    """Backward-compat redirect — old bookmarks/links without group_id."""
    # Use the session group if available, else fall back to grp_main
    group_id = gid() or "grp_main"
    return redirect(url_for("bulletin", group_id=group_id))


@app.route("/bulletin/<group_id>")
def bulletin(group_id):
    if not get_group(group_id):
        return "Group not found", 404

    cfg = load_config(group_id)
    trip_time = arrival_time(group_id)
    rotation_data = _load_rotation(group_id)
    order = rotation_data.get("order", get_all_family_ids(group_id))
    current_index = rotation_data.get("current_index", 0)
    next_driver_id = order[current_index] if order else None
    next_driver_name = get_family(next_driver_id, group_id).name if next_driver_id else "—"
    default_dest = get_destination(get_destination_id(group_id))
    dest_name = cfg.get("destination_name") or default_dest.name

    schedule = load_schedule(group_id)
    rotation = []
    for i, fid in enumerate(order):
        family = get_family(fid, group_id)
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
    f = _confirmations_file(group_id)
    return json.loads(f.read_text()) if f.exists() else {}

def _save_confirmations(data: dict, group_id: str) -> None:
    _confirmations_file(group_id).write_text(json.dumps(data, indent=2))

def _load_swap(group_id: str) -> dict:
    f = _swap_file(group_id)
    return json.loads(f.read_text()) if f.exists() else {}

def _save_swap(data: dict, group_id: str) -> None:
    _swap_file(group_id).write_text(json.dumps(data, indent=2))


def _phone_to_family_and_group(phone: str):
    """Find a family across all groups by phone number."""
    for group in list_groups():
        gid_val = group["id"]
        for fid in get_all_family_ids(gid_val):
            try:
                fam = get_family(fid, gid_val)
                if fam.guardians and fam.guardians[0].phone == phone:
                    return fam, gid_val
            except Exception:
                continue
    return None, None


def _handle_swap(from_phone: str, reason: str) -> str:
    from datetime import timezone, timedelta
    from rotation import next_driver as _next_driver
    family, group_id = _phone_to_family_and_group(from_phone)
    if not family:
        return "Sorry, your number isn't registered in this carpool."
    if family.id != _next_driver(group_id):
        return "You're not the driver for the next trip, so no swap needed."
    now = datetime.now(timezone.utc)
    trip_time = arrival_time(group_id)
    if now >= trip_time - timedelta(days=1):
        return "It's less than 1 day before the trip — too late to swap automatically. Please contact the admin directly."
    others = [get_family(fid, group_id) for fid in get_all_family_ids(group_id) if fid != family.id]
    asked = []
    for other in others:
        phone = other.guardians[0].phone if other.guardians else ""
        if phone:
            try:
                send_sms(to_phone=phone, message=(
                    f"{family.name} can't drive on {trip_time.strftime('%a %b %d')} ({reason}). "
                    f"Can you take over? Reply YES to volunteer."
                ))
                asked.append(phone)
            except Exception as e:
                logger.error("Failed to send swap request SMS to %s: %s", phone, e)
    _record_swap_req(family.id, family.name, group_id)
    _save_swap({"pending": True, "original_driver_id": family.id,
                "original_driver_phone": from_phone, "reason": reason,
                "asked_phones": asked, "confirmed_driver_id": None,
                "group_id": group_id}, group_id)
    return f"Got it! Asked {len(asked)} other drivers. We'll let you know who takes over."


def _handle_yes_for_swap(from_phone: str) -> str | None:
    # Check across all groups for a pending swap that asked this phone
    for group in list_groups():
        gid_val = group["id"]
        swap = _load_swap(gid_val)
        if not swap.get("pending"):
            continue
        if from_phone not in swap.get("asked_phones", []):
            continue
        if swap.get("confirmed_driver_id"):
            return "Thanks, but someone already volunteered to drive!"
        family, _ = _phone_to_family_and_group(from_phone)
        if not family:
            return None
        _set_driver(family.id, gid_val)
        swap["pending"] = False
        swap["confirmed_driver_id"] = family.id
        _save_swap(swap, gid_val)
        _record_swap_cover(family.id, family.name, gid_val)
        trip_time = arrival_time(gid_val)
        send_sms(to_phone=swap["original_driver_phone"],
                 message=f"Great news! {family.name} will drive on {trip_time.strftime('%a %b %d')}. You're off the hook!")
        for phone in swap["asked_phones"]:
            if phone != from_phone:
                send_sms(to_phone=phone,
                         message=f"{family.name} has volunteered to drive on {trip_time.strftime('%a %b %d')}. No need to respond.")
        return f"You're confirmed as driver on {trip_time.strftime('%a %b %d')}! You'll get route details closer to the trip."
    return None


@app.route("/sms", methods=["POST"])
def sms_webhook():
    # Verify the request genuinely came from Twilio.
    # Railway sits behind a TLS-terminating proxy, so request.url may arrive
    # as http:// — we reconstruct the https:// URL that Twilio signed.
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if auth_token:
        validator = RequestValidator(auth_token)
        url = request.url.replace("http://", "https://", 1)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validator.validate(url, request.form, signature):
            logger.warning("Rejected SMS webhook: invalid Twilio signature from %s", request.remote_addr)
            return Response("Forbidden", status=403)

    from_number = request.form.get("From", "")
    raw_body = request.form.get("Body", "").strip()
    upper = raw_body.upper()
    logger.info("SMS received from %s: %r", from_number, raw_body)

    reply = None
    if upper == "YES":
        reply = _handle_yes_for_swap(from_number)
        if reply is None:
            # Store confirmation in whatever group this phone belongs to
            _, group_id = _phone_to_family_and_group(from_number)
            if group_id:
                confs = _load_confirmations(group_id)
                confs[from_number] = raw_body
                _save_confirmations(confs, group_id)
            reply = "Got it, confirmed!"
    elif upper.startswith("SWAP"):
        reason = raw_body[4:].strip() or "no reason given"
        reply = _handle_swap(from_number, reason)
    else:
        _, group_id = _phone_to_family_and_group(from_number)
        if group_id:
            confs = _load_confirmations(group_id)
            confs[from_number] = raw_body
            _save_confirmations(confs, group_id)
        reply = f"Got it! We'll pick you up at: {raw_body}"

    if reply:
        try:
            send_sms(to_phone=from_number, message=reply)
        except Exception as e:
            logger.error("Failed to send SMS reply to %s: %s", from_number, e)

    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(twiml, mimetype="text/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
