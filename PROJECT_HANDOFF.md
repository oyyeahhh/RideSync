# CarpoolSync — Project Handoff Doc

You are picking up work on **CarpoolSync**, a Flask web app that coordinates kid carpools for small groups of families. This doc has everything you need to be useful from message one.

---

## TL;DR — 60-second context

- **Owner / user:** Orly Nadler (`orlyn8@gmail.com`). Solo founder / single person at the wheel. Non-engineer but technically literate.
- **What it does:** Replaces parents' group-chat chaos with smart driver rotation, optimized pickup routes, WhatsApp invites, a live status board for kids' iPads, and "be ready by 4:42pm" countdowns.
- **Tech stack:** Python 3 + Flask, gunicorn, deployed on Railway. Twilio for WhatsApp. Google Maps API for geocoding + routes. Currently migrating auth + data to **Supabase**.
- **Repo:** `https://github.com/oyyeahhh/RideSync` — local at `/Users/orlynadler/Desktop/Carpool`
- **Production URL:** `https://carpoolsync.up.railway.app`
- **Brand / mascot:** "Carpool, on autopilot" — Tesla illustration with kids waving (file: `static/tesla.png`)
- **Current state:** Working but recently brittle. Auth has been rebuilt several times. Supabase Auth migration is built behind a feature flag (`USE_SUPABASE_AUTH=1`) and ready to enable.

---

## 1. What the product is

CarpoolSync is a small-team coordinator for parents who drive their kids to the same destination — soccer, school, music lessons. The pain it solves: nobody wants to track whose turn it is to drive in a group chat. The app does that, plus:

- Picks the next driver from a fair rotation
- Skips drivers marked absent
- Optimizes the pickup route via Google Maps
- SMSes the driver their route 2 hours before
- Sends "running late" alerts in one tap
- Shows a kid-friendly bulletin board for the kitchen iPad
- Tracks miles/hours per family

It's designed for ~3–10 families per group. Orly is the first user; she plans to soft-launch with 1–2 trusted families, then expand.

## 2. Tech stack

| Layer | What |
|---|---|
| **Backend** | Flask (Python 3.11+), `flask-session`, `flask-wtf` (CSRF) |
| **Server** | gunicorn on Railway |
| **Storage (legacy)** | JSON files on Railway persistent volume at `/data` |
| **Storage (in progress)** | Supabase Postgres — schema defined, migration pending |
| **Auth (legacy)** | bcrypt + filesystem sessions |
| **Auth (new, dormant)** | Supabase Auth email + password — built behind `USE_SUPABASE_AUTH` env var |
| **Background jobs** | APScheduler — every 15 min route check + nightly 11pm rotation advance |
| **SMS / WhatsApp** | Twilio sandbox (`+1 415 523 8886`, join keyword `material-burn`) |
| **Routing / geocoding** | Google Maps Platform |
| **Calendar** | iCal feed at `/calendar/<token>.ics` (cookieless, per-user opaque token) |

## 3. Repo layout

```
Carpool/
├── portal.py                 # Flask app, ALL routes (single big file ~1900 LOC)
├── auth.py                   # Legacy bcrypt-based user/invite/reset management
├── auth_supabase.py          # NEW Supabase Auth helpers (signup, signin, magic link)
├── supabase_client.py        # Supabase SDK client singletons (anon + service)
├── storage.py                # Atomic JSON writes + file locking (legacy)
├── groups.py                 # Per-group registry (groups.json)
├── families.py               # Family + guardians + kids
├── schedule.py               # Upcoming trips
├── trips.py                  # Trip history log
├── rotation.py               # Driver rotation cursor
├── absences.py karma.py      # Per-day absences, fairness ledger
├── route_cache.py routing.py # Google Maps optimization + cache
├── route.py routing.py       # WAIT — route.py was deleted; just routing.py
├── geocode.py                # Address → lat/lng with caching
├── cal_feed.py               # ICS calendar generation
├── sms.py                    # Twilio wrappers (send_sms, send_route_sms)
├── location.py               # Live driver GPS state
├── config.py                 # Per-group trip_config (arrival time, destination, tz)
├── startup.py                # Seed copy from CODE_DIR → DATA_DIR (legacy)
├── models.py                 # Dataclasses for Family/Guardian/Kid/Address/Destination
├── supabase/
│   └── schema.sql            # CANONICAL Postgres schema (17 tables, idempotent)
├── templates/                # Jinja2 — see below
├── static/
│   ├── carpoolsynclogo.png   # Smiling-road logo
│   ├── tesla.png             # Mascot (kids in a Tesla) — bg already removed
│   └── background.png        # Aerial city background
├── AUTH_RECOVERY.md          # Recovery procedures (login bypass, password reset, etc.)
├── PROJECT_HANDOFF.md        # ← you are here
├── requirements.txt
├── Procfile                  # web: gunicorn -w 2 portal:app
└── railway.toml              # Railway deploy config
```

### Templates

- `dashboard.html` — main signed-in UI (~2100 LOC, all inline CSS+JS)
- `bulletin.html` — old internal bulletin (requires login)
- `kid_bulletin.html` — NEW public kitchen-iPad view (cookieless via `/display/<token>`)
- `login.html` — login page (recently cleaned up to remove autofill confusion)
- `create_group.html` — onboarding for the first admin
- `signup.html` — invited-family signup
- `welcome.html` — celebratory landing after creating a group (Tesla drives in + confetti)
- `forgot_password.html`, `reset_password.html`
- `admin_users.html` — user management (admin only)
- `admin_system.html` — full-system view: every user, group, storage status
- `about.html` — public marketing page at `/about`
- `404.html` — friendly with mascot

## 4. Current state of deployment

Production runs on Railway. Volume mounted at `/data`, env var `DATA_DIR=/data`.

### Env vars currently set in Railway

```
DATA_DIR                  = /data
SECRET_KEY                = <some-long-string>
TWILIO_ACCOUNT_SID        = ...
TWILIO_AUTH_TOKEN         = ...
TWILIO_FROM_NUMBER        = +1...
TWILIO_SANDBOX_KEYWORD    = material-burn
TWILIO_SANDBOX_NUMBER     = +14155238886
GOOGLE_MAPS_API_KEY       = ...
SUPABASE_URL              = https://xxx.supabase.co
SUPABASE_ANON_KEY         = sb_publishable_...
SUPABASE_SERVICE_ROLE_KEY = sb_secret_...    (secret!)
```

**Not set (would enable features if added):**
- `USE_SUPABASE_AUTH=1` → flips login to Supabase Auth (the next big step Orly is doing)
- `EMERGENCY_LOGIN_TOKEN=...` → enables `/emergency-login?token=...` bypass
- `EMERGENCY_RESET_EMAIL`, `EMERGENCY_RESET_PASSWORD` → resets a user's password on next boot

## 5. The active migration: Supabase

We're partway through migrating to Supabase. Status as of handoff:

| Phase | Status | Notes |
|---|---|---|
| 1a — Install SDK, `/health` connection check | ✅ Shipped | `supabase_client.py` + health endpoint reports connection state |
| 1b — Magic-link login alongside password | ✅ Shipped but unused | Magic link works but hits free-tier email rate limit (4/hr). Custom SMTP needed. |
| 1c — Email+password via Supabase Auth (behind flag) | ✅ Shipped, dormant | Set `USE_SUPABASE_AUTH=1` to enable |
| 1d — Schema for full data migration | ✅ Defined, not applied | `supabase/schema.sql` — 17 tables, idempotent, RLS off (we use service role) |
| 2a — User to run schema in Supabase | ⏸ Waiting on user | One-time paste-and-run in SQL Editor |
| 2b — Replace JSON modules with Supabase queries | ⏸ Not started | Module-by-module: users → groups → families → schedule → trips → … |
| 2c — Enable RLS + switch to anon client | 🔮 Future | Once auth is stable, lock down per-group reads/writes |

### Feature flag: `USE_SUPABASE_AUTH`

When `USE_SUPABASE_AUTH=1`:
- `/create-group` creates a Supabase Auth user first (`auth.admin.create_user` with `email_confirm=True`), then the local user record with `supabase_uid` linked
- `/login` verifies via `auth.sign_in_with_password`; falls back to bcrypt if Supabase is misconfigured
- Local `users.json` still stores name/phone/family_id/etc.; Supabase Auth stores just credentials

When unset or `0`: legacy bcrypt path runs as before. **Toggle is reversible with zero data loss** — both paths read/write the same `users.json`.

### What Orly needs to do (sequence, can be done now)
1. Supabase Dashboard → SQL Editor → paste `supabase/schema.sql` → Run
2. Supabase Dashboard → Authentication → Providers → Email → toggle **Confirm email OFF**
3. Railway → Variables → add `USE_SUPABASE_AUTH=1`
4. Open the app in Incognito → `/create-group` → make a fresh account
5. Log out → log back in (with "Keep me signed in" checked) — should work cleanly

## 6. Major features — fully built

### Multi-tenant groups
- Every entity has a `group_id`. JSON files live in `/data/groups/<group_id>/`.
- Top-level files: `users.json`, `groups.json`, `invites.json`, `geocode_cache.json`.
- `grp_main` is the legacy seed group; cleanup UI exists at `/admin/users` for "leftover groups."

### Invites via WhatsApp
- Admin enters a phone number (split into area code + local for safety) → app generates an invite token → Twilio sends a one-tap link.
- Recipients must text `join material-burn` to `+1 415 523 8886` first (Twilio sandbox requirement) — signup page has instructions inline.

### Rotation
- Per group: an ordered list of `family_id`s + a `current_index`.
- `next_driver(group_id, absent_ids=…)` returns who's up; `advance(group_id, absent_ids=…)` increments and skips absent.
- Nightly cron at 11pm auto-advances if `/arrived` wasn't tapped that day.

### Schedule
- Trips have `id`, `series_id` (for recurring), `date`, `arrival_time`, `return_time`, `destination_*`, `driver_*`.
- Recurring add generates a series with auto-cycled drivers (in auto mode), and persists the rotation cursor forward.

### Route auto-send
- Background job every 15 min checks for trips arriving in 2 hours.
- Computes optimal pickup order via Google Maps Routes API.
- SMS the driver their pickup list + a tap-to-navigate `https://www.google.com/maps/dir/?api=1&...` URL.
- `route_sent=True` flag prevents duplicate sends. **APScheduler is started under a `flock` so only one gunicorn worker runs it.**

### Live driver tracking
- Driver taps "Start Outbound" → browser geolocation → POSTs to `/update-location` every ~15s
- Public-readable on the Kid Bulletin (via display token) with a pulsing green "live" banner

### Kid Bulletin (kitchen iPad)
- Cookieless URL at `/display/<token>` (per-group opaque token in groups table)
- Auto-refreshes every 60s
- Big "Today's Driver" card, pickup list with live "in X min" countdowns, upcoming trips
- Admin gets the URL via dashboard → 📺 Kid Bulletin button (modal with copy/regenerate)

### Calendar feed
- Public, cookieless `/calendar/<token>.ics` (per-user calendar_token)
- Subscribe in Google/Apple Calendar with the URL; auto-syncs

### Admin tools (only visible to `role=admin`)
- `/admin/users` — manage members, reset passwords, dedupe by email (cross-group), delete leftover empty groups
- `/admin/system` — full DB view: every user, every group, storage status
- `/admin/cleanup-legacy-group` — wipe `grp_main` if it's empty

### Public marketing page
- `/about` — Tesla mascot hero, "Carpool, on autopilot" tagline, 16-feature grid, How It Works section, dark CTA at bottom

### Welcome flow
- After `/create-group` → redirect to `/welcome` (one-shot, session flag)
- Tesla drives in from off-screen, confetti rain, "🎉 Welcome to [group name]", 4-step quick-start checklist

## 7. Security model

- CSRF protection via Flask-WTF on all POST forms except `/sms` (Twilio webhook) and `/login` (login forms generally don't need CSRF and stale tokens caused real bugs)
- Rate limits (in-memory token bucket):
  - `/login`: 10/IP/5min, 8/email/5min
  - `/forgot-password`: 3/IP/10min, 2/email/10min, 5/email/day
  - `/auth/magic-link`: 5/IP/5min, 3/email/5min
- Twilio webhook signature verified via `RequestValidator`; rejects unsigned in prod
- Atomic JSON writes (`tempfile + os.replace + fsync`) and per-file `flock` to prevent races
- Group isolation: every admin action verifies `target.group_id == admin.group_id`
- Group ID regex validated in `storage.py:_validate_group_id` (defense vs. path traversal)
- Session cookies: `HttpOnly`, `SameSite=Lax`, `Secure` in production

## 8. The login saga (critical context — read this before changing auth)

We rebuilt login several times this past week. The user has been frustrated. Here's the history:

1. **Original**: bcrypt + Flask filesystem sessions on the volume.
2. **Discovery**: data files were tracked in git → every deploy wiped them. Fixed with `.gitignore` + atomic writes.
3. **Discovery**: filesystem sessions persisted on the volume but were fragile to SECRET_KEY changes and worker count.
4. **Bug class 1**: bcrypt verification failing after signup. Added post-signup verify check (`CRITICAL: signup persistence check failed` log line) and password stripping.
5. **Bug class 2**: CSRF tokens going stale on the login form. Caused "Bad Request" errors. Login form is now CSRF-exempt + we have a graceful CSRF error handler.
6. **Bug class 3**: `users.json` got wiped at one point (cause unclear — possibly a bad dedupe or atomic write). Cannot reliably reproduce.
7. **Bug class 4 (suspected)**: the login page used to have **two side-by-side `<form>` blocks both with `autocomplete="email"` inputs**. Chrome's password manager could fill the wrong one, making the password form appear "broken" even when the password was correct. **This was fixed in the last commit** — there's now a single email field, one primary "Sign In" button, magic-link is a secondary button that mirrors the email. This may have been the real root cause.
8. **Built but dormant**: Full Supabase Auth migration (Phase 1c) behind `USE_SUPABASE_AUTH=1` flag. Enabling it should kill the entire bug class.

**Recovery routes that exist** (see `AUTH_RECOVERY.md` for usage):
- `/emergency-login?token=<EMERGENCY_LOGIN_TOKEN-env-var>` — bypasses bcrypt, sets session directly
- `EMERGENCY_RESET_EMAIL + EMERGENCY_RESET_PASSWORD` env vars → resets password on next boot, logs `[EMERGENCY RESET]` diagnostics
- `/health` shows storage + Supabase status
- `/admin/system` shows every user across every group

## 9. Design system

| Token | Value | Used for |
|---|---|---|
| `--cream` | `#FAF9F5` | Primary background (auth pages, dashboard cards) |
| `--ink` | `#1A1714` | Body text |
| `--mid` | `#6E6862` | Secondary text |
| `--orange` | `#D4784A` | Accent, primary CTA hover |
| `--coral` | `#FF6B8A` | Warning / "running late" |
| `--cyan` | `#3EC9E8` | Success / live status |
| `--border` | `#EAE8E3` | Card borders |

Fonts:
- **Styrene A** — sans, body
- **Tiempos Text** — serif italic, headings + emphasis
- **Fredoka** — playful sans, only on the Kid Bulletin

Tagline: **"Carpool, on autopilot."** (italic-emphasis on "on autopilot")

Mascot: Tesla full of kids (`static/tesla.png`) with the background flood-filled to transparent. Used on: about page hero, kid bulletin hero, dashboard empty state ("Ready to roll?"), running-late modal, welcome page (drives in + bobbles), 404 page, about page footer (small wave).

## 10. How Orly works (style notes)

- Prefers **concise, plan-then-execute** messages. Lay out the plan in 3–5 bullet steps, then ship.
- Doesn't want hand-holding — fix the thing, summarize what changed, move on.
- Has been on a frustration cycle today. Lead with empathy when she reports a bug; lead with execution when she says "go."
- Skeptical of overengineering. SQL migration was right; Netlify swap was overkill.
- Wants the product real before opening to families. Will not tolerate the login disappointing a friend.
- **Prefers**: emojis in moderation, fewer words, tables for comparison, code blocks for env vars / paths.
- **Don't**: lecture, over-explain her own product back to her, add features she didn't ask for.

## 11. Day-to-day workflow

- Code is edited locally at `/Users/orlynadler/Desktop/Carpool`
- Push to GitHub `main` → Railway auto-deploys (~60–90s)
- No CI / tests yet (a known gap)
- We co-author commits as `Claude Sonnet 4.6 <noreply@anthropic.com>`
- Big migrations land on `main` with feature flags rather than separate branches

## 12. What to do next (in order of priority)

If Orly hasn't done it yet:

1. **Enable Supabase Auth** — 3 steps (run schema, toggle confirm-email off, set `USE_SUPABASE_AUTH=1`)
2. **Confirm login works** in Incognito after enabling — this is the proof point
3. **Soft-launch with 1–2 trusted families** — see what breaks in the real world
4. **Then** — start Phase 2 (data migration to Postgres):
   - users module first (already has `supabase_uid` link from Phase 1c)
   - groups, families
   - schedule, trips
   - rest

Skip:
- Switching to Netlify / Vercel (already explored; not a fit unless we rewrite to SPA)
- Adding tests right now (good idea, but lower priority than getting real users)
- Migrating to Postgres for ephemeral data (route_cache, location) — fine to keep as JSON

## 13. Open issues / known scuff

- The Tesla mascot's background removal was good but a faint edge sometimes shows on white backgrounds (acceptable)
- Backup story is hand-rolled — no automated daily snapshot of `/data` (would be nice; SQLite or Supabase eliminates this)
- The dedupe action can wipe users.json to 0 in some path we haven't fully traced (possible cause of the recent "no users" incident). Switching to Supabase Auth makes this irrelevant.
- Free-tier Supabase SMTP has a 4-email/hour limit; once magic-link UX matters, wire up Resend (instructions in `AUTH_RECOVERY.md`)
- Some recurring-schedule edge cases (mid-series driver swap) haven't been tested in production

---

## How to get oriented quickly

1. Read this file end-to-end (you just did).
2. Read `AUTH_RECOVERY.md` — short, has every "stuck" escape hatch.
3. Skim `supabase/schema.sql` — that's the canonical data model.
4. Skim the top of `portal.py` — the imports give you a tour of every module.
5. When Orly says "the login is broken again," **don't add another bandaid** — check whether `USE_SUPABASE_AUTH=1` is set; if not, ask her to enable it. That's the real fix.

Welcome to CarpoolSync. Take good care of her app.
