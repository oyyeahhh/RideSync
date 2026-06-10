# Quickstart — First 30 minutes

A receiving Claude (or new contributor) should be able to follow these steps and end with proof they can read the repo, run the app, and push a trivial change to production. Don't skip any step — each one verifies a specific thing.

**Expected time:** 20–30 minutes including waiting for Railway to redeploy.

---

## Goal of this exercise

By the end, you'll have:
1. ✅ Read the repo locally
2. ✅ Run the Flask app on your machine (in an isolated mode that doesn't touch production data)
3. ✅ Made a verifiable change to a visible string
4. ✅ Committed + pushed it to `main`
5. ✅ Confirmed Railway picked it up and the live site shows the change
6. ✅ Reverted the change to leave production clean

If anything in this list fails, you're not ready to do real work yet — debug the failure first.

---

## Step 0 — Verify access (1 min)

```bash
cd /Users/orlynadler/Desktop/Carpool
git status        # should be clean (or near-clean)
git log --oneline -5
```

Expected: latest commit references PROJECT_HANDOFF.md, AUTH_RECOVERY.md, or Supabase Auth migration work.

If `git status` shows uncommitted changes you didn't make: stop and ask Orly before touching anything.

---

## Step 1 — Local Python environment (5 min)

The app targets Python 3.11+. Set up an isolated venv so you don't pollute the user's global Python.

```bash
cd /Users/orlynadler/Desktop/Carpool
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

If `pip install` fails on `apscheduler` or `supabase`, try `pip install --upgrade setuptools wheel` first.

---

## Step 2 — Stub env vars for local dev (2 min)

The app reads env vars from the environment (production) or a `.env` file (local). **Do NOT use production values locally** — that would touch the live Supabase/Twilio.

Create `.env` in the project root:

```bash
cat > .env <<'EOF'
# Local dev only — never commit these
SECRET_KEY=local-dev-secret-do-not-use-in-prod
DATA_DIR=./local_data

# Twilio: leave the auth token blank so webhook signature check is skipped in dev
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
TWILIO_SANDBOX_KEYWORD=
TWILIO_SANDBOX_NUMBER=+14155238886

# Google Maps: blank means geocoding will fail loudly when used — fine for testing UI
GOOGLE_MAPS_API_KEY=

# Supabase: leave blank for local dev unless you've created a separate dev project
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
EOF
```

`.env` is gitignored (verify: `cat .gitignore`).

---

## Step 3 — Run the app locally (2 min)

```bash
source .venv/bin/activate
export FLASK_TESTING=1   # disables APScheduler so it doesn't run jobs locally
python portal.py
```

Expected output ends with something like:
```
* Running on http://0.0.0.0:3000
```

Open `http://localhost:3000/about` in your browser. The marketing page with the Tesla mascot should load. If it does — you're up.

`Ctrl+C` to stop the server.

> **Note:** without Supabase env vars, `/health` will show `❌ NOT CONFIGURED — missing env: ...`. That's expected locally.

---

## Step 4 — Make a tiny verifiable change (3 min)

We're going to edit a string nobody will mind being briefly different. Open `templates/about.html` and find the footer:

```html
Made by parents, for parents. © CarpoolSync.
```

Change it to:

```html
Made by parents, for parents. © CarpoolSync. (deploy test by claude)
```

Save the file.

---

## Step 5 — Verify locally (1 min)

```bash
source .venv/bin/activate
export FLASK_TESTING=1
python portal.py
```

Reload `http://localhost:3000/about` → scroll to the footer → confirm your change shows.

Stop the server (`Ctrl+C`).

---

## Step 6 — Commit and push (2 min)

```bash
git status                       # should show templates/about.html modified
git diff templates/about.html    # review the change

git add templates/about.html
git commit -m "$(cat <<'EOF'
test: handoff verification — temporary footer tag on /about

Will be reverted in the next commit. Verifies that the new contributor
can edit, commit, and push to main and Railway auto-deploys.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
git push
```

---

## Step 7 — Verify Railway deployed it (5–10 min)

1. Open https://railway.app → CarpoolSync project → Deployments tab
2. Watch for a new deployment to start (within ~30 seconds of `git push`)
3. Wait for the green ACTIVE badge (~60–90 seconds total)
4. Open https://carpoolsync.up.railway.app/about in your browser
5. Scroll to the footer → you should see `(deploy test by claude)` appended

**If you don't see it:**
- Hard-refresh (`Cmd+Shift+R`) — your browser may have cached the old version
- Check the build log for errors
- If the build failed, fix it before continuing

---

## Step 8 — Revert the test change (2 min)

Clean up so production doesn't have your test text forever.

```bash
git revert HEAD --no-edit
git push
```

Wait 60–90s, hard-refresh `/about`, confirm the footer is back to normal.

You've now demonstrated the full edit → push → deploy → verify → revert loop. You're ready to do real work.

---

## What NOT to touch without asking

These are tripwires. If you change any of them by accident, stop and tell Orly.

- **`/data/` on the Railway volume** — the live database. Never run scripts that delete or rewrite it.
- **Railway env vars** — especially `SECRET_KEY`, `DATA_DIR`, `SUPABASE_SERVICE_ROLE_KEY`. Changing these can lock everyone out.
- **`users.json` directly** — even on the volume. Has been wiped accidentally once already; don't push your luck.
- **Twilio account** — sandbox costs ~$0; don't accidentally trigger 100 SMS sends in testing.
- **`supabase/schema.sql` after it's been applied** — modifying applied SQL is a migration, not an edit. Add a new migration file instead.

---

## If you break production

Order of operations, fastest to slowest:

1. **Revert the bad commit:**
   ```bash
   git revert HEAD --no-edit && git push
   ```
   Railway will redeploy with the working version in ~60s.

2. **Emergency login** (in case you broke auth):
   - Railway → Variables → set `EMERGENCY_LOGIN_TOKEN=letmein1234`
   - Visit `https://carpoolsync.up.railway.app/emergency-login?token=letmein1234`
   - **Delete the env var after** you're in

3. **See `AUTH_RECOVERY.md`** for every other recovery path.

---

## Where to go next

Once Steps 0–8 are green:

1. **Read `PROJECT_HANDOFF.md` end to end** if you haven't already
2. **Skim `portal.py`** — it's the heart; ~1900 lines, every route lives there
3. **Skim `supabase/schema.sql`** — that's the future data model
4. **Ask Orly what's next.** Don't pick up unfinished migration work without checking — phase ordering matters

---

## Sanity-check checklist (for the receiving Claude)

Before you reply to Orly with anything substantive, confirm you can answer these without re-reading:

- [ ] What is the production URL?
- [ ] Where do data files live on the Railway volume?
- [ ] What does `USE_SUPABASE_AUTH=1` switch on?
- [ ] What's the difference between `auth.py` and `auth_supabase.py`?
- [ ] What does `/emergency-login` do?
- [ ] What's the Twilio sandbox join keyword?
- [ ] What's the tagline?

If you can answer all seven, you're oriented. Welcome to CarpoolSync.
