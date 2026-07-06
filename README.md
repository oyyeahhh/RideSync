# CarpoolSync

<img src="static/tesla.png" alt="" align="right" width="220">

**Carpool, on autopilot.**

A small-team coordinator for parents who drive their kids to the same destination — soccer, school, music lessons. Replaces group-chat chaos with smart driver rotation, optimized pickup routes, WhatsApp invites, and a kid-friendly bulletin board for the kitchen iPad.

🌐 **Live:** [carpoolsync.up.railway.app](https://carpoolsync.up.railway.app)
📖 **Marketing page:** [/about](https://carpoolsync.up.railway.app/about)

---

## 👋 New here? Start with the handoff docs

If you're a new contributor (or a fresh Claude session) picking this up, read these three docs in order before doing anything else:

| Read first | What it gives you |
|---|---|
| **[PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)** | Full project context: tech stack, every feature, the auth/login history, design system, how the owner works |
| **[QUICKSTART.md](QUICKSTART.md)** | 30-min hands-on verification: clone → run locally → push a trivial change → revert. Ends with a 7-question orientation quiz. |
| **[AUTH_RECOVERY.md](AUTH_RECOVERY.md)** | Keep for emergencies — every "I'm locked out" escape hatch (emergency-login, env-var reset, etc.) |

---

## What's in this repo (top-level)

- `portal.py` — Flask app, all routes (~1900 LOC, single file)
- `auth.py`, `auth_supabase.py` — legacy bcrypt auth + new Supabase Auth (behind feature flag)
- `supabase_client.py` — Supabase SDK singletons
- `supabase/schema.sql` — canonical Postgres schema (18 tables incl. memberships, ready to apply)
- `templates/` — Jinja2: `dashboard.html`, `kid_bulletin.html`, `about.html`, etc.
- `static/` — logo, mascot (Tesla full of kids), background

Module-by-module breakdown is in [PROJECT_HANDOFF.md § 3](PROJECT_HANDOFF.md).

## Tech stack

- **Backend:** Python 3.11 + Flask, gunicorn
- **Host:** Railway (with persistent volume at `/data`)
- **Storage:** JSON files (migrating to Supabase Postgres)
- **Auth:** bcrypt + Flask sessions (migrating to Supabase Auth)
- **SMS / WhatsApp:** Twilio sandbox
- **Maps:** Google Maps Platform (Routes + Geocoding)
- **Background jobs:** APScheduler — auto-route 15-min ticker + nightly rotation advance

## Status at a glance

| Layer | State |
|---|---|
| Production deploy | ✅ Live on Railway, persistent volume |
| Multi-tenant groups | ✅ Done |
| WhatsApp invites + signup | ✅ Done (Twilio sandbox) |
| Driver rotation + skip-absent | ✅ Done + nightly auto-advance |
| Recurring schedule | ✅ Done |
| Optimal route + auto-send SMS | ✅ Done |
| Live driver tracking | ✅ Done |
| Kid bulletin (public iPad view) | ✅ Done |
| Calendar feed (Google/Apple subscribe) | ✅ Done |
| Admin tools (manage users, system view) | ✅ Done |
| **Supabase Auth migration** | 🟡 Built behind `USE_SUPABASE_AUTH=1` flag, ready to enable |
| **Supabase Postgres migration** | 🟡 Schema defined, modules not yet swapped |

Full migration plan: [PROJECT_HANDOFF.md § 5](PROJECT_HANDOFF.md).

## To run locally

See [QUICKSTART.md](QUICKSTART.md) for the full walkthrough. Briefly:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# create .env with stub values — see QUICKSTART.md for the template
export FLASK_TESTING=1   # disables APScheduler locally
python portal.py
```

App runs at `http://localhost:3000`.

## Deploy

Push to `main` → Railway auto-deploys in ~60–90s. No CI yet.

## Who built it

Orly Nadler ([@oyyeahhh](https://github.com/oyyeahhh)), with extensive pair-programming by Claude (Anthropic).
