"""
Web dashboard. Run with:
    python3 portal.py

Then open http://localhost:3000 in your browser.
"""

import json
import os
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, Response, session, flash,
)
from flask_session import Session

from config import arrival_time, get_destination_id, load_config, save_config, get_group_name, get_assignment_mode, set_assignment_mode
from storage import DATA_DIR, CODE_DIR
from families import get_family, get_destination, get_all_family_ids, add_family
from rotation import _load as load_rotation, add_to_rotation
from trips import get_stats, load_trips
from schedule import load_schedule, add_trip, remove_trip, remove_series, add_recurring_trips, claim_trip
from karma import get_karma
from cal_feed import build_ics
from auth import (
    get_user_by_email, get_user_by_id,
    verify_password, create_user,
    generate_invite_token, verify_invite_token, mark_invite_used,
)
from sms import send_sms
from absences import toggle_absent, get_absences
from route_cache import load as load_route_cache
from location import start_ride, stop_ride, update_location, get_location
from eta import compute_etas

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")


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

def gcal_url(trip: dict) -> str:
    """Build a Google Calendar 'add event' URL for the outbound leg."""
    tz = ZoneInfo("America/New_York")
    arrival = datetime.strptime(f"{trip['date']} {trip['arrival_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)

    start = None
    route_cache = load_route_cache()
    if route_cache and route_cache.get("date") == trip["date"]:
        try:
            depart_str = route_cache["depart_at"]
            depart_naive = datetime.strptime(f"{trip['date']} {depart_str}", "%Y-%m-%d %I:%M %p")
            start = depart_naive.replace(tzinfo=tz)
        except (ValueError, KeyError):
            pass

    if start is None:
        cfg = load_config()
        start = arrival - timedelta(minutes=cfg.get("buffer_minutes", 10))

    pickup_start = start.strftime("%-I:%M %p")
    fmt = "%Y%m%dT%H%M%S"
    title = quote(f"{get_group_name()} — {trip['destination_name']} (There)")
    details = quote(
        f"Driver: {trip['driver_name']}\n"
        f"Pickup starts: {pickup_start}\n"
        f"Arrive by: {arrival.strftime('%-I:%M %p')}"
    )
    location = quote(f"{trip['destination_address']} ({get_group_name()})")
    dates = f"{start.strftime(fmt)}/{arrival.strftime(fmt)}"
    return f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={title}&dates={dates}&details={details}&location={location}"


def gcal_url_return(trip: dict) -> str:
    """Build a Google Calendar 'add event' URL for the return leg."""
    if not trip.get("return_time"):
        return ""
    tz = ZoneInfo("America/New_York")
    fmt = "%Y%m%dT%H%M%S"
    cfg = load_config()
    pickup = datetime.strptime(f"{trip['date']} {trip['return_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    arrive_home = pickup + timedelta(minutes=cfg.get("buffer_minutes", 10) + 20)
    driver = trip.get("return_driver_name") or trip.get("driver_name", "")
    title = quote(f"{get_group_name()} — {trip['destination_name']} (Return)")
    details = quote(
        f"Driver: {driver}\n"
        f"Pickup from {trip['destination_name']}: {pickup.strftime('%-I:%M %p')}"
    )
    location = quote(f"{trip['destination_address']} ({get_group_name()})")
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
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid email or password."

    return render_template("login.html", error=error, email=email)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
                    )
                    add_to_rotation(family["id"])
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
                )
                mark_invite_used(token)
                session["user_id"] = user["id"]
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
    )


# ── Invite route (admin only) ─────────────────────────────────────────────────

@app.route("/invite", methods=["POST"])
@login_required
@admin_required
def invite():
    group_name = get_group_name()
    if not group_name or group_name == "Carpool":
        return redirect(url_for("dashboard"))

    phone = request.form.get("phone", "").strip()
    if not phone:
        return redirect(url_for("dashboard"))

    # Normalize: ensure leading +
    if not phone.startswith("+"):
        phone = "+" + phone

    family_id = request.form.get("family_id", "").strip()
    family_name = ""
    if family_id:
        try:
            family_name = get_family(family_id).name
        except ValueError:
            family_id = ""

    token = generate_invite_token(phone, family_id=family_id, family_name=family_name)
    signup_url = url_for("signup", token=token, _external=True)
    message = (
        f"You've been invited to join {get_group_name()}!\n\n"
        f"Click the link below to create your account:\n{signup_url}"
    )
    whatsapp_ok = True
    try:
        send_sms(to_phone=phone, message=message)
    except Exception:
        whatsapp_ok = False

    return redirect(url_for("dashboard", invite_link=signup_url, invite_phone=phone, whatsapp_ok=int(whatsapp_ok)))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    cfg = load_config()
    trip_time = arrival_time()
    rotation_data = load_rotation()
    order = rotation_data.get("order", get_all_family_ids())
    current_index = rotation_data.get("current_index", 0)
    next_driver_id = order[current_index] if order else None

    rotation = []
    for fid in order:
        family = get_family(fid)
        rotation.append({"name": family.name, "id": fid, "is_next": fid == next_driver_id})

    next_driver_name = get_family(next_driver_id).name if next_driver_id else "—"
    default_dest = get_destination(get_destination_id())
    dest_name = cfg.get("destination_name") or default_dest.name
    if not cfg.get("destination_name"):
        cfg["destination_name"] = default_dest.name
    if not cfg.get("destination_address"):
        cfg["destination_address"] = default_dest.street

    stats = get_stats()
    max_minutes = max((s["minutes"] for s in stats.values()), default=1) or 1
    for s in stats.values():
        s["hours"] = s["minutes"] // 60
        s["mins"] = s["minutes"] % 60
        s["bar_pct"] = round(s["minutes"] / max_minutes * 100)

    raw_trips = load_trips()
    history = []
    for t in reversed(raw_trips):
        mins = t.get("minutes", 0)
        history.append({
            "date": t["date"],
            "driver": t["driver_name"],
            "duration": f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m",
            "pickups": len(t.get("pickups", [])),
        })

    schedule = load_schedule()
    for trip in schedule:
        trip["gcal_url"] = gcal_url(trip)
        trip["gcal_url_return"] = gcal_url_return(trip)

    families = [{"id": fid, "name": get_family(fid).name} for fid in get_all_family_ids()]
    raw_karma = {k["family_id"]: k for k in get_karma()}
    karma = []
    for fid in get_all_family_ids():
        if fid in raw_karma:
            karma.append(raw_karma[fid])
        else:
            karma.append({
                "family_id": fid,
                "name": get_family(fid).name,
                "requested": 0,
                "covered": 0,
                "score": 0,
            })

    trip_date = arrival_time().strftime("%Y-%m-%d")
    absences = get_absences(trip_date)
    pickup_families = []
    for fid in get_all_family_ids():
        if fid == next_driver_id:
            continue
        family = get_family(fid)
        pickup_families.append({
            "id": fid,
            "name": family.name,
            "absent": fid in absences,
        })

    invite_link = request.args.get("invite_link")
    invite_phone = request.args.get("invite_phone")
    whatsapp_ok = request.args.get("whatsapp_ok", "1") == "1"
    invite_status = None
    if invite_link:
        if whatsapp_ok:
            invite_status = f"Invite sent to {invite_phone} via WhatsApp. Signup link: {invite_link}"
        else:
            invite_status = f"WhatsApp failed. Share this link manually with {invite_phone}: {invite_link}"

    group_name = get_group_name()

    return render_template(
        "dashboard.html",
        group_name=group_name,
        current_user=user,
        next_trip={
            "date": trip_time.strftime("%A, %B %d, %Y"),
            "driver": next_driver_name,
            "destination": dest_name,
            "arrive_by": trip_time.strftime("%I:%M %p"),
            "return_time": cfg.get("return_time", ""),
            "return_driver": cfg.get("return_driver_name", ""),
        },
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
        live_location=get_location(),
        maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        assignment_mode=get_assignment_mode(),
    )


# ── Trip config (admin only) ──────────────────────────────────────────────────

@app.route("/save-trip", methods=["POST"])
@login_required
@admin_required
def save_trip():
    cfg = load_config()
    cfg["arrival_date"] = request.form["arrival_date"]
    cfg["arrival_time"] = request.form["arrival_time"]
    cfg["return_time"] = request.form.get("return_time", "").strip()
    cfg["return_driver_family_id"] = request.form.get("return_driver_family_id", "").strip()
    cfg["return_driver_name"] = request.form.get("return_driver_name", "").strip()
    cfg["destination_name"] = request.form.get("destination_name", "").strip()
    cfg["destination_address"] = request.form.get("destination_address", "").strip()
    cfg["buffer_minutes"] = int(request.form.get("buffer_minutes", 5))
    if request.form.get("group_name", "").strip():
        cfg["group_name"] = request.form.get("group_name").strip()
    save_config(cfg)
    return redirect(url_for("dashboard"))


# ── Calendar ──────────────────────────────────────────────────────────────────

@app.route("/calendar.ics")
@login_required
def calendar_feed():
    ics = build_ics()
    return Response(ics, mimetype="text/calendar", headers={
        "Content-Disposition": "inline; filename=carpool.ics"
    })


# ── Schedule ──────────────────────────────────────────────────────────────────

def _rotation_pairs_from(start_index: int) -> list[tuple[str, str]]:
    """Return (family_id, name) pairs for the rotation, starting at start_index."""
    rot = load_rotation()
    order = rot.get("order", [])
    if not order:
        return []
    pairs = [(fid, get_family(fid).name) for fid in order]
    return pairs[start_index:] + pairs[:start_index]


@app.route("/schedule/add", methods=["POST"])
@login_required
@admin_required
def schedule_add():
    data = request.json
    driver_fid = data.get("driver_family_id", "")
    driver_name = data.get("driver_name", "")

    # Auto mode: if driver not supplied, pull from rotation
    if get_assignment_mode() == "auto" and not driver_fid:
        rot = load_rotation()
        order = rot.get("order", [])
        idx = rot.get("current_index", 0)
        if order:
            driver_fid = order[idx]
            driver_name = get_family(driver_fid).name

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
    )
    trip["gcal_url"] = gcal_url(trip)
    trip["gcal_url_return"] = gcal_url_return(trip)
    return jsonify(trip)


@app.route("/schedule/remove/<trip_id>", methods=["POST"])
@login_required
@admin_required
def schedule_remove(trip_id):
    remove_trip(trip_id)
    return jsonify({"ok": True})


@app.route("/schedule/remove-series/<series_id>", methods=["POST"])
@login_required
@admin_required
def schedule_remove_series(series_id):
    count = remove_series(series_id)
    return jsonify({"ok": True, "removed": count})


@app.route("/schedule/set-assignment-mode", methods=["POST"])
@login_required
@admin_required
def schedule_set_assignment_mode():
    mode = request.json.get("mode", "auto")
    if mode not in ("auto", "manual"):
        return jsonify({"error": "invalid mode"}), 400
    set_assignment_mode(mode)
    return jsonify({"ok": True, "mode": mode})


@app.route("/schedule/claim/<trip_id>/<leg>", methods=["POST"])
@login_required
def schedule_claim(trip_id, leg):
    if leg not in ("outbound", "return"):
        return jsonify({"error": "invalid leg"}), 400
    user = current_user()
    family_id = user.get("family_id")
    if not family_id:
        return jsonify({"error": "no family linked to your account"}), 400
    family = get_family(family_id)
    trip = claim_trip(trip_id, leg, family_id, family.name)
    if not trip:
        return jsonify({"error": "trip not found"}), 404
    trip["gcal_url"] = gcal_url(trip)
    trip["gcal_url_return"] = gcal_url_return(trip)
    return jsonify({"ok": True, "trip": trip})


@app.route("/schedule/add-recurring", methods=["POST"])
@login_required
@admin_required
def schedule_add_recurring():
    data = request.json
    driver_fid = data.get("driver_family_id", "")
    driver_name = data.get("driver_name", "")
    auto_mode = get_assignment_mode() == "auto"

    # Build a sequence of drivers for auto mode (cycles through rotation)
    rot_sequence: list[tuple[str, str]] = []
    if auto_mode and not driver_fid:
        rot = load_rotation()
        idx = rot.get("current_index", 0)
        base = _rotation_pairs_from(idx)
        if base:
            rot_sequence = base  # will be indexed with modulo in loop below

    # Pre-compute per-trip drivers when using rotation sequence
    # (add_recurring_trips needs a single driver; we handle cycling here manually)
    if rot_sequence:
        from datetime import date as _date, timedelta as _td
        import uuid as _uuid
        from schedule import load_schedule as _ls, save_schedule as _ss
        weekdays = [int(d) for d in data.get("weekdays", [])]
        cur = _date.fromisoformat(data["start_date"])
        end = _date.fromisoformat(data["end_date"])
        series_id = str(_uuid.uuid4())[:12]
        rot_n = len(rot_sequence)
        trips_list = _ls()
        created = []
        rot_idx = 0
        while cur <= end:
            if cur.weekday() in weekdays:
                fid, fname = rot_sequence[rot_idx % rot_n]
                rot_idx += 1
                trip = {
                    "id": str(_uuid.uuid4())[:8],
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
            cur += _td(days=1)
        trips_list.sort(key=lambda t: t["date"])
        _ss(trips_list)
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
        )

    for trip in created:
        trip["gcal_url"] = gcal_url(trip)
        trip["gcal_url_return"] = gcal_url_return(trip)
    return jsonify({"ok": True, "trips": created, "count": len(created)})


@app.route("/toggle-absent", methods=["POST"])
@login_required
def toggle_absent_route():
    data = request.json or {}
    family_id = data.get("family_id")
    user = current_user()
    if user.get("role") != "admin" and user.get("family_id") != family_id:
        return jsonify({"error": "forbidden"}), 403
    trip_date = arrival_time().strftime("%Y-%m-%d")
    now_absent = toggle_absent(trip_date, family_id)
    return jsonify({"absent": now_absent})


@app.route("/running-late", methods=["POST"])
@login_required
def running_late():
    data = request.json or {}
    minutes = data.get("minutes", "")
    cfg = load_config()
    trip_date = arrival_time().strftime("%Y-%m-%d")
    absences = get_absences(trip_date)
    rotation_data = load_rotation()
    order = rotation_data.get("order", get_all_family_ids())
    current_index = rotation_data.get("current_index", 0)
    driver_id = order[current_index] if order else None
    driver_name = get_family(driver_id).name if driver_id else "The driver"

    msg = f"⏰ {driver_name} is running late"
    if minutes:
        msg += f" (~{minutes} min)"
    msg += ". Hang tight!"

    for fid in get_all_family_ids():
        if fid == driver_id or fid in absences:
            continue
        family = get_family(fid)
        try:
            send_sms(to_phone=family.guardians[0].phone, message=msg)
        except Exception:
            pass

    return jsonify({"ok": True})


@app.route("/arrived", methods=["POST"])
@login_required
def arrived():
    cfg = load_config()
    dest_name = cfg.get("destination_name", "the destination")
    trip_date = arrival_time().strftime("%Y-%m-%d")
    absences = get_absences(trip_date)
    rotation_data = load_rotation()
    order = rotation_data.get("order", get_all_family_ids())
    current_index = rotation_data.get("current_index", 0)
    driver_id = order[current_index] if order else None
    driver_name = get_family(driver_id).name if driver_id else "The driver"

    msg = f"✅ Kids have arrived safely at {dest_name}! Thanks {driver_name}!"

    for fid in get_all_family_ids():
        if fid == driver_id or fid in absences:
            continue
        family = get_family(fid)
        try:
            send_sms(to_phone=family.guardians[0].phone, message=msg)
        except Exception:
            pass

    return jsonify({"ok": True})


@app.route("/start-ride", methods=["POST"])
@login_required
def start_ride_route():
    user = current_user()
    start_ride(driver_name=user.get("name", "Driver"))
    return jsonify({"ok": True})


@app.route("/start-return", methods=["POST"])
@login_required
def start_return_route():
    user = current_user()
    start_ride(driver_name=user.get("name", "Driver"), trip_leg="return")
    return jsonify({"ok": True})


@app.route("/stop-ride", methods=["POST"])
@login_required
def stop_ride_route():
    stop_ride()
    return jsonify({"ok": True})


@app.route("/update-location", methods=["POST"])
@login_required
def update_location_route():
    data = request.json or {}
    lat, lng = data["lat"], data["lng"]
    update_location(lat=lat, lng=lng)
    trip_date = arrival_time().strftime("%Y-%m-%d")
    try:
        etas = compute_etas(lat, lng, trip_date)
    except Exception:
        etas = []
    loc = get_location()
    loc["etas"] = etas
    from location import _save as save_location
    save_location(loc)
    return jsonify({"ok": True})


@app.route("/get-location")
def get_location_route():
    return jsonify(get_location())


@app.route("/bulletin")
def bulletin():
    cfg = load_config()
    trip_time = arrival_time()
    rotation_data = load_rotation()
    order = rotation_data.get("order", get_all_family_ids())
    current_index = rotation_data.get("current_index", 0)
    next_driver_id = order[current_index] if order else None
    next_driver_name = get_family(next_driver_id).name if next_driver_id else "—"
    default_dest = get_destination(get_destination_id())
    dest_name = cfg.get("destination_name") or default_dest.name

    schedule = load_schedule()
    rotation = []
    for i, fid in enumerate(order):
        family = get_family(fid)
        rotation.append({
            "name": family.name,
            "is_next": fid == next_driver_id,
            "position": i + 1,
        })

    route_cache = load_route_cache()
    live_location = get_location()

    return render_template(
        "bulletin.html",
        maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        live_location=live_location,
        group_name=get_group_name(),
        route_cache=route_cache,
        next_trip={
            "date": trip_time.strftime("%A, %B %d, %Y"),
            "date_raw": trip_time.strftime("%Y-%m-%d"),
            "driver": next_driver_name,
            "destination": dest_name,
            "arrive_by": trip_time.strftime("%I:%M %p"),
            "return_time": cfg.get("return_time", ""),
            "return_driver": cfg.get("return_driver_name", ""),
        },
        schedule=schedule,
        rotation=rotation,
    )


# ── SMS Webhook (Twilio) ──────────────────────────────────────────────────────
# Handles inbound SMS replies: YES confirmations, SWAP requests, swap volunteers.

import json as _json
from pathlib import Path as _Path
from rotation import set_driver as _set_driver
from karma import record_swap_request as _record_swap_req, record_swap_cover as _record_swap_cover
from config import ADMIN_PHONE

CONFIRMATIONS_FILE = DATA_DIR / "confirmations.json"
SWAP_FILE = DATA_DIR / "swap_state.json"


def _load_confirmations() -> dict:
    return _json.loads(CONFIRMATIONS_FILE.read_text()) if CONFIRMATIONS_FILE.exists() else {}

def _save_confirmations(data: dict) -> None:
    CONFIRMATIONS_FILE.write_text(_json.dumps(data, indent=2))

def _load_swap() -> dict:
    return _json.loads(SWAP_FILE.read_text()) if SWAP_FILE.exists() else {}

def _save_swap(data: dict) -> None:
    SWAP_FILE.write_text(_json.dumps(data, indent=2))

def _phone_to_family(phone: str):
    from families import get_all_family_ids, get_family
    for fid in get_all_family_ids():
        fam = get_family(fid)
        if fam.guardians and fam.guardians[0].phone == phone:
            return fam
    return None

def _handle_swap(from_phone: str, reason: str) -> str:
    from datetime import datetime, timezone, timedelta
    from rotation import next_driver as _next_driver
    family = _phone_to_family(from_phone)
    if not family:
        return "Sorry, your number isn't registered in this carpool."
    if family.id != _next_driver():
        return "You're not the driver for the next trip, so no swap needed."
    now = datetime.now(timezone.utc)
    trip_time = arrival_time()
    if now >= trip_time - timedelta(days=1):
        return "It's less than 1 day before the trip — too late to swap automatically. Please contact the admin directly."
    from families import get_all_family_ids, get_family
    others = [get_family(fid) for fid in get_all_family_ids() if fid != family.id]
    asked = []
    for other in others:
        phone = other.guardians[0].phone if other.guardians else ""
        if phone:
            send_sms(to_phone=phone, message=(
                f"{family.name} can't drive on {trip_time.strftime('%a %b %d')} ({reason}). "
                f"Can you take over? Reply YES to volunteer."
            ))
            asked.append(phone)
    _record_swap_req(family.id, family.name)
    _save_swap({"pending": True, "original_driver_id": family.id,
                "original_driver_phone": from_phone, "reason": reason,
                "asked_phones": asked, "confirmed_driver_id": None})
    return f"Got it! Asked {len(asked)} other drivers. We'll let you know who takes over."

def _handle_yes_for_swap(from_phone: str) -> str | None:
    swap = _load_swap()
    if not swap.get("pending"):
        return None
    if from_phone not in swap.get("asked_phones", []):
        return None
    if swap.get("confirmed_driver_id"):
        return "Thanks, but someone already volunteered to drive!"
    family = _phone_to_family(from_phone)
    if not family:
        return None
    _set_driver(family.id)
    swap["pending"] = False
    swap["confirmed_driver_id"] = family.id
    _save_swap(swap)
    _record_swap_cover(family.id, family.name)
    trip_time = arrival_time()
    send_sms(to_phone=swap["original_driver_phone"],
             message=f"Great news! {family.name} will drive on {trip_time.strftime('%a %b %d')}. You're off the hook!")
    for phone in swap["asked_phones"]:
        if phone != from_phone:
            send_sms(to_phone=phone,
                     message=f"{family.name} has volunteered to drive on {trip_time.strftime('%a %b %d')}. No need to respond.")
    return f"You're confirmed as driver on {trip_time.strftime('%a %b %d')}! You'll get route details closer to the trip."


@app.route("/sms", methods=["POST"])
def sms_webhook():
    from_number = request.form.get("From", "")
    raw_body = request.form.get("Body", "").strip()
    upper = raw_body.upper()

    reply = None
    if upper == "YES":
        reply = _handle_yes_for_swap(from_number)
        if reply is None:
            confs = _load_confirmations()
            confs[from_number] = raw_body
            _save_confirmations(confs)
            reply = "Got it, confirmed!"
    elif upper.startswith("SWAP"):
        reason = raw_body[4:].strip() or "no reason given"
        reply = _handle_swap(from_number, reason)
    else:
        confs = _load_confirmations()
        confs[from_number] = raw_body
        _save_confirmations(confs)
        reply = f"Got it! We'll pick you up at: {raw_body}"

    if reply:
        send_sms(to_phone=from_number, message=reply)

    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(twiml, mimetype="text/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
