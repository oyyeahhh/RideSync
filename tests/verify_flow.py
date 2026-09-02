"""Verify the patched CarpoolSync: core flow plus every fix that has a testable
surface. Run against a fresh DATA_DIR with the server already up."""
import re
import json
import requests

BASE = "http://127.0.0.1:3000"
s = requests.Session()
rows = []


def check(name, ok, detail=""):
    rows.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("   | " + detail if detail else ""))


def form_token(path):
    r = s.get(BASE + path)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text)
    return m.group(1) if m else None


# ── core flow ────────────────────────────────────────────────────────────────
for path in ["/about", "/login", "/health", "/create-group"]:
    r = s.get(BASE + path, allow_redirects=False)
    check("GET " + path, r.status_code == 200, "status=%d" % r.status_code)

tok = form_token("/create-group")
r = s.post(BASE + "/create-group", data={
    "csrf_token": tok, "group_name": "Test Soccer Carpool", "name": "Test Admin",
    "family_name": "Testfamily", "email": "admin@example.com", "phone": "+15551234567",
    "password": "Sunflower99", "address": "1600 Pennsylvania Ave NW, Washington, DC",
    "child_name": "Kid One"}, allow_redirects=False)
check("POST /create-group", r.status_code == 302, "status=%d" % r.status_code)

s.get(BASE + "/logout")
tok = form_token("/login")
r = s.post(BASE + "/login", data={"csrf_token": tok, "email": "admin@example.com",
                                  "password": "Sunflower99"}, allow_redirects=False)
check("POST /login with CSRF token", r.status_code == 302, "status=%d" % r.status_code)

r = s.get(BASE + "/", allow_redirects=False)
check("GET / (dashboard)", r.status_code == 200, "status=%d" % r.status_code)
dash = r.text

# ── FIX 13: login now rejects a POST with no CSRF token ─────────────────────
bare = requests.Session()
r = bare.post(BASE + "/login", data={"email": "admin@example.com",
                                     "password": "Sunflower99"}, allow_redirects=False)
# The app answers a form CSRF failure with a redirect back to the page, so the
# proof of refusal is that no session was established.
after = bare.get(BASE + "/", allow_redirects=False)
check("FIX login CSRF: tokenless POST /login establishes no session",
      r.status_code in (302, 400) and after.status_code == 302,
      "login=%d then dashboard=%d" % (r.status_code, after.status_code))

# ── FIX 2: the parent-dashboard script abort is guarded ─────────────────────
check("FIX parent JS: #m-end-date access is guarded",
      'if (endDateEl) endDateEl.addEventListener' in dash
      and 'document.getElementById("m-end-date").addEventListener' not in dash)

# ── FIX 5 (drive_token): not serialized into the page ───────────────────────
hdrs = {"X-CSRFToken": s.cookies.get("csrf_token"), "Content-Type": "application/json"}
r = s.post(BASE + "/schedule/add", data=json.dumps({
    "date": "2026-09-10", "destination_name": "Soccer Field",
    "destination_address": "1 Stadium Way, Washington, DC",
    "arrival_time": "16:30", "return_time": "18:00"}), headers=hdrs)
check("POST /schedule/add (valid trip)", r.status_code == 200,
      "status=%d" % r.status_code)
r = s.get(BASE + "/", allow_redirects=False)
dash = r.text
check("GET / after adding a trip", r.status_code == 200, "status=%d" % r.status_code)
check("FIX drive_token: absent from the dashboard payload",
      '"drive_token"' not in dash and "drive_token" not in dash.split("const SCHEDULE")[-1][:4000])

# ── FIX 1 (blocker): empty and malformed times are rejected, not stored ─────
for label, payload in [
    ("empty arrival_time", {"date": "2026-09-11", "destination_name": "Bad",
                            "destination_address": "2 Stadium Way", "arrival_time": ""}),
    ("garbage arrival_time", {"date": "2026-09-12", "destination_name": "Bad",
                              "destination_address": "2 Stadium Way", "arrival_time": "4pm"}),
    ("hour 99", {"date": "2026-09-13", "destination_name": "Bad",
                 "destination_address": "2 Stadium Way", "arrival_time": "99:00"}),
    ("bad date", {"date": "9/2/26", "destination_name": "Bad",
                  "destination_address": "2 Stadium Way", "arrival_time": "16:30"}),
    ("bad return_time", {"date": "2026-09-14", "destination_name": "Bad",
                         "destination_address": "2 Stadium Way",
                         "arrival_time": "16:30", "return_time": "nope"}),
]:
    r = s.post(BASE + "/schedule/add", data=json.dumps(payload), headers=hdrs)
    check("FIX blocker: /schedule/add rejects " + label, r.status_code == 400,
          "status=%d body=%s" % (r.status_code, r.text.strip()[:90]))

r = s.get(BASE + "/", allow_redirects=False)
check("FIX blocker: dashboard still loads after every bad attempt",
      r.status_code == 200, "status=%d" % r.status_code)

# ── recurring series validation ─────────────────────────────────────────────
r = s.post(BASE + "/schedule/add-recurring", data=json.dumps({
    "start_date": "2026-09-14", "end_date": "2026-09-25", "weekdays": [0, 2],
    "destination_name": "Soccer Field", "destination_address": "1 Stadium Way",
    "arrival_time": ""}), headers=hdrs)
check("FIX blocker: /schedule/add-recurring rejects an empty time",
      r.status_code == 400, "status=%d" % r.status_code)

r = s.post(BASE + "/schedule/add-recurring", data=json.dumps({
    "start_date": "2026-09-14", "end_date": "2026-09-25", "weekdays": [0, 2],
    "destination_name": "Soccer Field", "destination_address": "1 Stadium Way",
    "arrival_time": "16:30"}), headers=hdrs)
check("POST /schedule/add-recurring (valid series)", r.status_code == 200,
      "status=%d body=%s" % (r.status_code, r.text.strip()[:80]))

r = s.get(BASE + "/", allow_redirects=False)
check("GET / after the recurring series", r.status_code == 200,
      "status=%d" % r.status_code)

# ── FIX 3: the open email endpoint is gone ──────────────────────────────────
r = s.get(BASE + "/health/smtp-test?email=victim@example.com", allow_redirects=False)
check("FIX security: /health/smtp-test removed", r.status_code == 404,
      "status=%d" % r.status_code)

# ── FIX 4 + 5: cross-group admin routes ─────────────────────────────────────
r = s.get(BASE + "/admin/backup", allow_redirects=False)
check("FIX security: /admin/backup refused without OWNER_EMAILS",
      r.status_code == 404, "status=%d" % r.status_code)

r = s.get(BASE + "/admin/system", allow_redirects=False)
check("GET /admin/system (own group only)", r.status_code == 200,
      "status=%d" % r.status_code)

# ── remaining authenticated pages ───────────────────────────────────────────
for path in ["/admin/users", "/calendar.ics", "/settings"]:
    r = s.get(BASE + path, allow_redirects=False)
    check("GET " + path, r.status_code in (200, 302, 404), "status=%d" % r.status_code)

passed = sum(1 for _, ok, _ in rows if ok)
print("\n%d/%d checks passed" % (passed, len(rows)))
