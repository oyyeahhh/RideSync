# Launch Plan: one carpool first

One carpool of families Orly knows, usable on their phones within a week,
solid within three, structured so that opening it to a second carpool later
is additive work rather than a rewrite. Companion to REPAIR_PLAN.md (what is
broken today). Revised 2 September 2026.

## Status, 2 September 2026

Week one is built and sits on `claude/repair-blockers-2026-09-02`, nine
commits ahead of `main`, not yet pushed or deployed. All five days landed:
phone layout (overflow 0 on every page), role-aware dashboard, attendance on
any date, message fan-out to every parent in a household, honest delivery
counts, Trip Settings validation, email links that work in all three shapes
Supabase produces, collapsed schedule, pinned requirements, a 56-test suite
and CI. Still needed from Orly before parents are invited: push the branch
and merge, set `OWNER_EMAILS=orlyn8@gmail.com` on Railway (it must match the email of your admin login in the app), submit the Twilio registration, and
in the Supabase dashboard point the Magic Link and Reset Password email
templates at `{{ .RedirectTo }}?token_hash={{ .TokenHash }}&type=magiclink`
and `...&type=recovery` (the fragment fallback works without this, but the
token_hash flow is the reliable one).

## Decisions

- **SMS on the Twilio number Orly already owns.** Registration starts on day
  one: A2P 10DLC for a local ten-digit number (brand plus campaign; sole
  proprietors qualify; one to three weeks) or toll-free verification for a
  toll-free number (usually faster). Until approval, the WhatsApp sandbox
  stays available behind `MESSAGE_CHANNEL=whatsapp` as the fallback.
- **Two-parent households get every message.** Week one: every phone attached
  to a family receives what the family receives. Week two: a proper guardians
  list with `is_driver` and a notification preference.
- **Every trip has a return leg, usually driven by a different family.**
  Return-leg parity moves from "later" into weeks two and three: return
  driver from the rotation, route, reminder, check-ins, arrival, calendar
  event and ETA for the return, and the driver-out flow for either leg.
- **Assumed until told otherwise:** one car fits everyone.

## The rule that keeps the door open

Everything already keys on `group_id`. Week one keeps it that way: no
group-specific value in code (destination, times, numbers and the sandbox
keyword live in config or environment); `OWNER_EMAILS` separates the operator
from a group admin; guardians and preferences are data. When a second carpool
arrives, the remaining work is privacy text and consent, admin transfer,
joining a second group, and token hygiene. Nothing built this week comes out.

## Week one: usable by parents

1. **Day 1. Fit the phone, deploy the repairs, start the clocks.** The two CSS
   lines that end the 227 px overflow (`repeat(7, minmax(0, 1fr))` on
   `.cal-grid`, `min-width: 0` on `.cal-trip-dot`), logo capped at 40 px with
   an SVG. Merge the repair branch with `OWNER_EMAILS` set. Procfile to one
   worker, eight threads, gthread. `MESSAGE_CHANNEL=sms|whatsapp` makes the
   `whatsapp:` prefix conditional. Orly submits the carrier registration.
2. **Day 2. A dashboard that knows who you are.** Driver buttons only for the
   active driver or an admin. Other families' attendance as read-only status.
   Attendance card named for its real date. Stats, karma, history, trip
   settings, invites and user management under one admin Manage section.
   Empty sections hidden.
3. **Day 3. Absent on any date, and both parents in the loop.** Per-trip
   attendance list; `/toggle-absent` takes a date. Message fan-out to every
   phone attached to a family.
4. **Day 4. Tell the truth, and make reset work.** Running Late, Kids Arrived
   and Invite report sent and failed counts with a copy-the-link fallback, in
   a toast, never a raw server string. Trip Settings validates date and
   timezone. The Supabase code verifier moves into the session so password
   reset and magic links stop failing at random.
5. **Day 5. Collapse the schedule, lock the build, invite the families.**
   Series shown as one line with the next three dates. Lock file and Python
   pin. The 27 checks in pytest with a smoke test over every route, run by
   GitHub Actions. Nightly backup, uptime ping. Walk it with
   `tests/mobile_walk.py`, then send the invites.

## Weeks two and three: solid

First:
- SMS cutover when approved: flip the channel, Twilio status callback, a
  per-message delivery record, join-keyword copy removed from signup.html and
  welcome.html, invites by SMS with an email fallback (Resend or Postmark),
  stop marking the route sent before the send (portal.py:3262). 2 days.
- Guardians as a list (families.py:44-52, 69-87; portal.py:1556, 1695). 2 days.

Then:
- Return-leg parity: the return driver comes from the rotation (or is
  claimed), and gets a route, a reminder, check-ins, arrival, a calendar
  event and an ETA. cal_feed.py emits outbound only today. 1 week.
- Driver-out flow, for either leg, with one-tap claim (portal.py:3000-3088, 3206, 3350). 2 to
  3 days.
- Series editing: this and following, skip range, moveable date
  (portal.py:2299-2309; schedule.py:66). 2 days.
- Missing reminders: night-before to all families, morning driver, trip or
  driver changed, driver on the way with ETA (portal.py:2290, 3323, 3364).
  2 days.
- Base template, tokens, self-hosted fonts, 44 px targets, labels, focus
  rings, SVG icons, AA contrast. 2 days.
- Second admin (promote, demote, refuse to remove the last) and self-service
  delete with full cascade (portal.py:1562, 3617, 3440). 1.5 days.

If time:
- Installable: manifest, icons, safe areas, service worker with the drive
  page offline. 2 days.
- Tracking throttle (one ping per 20 s, one ETA per minute per group, off the
  request thread) and ETA on the bulletin (portal.py:2745-2769, 2863). Half a
  day.
- Request ids, Sentry, `/healthz` with a scheduler heartbeat. 1 day.

## Later: opening to other carpools

- Privacy policy, terms, consent checkbox, sub-processors named. 2 to 3 days
  including review.
- Bulletin and calendar token regeneration, viewer log, retention, export,
  audit trail on trips. 2 days.
- Memberships end to end: join a second group, leave, change email, admin
  transfer between groups. 1 week.
- Car capacity (after a group proves the size), split households (2 to 3
  days).
- App factory and blueprints, scheduler as a cron service, numbered
  migrations, Postgres normalization one store at a time, one operations
  document. 3 to 4 weeks, spread out.
- About page rebuilt on a fluid grid. 2 days.

## Left out on purpose

WhatsApp Business (Meta verification and per-message templates; SMS covers
everything). Web push as the primary channel (SMS was chosen; push stays a
free add-on once installable). Native apps.

## Needed from Orly this week

- Submit the Twilio registration and say which kind of number
  `TWILIO_FROM_NUMBER` is.
- Set `OWNER_EMAILS` on Railway, then merge and push the branch.
- Group size, and whether there is a return leg.
