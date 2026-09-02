# Launch Plan

What it takes to make CarpoolSync a professional, phone-first product with the
small details worked out. Companion to REPAIR_PLAN.md, which covers what is
broken today. This covers the distance from a working prototype to a product a
stranger's family would trust with their children's names, address and live
location. Audit of `main` at `9d76e5d`, 2 September 2026.

Rough total: 45 working days for one person with an AI pair, plus one to three
weeks of carrier approval that should start on day one.

## What it is today

A well-built scheduler for one carpool, run by one admin, on one phone number
per family. One guardian per family. One address. One group per person. One
admin, forever. Absences only for the next trip. Messaging depends on the
Twilio WhatsApp sandbox, so a parent receives nothing until they text a join
keyword, and their opt-in silently expires after 72 hours.

Measured on a 390 by 844 viewport: both dashboards overflow horizontally by
more than 200 px; the admin dashboard has 39 tap targets under 40 px and 175
text elements under 12 px; the parent dashboard is about five screens tall; 44
inputs across the app have no associated label; there are zero focus-visible
styles and 116 emoji used as icons. The drive page, kid bulletin and 404 page
fit the screen. `tests/mobile_walk.py` reproduces these numbers.

## Start today

1. Register for A2P 10DLC or toll-free verification on Twilio. One to three
   weeks; everything in Track 2 waits on it. Choose SMS over WhatsApp Business.
2. Fix the two CSS lines that break the phone layout:
   `grid-template-columns: repeat(7, minmax(0, 1fr))` on `.cal-grid` and
   `min-width: 0` on `.cal-trip-dot` (dashboard.html:449-502). Cap the logo at
   40 px on phones and replace the 1 MB PNG with an SVG (dashboard.html:710).
3. Draft the privacy policy and terms and send them for review.
4. Pin the build: `.python-version`, `requirements.lock` with hashes, install
   from the lock. Unpinned floors are how the Supabase client switched to PKCE
   under the app and broke password reset.
5. Deploy the repair branch with `OWNER_EMAILS` set, then move the Procfile to
   `--workers 1 --threads 8 -k gthread`.

## Track 1. Phone-first front end (about 10 days)

- **Blocker.** Make the dashboard fit a phone (above). 1 hour.
- **Blocker.** Branch the dashboard by role. Hide driver buttons unless the
  viewer is the active driver or an admin; other families' attendance is
  read-only. Rename "Today's trip" to the real date: the card is built from the
  next trip (portal.py:1985-2031) and the absence toggle writes to that date
  (portal.py:2513-2522), so "not coming" on Tuesday changes next week. Move
  Stats, Karma, History, Trip Settings, Invite and User Management behind an
  admin Manage view. Hide empty sections. 1 day.
- **High.** Collapse the schedule: group recurring series, show the next three
  instances, week strip instead of month grid on phones, drop the per-leg
  Google Cal links in favour of the existing Subscribe action. Fix the tab bar
  (z-index below the Add Trip sheet, hard-coded active state, Rotation tab
  scrolls to the wrong card). dashboard.html:1430-1485, 681-705, 2141-2153.
  1 day.
- **High.** Base template and tokens. Today: no shared CSS, 99 hex colours, 17
  radii, 21 shadows, 20 type sizes, five primary-button styles. Target: about
  12 colours, 4 radii, 3 shadows, 6 sizes, 4 button classes at 44 px. Self-host
  real fonts; Styrene A and Tiempos Text are not on Google Fonts and every page
  falls back to Georgia. Label associations (only login.html has any), focus
  rings, an h1 per page, SVG icons in place of emoji. 2 days.
- **High.** Replace the 36 native dialogs (24 in dashboard.html, 9 in
  drive.html, 3 in admin_users.html) with a toast, a bottom sheet with focus
  trap and Escape, inline field errors, pending states on every submit.
  Server strings like "forbidden" and raw exception text must never reach a
  parent. The recurring add path (dashboard.html:1681-1686) never checks the
  response. 1 day.
- **High.** Contrast and size. Card labels measure 2.9:1 to 3.8:1, white on
  the coral gradient 2.1:1 to 2.7:1; AA needs 4.5:1. The arrive-by time is
  11 px grey. Minimum 12 px labels, 14 to 15 px body, 44 px targets.
- **Medium.** Installable: manifest, icons, theme-color, `viewport-fit=cover`
  (without it the tab bar's safe-area padding is zero), service worker caching
  the shell and the drive page. Prerequisite for web push. 3 days incl. push.
- **Medium.** Form details: single `type=tel` invite field with autocomplete,
  `street-address` on address fields, 16 px inputs to stop iOS zoom, geocode
  on save with a "we found" confirmation. Half a day.
- **Medium.** Rebuild about.html on a fluid grid; the vh-positioned poster
  layout leaves a screen of empty yellow on phones. 2 days.

## Track 2. Messages that arrive (about 6 days plus the wait)

- **Blocker.** Retire the WhatsApp sandbox (sms.py:23 prefixes `whatsapp:` on
  everything). Invites, reminders, late alerts and cancellations to SMS on
  your own verified number; invites, resets and cancellations also to email
  (Resend or Postmark). Remove the join-keyword copy from signup.html and
  welcome.html. 2 to 3 days of code.
- **Blocker.** Make "sent" mean sent. Twilio returns "queued" and the app
  reports delivered (portal.py:1782, dashboard.html:2036, drive.html:224 and
  239). Add a status callback, record every outbound message and its state,
  show an honest state with a copy-the-link fallback. Stop marking the route
  sent before the send (portal.py:3262). 1 to 2 days.
- **High.** Web push for on-the-way, in-the-car, arrived and ETA. 3 days.
- **High.** Messages parents expect and do not get: night-before note to every
  family, morning driver reminder, driver on the way with the ETA the app
  already computes, trip added or driver changed (schedule_update at
  portal.py:2290 sends nothing), anything for the return leg. Per-family
  preferences. 2 days.
- **Medium.** The webhook replies "Got it! We'll pick you up at:" to a texted
  address that is stored (portal.py:2966) and never read. Remove the promise.

## Track 3. Household and schedule model (about 13 days)

- **Blocker.** Absences for any date via a per-trip attendance list. 1 day.
- **High.** Guardians as a list with phone, email, `is_driver`, notification
  preference, and fan-out to all of them. Today a second parent gets a login
  and never a message (families.py:44-52); every family joins the rotation at
  signup (portal.py:1556, 1695). 2 days incl. migration.
- **High.** Driver-out flow: open the slot, notify, one-tap claim on the
  dashboard. Today the only path is SWAP by text more than 24 hours ahead, and
  a driver marking themselves absent changes nothing (portal.py:3206, 3350).
  2 to 3 days.
- **High.** Series editing: this and following, date-range skips, moveable
  dates. remove_series deletes past occurrences too (schedule.py:66). 2 days.
- **High.** Admin promote, demote and transfer (role is set once at
  portal.py:1562 and 1704). Half a day.
- **High.** Return-leg parity: route, reminder, check-ins, arrival, calendar
  event, ETA. cal_feed.py emits outbound only. 1 week.
- **Medium.** Join a second group (memberships exists, db_identity.py:62 keeps
  the first row; signup rejects any known email at portal.py:1674), leave a
  group, change email. 1 week plus 1 day.
- **Later.** Car capacity and split households.

## Track 4. Trust (about 5 days plus review)

- **Blocker.** Privacy policy, terms, consent checkbox at signup and
  create-group, sub-processors named. 2 to 3 days incl. review.
- **Blocker.** Self-service deletion with full cascade. Admin delete leaves
  the family record and the Supabase auth user behind (portal.py:3617, 3440).
  1 day.
- **High.** The bulletin URL is a bearer credential to children's names and
  live GPS and is embedded in every member's dashboard (dashboard.html:2043,
  2068). Regenerate for it and for the per-user calendar token. 1 day.
- **Medium.** Retention job and per-group export. 1 day.
- **Medium.** created_by, updated_at on trips and a group activity list. 1 day.

## Track 5. Engineering floor (about 10 days)

- **Blocker.** CI on every PR (ruff, pytest, pip-audit), branch protection,
  Railway wait-for-CI, PR environments on a free-tier Supabase project.
  Rewrite QUICKSTART to stop using production as the practice sandbox. 1 day.
- **High.** Lock file and Python pin. Installed bcrypt is 5.0, which raises on
  passwords over 72 bytes; auth.py:81 has no guard. 2 hours.
- **High.** pytest with a temp DATA_DIR fixture and the test client; port the
  27 checks; a smoke test over every GET route as anonymous and admin; unit
  tests for rotation, recurring expansion, cal_feed, the update_json
  concurrency case. 2 days.
- **High.** Request id on the 500 page, JSON logs, gunicorn access log, Sentry
  free tier, a cheap `/healthz` with a scheduler heartbeat, an uptime monitor.
  1 day.
- **High.** One settings module validating 24 env vars at boot (.env.example
  documents one), a complete example file, a rotation runbook. Half a day.
- **High.** Nightly backup to object storage and one rehearsed restore. Half a
  day.
- **Medium.** Numbered migrations with a schema_migrations table and a
  pre-deploy step. 1 day.
- **Medium.** App factory and blueprints verified by the smoke test, then the
  scheduler as a Railway cron service. 1 week, spread out.
- **Medium.** One operations document replacing the three handoff docs, with a
  `/healthz` that reports git SHA, schema version and flag state. 1 day.

## Sequence

- **Week 1, foundation.** Day one items above. Then pytest, smoke test, CI,
  branch protection, PR environments, settings module, request ids, Sentry,
  healthz, uptime monitor, nightly backup.
- **Week 2, the phone.** Role-branched dashboard, collapsed schedule, tab bar,
  base template and tokens, fonts, 44 px targets, labels, focus rings, SVG
  icons, toast and bottom sheet.
- **Week 3, households.** Guardians list, absences for any date, admin
  transfer, self-service deletion, token regeneration. Email delivery live.
- **Week 4, messaging.** Cut over to SMS on your number, status callbacks,
  honest confirmations, the missing reminders, preferences, sandbox copy gone.
- **Week 5, driver out.** Driver-out flow, series editing, tracking throttled
  to one ping per 20 s and one ETA per minute per group, ETA on the bulletin,
  geocode on save.
- **Week 6, installable.** Manifest, service worker, web push. Privacy policy
  and terms published, consent live, retention, export.
- **Week 7, return leg.** Return-leg parity, audit trail, numbered migrations.
- **Week 8, structure.** App factory and blueprints, scheduler out, About page
  rebuilt, one operations document. Then a second group of real families.

## Left out on purpose

WhatsApp Business (Meta verification and per-message templates; SMS plus push
covers everything). Car-capacity routing (wait for a launch group to prove the
size). Native apps (an installable web app with push gets nearly all the
value). Normalizing the Postgres blobs (one store at a time, after the split).

## Decisions that are yours

- SMS on your own number, or WhatsApp through Meta verification?
- Who reviews the privacy policy and terms?
- Is the second launch group a friend group, a school, or a league?
- Keep the emergency login?
