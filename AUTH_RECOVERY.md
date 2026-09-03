# Auth & Login Recovery Guide

If you can't log in, work down this list. Each section is independent.

## 0. First, what app are you on?
- Production: `https://carpoolsync.com` (the old `carpoolsync.up.railway.app` still answers, but the domain is canonical)
- Make sure you're typing your email **exactly** (lowercase). Email comparison is case-insensitive server-side but typos still bite.

---

## 1. Use the show-password toggle

On `/login`, click the 👁 icon next to the password field while typing. **9 times out of 10**, locked-out users discover their Caps Lock was on or there's a typo. This is the fastest fix.

---

## 2. Email yourself a sign-in link

On `/login`, click **Email me a sign-in link instead**. Supabase mails a
one-time link that signs you in without a password. This is the normal
recovery path and it works: the email templates link to
`{{ .RedirectTo }}?token_hash={{ .TokenHash }}&type=magiclink`, which the
`/auth/callback` route verifies server-side.

If the link opens the app but leaves you on the login page, check
**Supabase → Authentication → URL Configuration**: Site URL must be
`https://carpoolsync.com` and the redirect allow list must include
`https://carpoolsync.com/**`. A link whose redirect target is not on the
allow list is silently dropped.

---

## 3. Reset your password

On `/login`, click **Forgot password?**. Same mechanism as above with
`type=recovery`, landing on `/auth/reset-callback`, which puts you on the
choose-a-new-password screen.

### The emergency bypasses are gone

Earlier versions of this app had two recovery hatches: an
`/emergency-login?token=...` route that set a session with no password
check, and a boot-time hook that reset a named user's password from
`EMERGENCY_RESET_EMAIL` / `EMERGENCY_RESET_PASSWORD` env vars and printed
every user's masked email to the deploy log.

**Both were removed on 3 September 2026, and the env vars deleted from
Railway.** They existed only because the real reset flow was broken in
August; it works now. Anyone who could add a Railway variable could use
them to sign in as the owner, which is too much standing risk for a hatch
that is no longer needed. Do not add them back. If you are ever locked out
with the email flow also broken, reset the password directly in
**Supabase → Authentication → Users**, which is audited and does not
require shipping a bypass.

---

## 4. See the current state of the database

Two ways:

### `/admin/system` (in the app, requires login)
Shows every user, every group, storage status. Get there via Dashboard → Manage Users → 🔍 System View.

### `/health` (no login)
Visit `https://carpoolsync.up.railway.app/health`. Shows: DATA_DIR location, persistence status, group count, Supabase connection status.

### Railway shell (advanced)
```bash
brew install railway        # one-time
railway login               # one-time
railway link                # one-time, picks the carpoolsync project
railway shell               # opens a shell on the live container
cat /data/users.json | jq   # see all users
```

---

## 5. The "nuke from orbit" option

If nothing else works:

1. Email yourself a sign-in link to get in (see Section 2)
2. From `/admin/users`, delete every leftover account
3. Log out
4. Visit `/create-group` and start fresh

The persistent volume keeps your data across deploys, so the new account stays.

---

## Switching to Supabase Auth (recommended fix)

The bandaids above exist because we're using filesystem sessions + bcrypt + JSON files. Supabase Auth replaces all of that with one battle-tested service.

### When you're ready

1. **In Supabase Dashboard → SQL Editor**, run the SQL from [`supabase/schema.sql`](supabase/schema.sql) (creates all 18 tables).

2. **In Supabase Dashboard → Authentication → Providers → Email**, turn **"Confirm email" OFF**. This lets users sign up and log in without an email round-trip. (Confirmation emails hit the free tier's 4/hour rate limit.)

3. **In Railway → Variables**, add:
   ```
   USE_SUPABASE_AUTH = 1
   ```

4. Save. Railway redeploys. From now on:
   - Every new signup creates a Supabase Auth user (in addition to the local `users.json` record, which still stores name/phone/family_id/etc.)
   - Every login verifies against Supabase Auth
   - Bcrypt hashes in `users.json` are no longer the source of truth
   - The internal user record links to the Supabase user via `supabase_uid`

### To revert
Delete `USE_SUPABASE_AUTH` (or set to `0`). The app falls back to bcrypt-based auth instantly. No data loss either direction — both paths read/write the same internal user records.

---

## Phase 2b: user/group records in Postgres (kills the "app forgot me" bug)

With `USE_SUPABASE_AUTH=1` alone, passwords live in Supabase but your **profile**
(users.json, groups.json) still lives on the Railway volume — if that file is
ever lost, logins dead-end even with the right password. Phase 2b moves those
records into Supabase Postgres so identity survives anything short of deleting
the Supabase project.

### To enable

1. **In Supabase Dashboard → SQL Editor**, run [`supabase/migration_2b_identity.sql`](supabase/migration_2b_identity.sql) once.
2. **In Railway → Variables**, add:
   ```
   USE_SUPABASE_DB = 1
   ```
3. Save. On the next boot the app copies any existing JSON users/groups into
   Postgres automatically (look for `[IDENTITY MIGRATION]` in the deploy logs),
   then reads and writes them there from then on.
4. Verify on `/health` — the `Identity` line should read
   `🐘 Supabase Postgres (USE_SUPABASE_DB=1) — N user(s)`.

### To revert
Delete `USE_SUPABASE_DB`. The app falls back to the JSON files, which are left
in place as a cold backup at the moment of migration (changes made while the
flag was on won't be in them).

### Phase 2c — per-group data in Postgres too
The same `USE_SUPABASE_DB=1` flag also moves every per-group data file
(families, rotation, schedule, trips, karma, absences, location, route_cache,
confirmations, swap_state, trip_config) into Postgres, stored as jsonb blobs in
a `group_files` table. So once the flag is on, **nothing** the app relies on
lives on the disposable volume.

Prerequisite: run [`supabase/migration_2c_group_files.sql`](supabase/migration_2c_group_files.sql)
once in the SQL Editor (alongside the 2b migration) before/at the time you set
the flag. On the next boot the app copies any on-disk group files into Postgres
automatically (`[GROUP-FILES MIGRATION]` in the deploy logs) and reads/writes
them there afterward. On-disk copies are left as a cold backup.

Still on the volume after 2c: `invites.json`, `resets.json`, and
`geocode_cache.json` (short-lived tokens and a regenerable cache — low stakes).

### Backups
Use **`/admin/backup`** (any admin account, in the browser) to download every
JSON file still on the volume as a tar.gz. Once Phase 2c is on, the durable
data lives in Supabase — back that up from the Supabase dashboard.

### What you gain
- Password hashing handled by Supabase (no atomic-write bugs)
- Login sessions stateless (no Flask filesystem sessions to wipe)
- Built-in admin UI in Supabase Dashboard → Authentication → Users
- Password reset emails (once you wire SMTP — see "Custom SMTP" below)
- Magic-link sign in (one tap from email — works today but rate-limited)
- Future-proof: easy to add Google sign-in, magic links, MFA later

---

## Custom SMTP (removes email rate limit)

Supabase's built-in SMTP is limited to 4 emails/hour on the free tier. To remove that:

1. Create a free [Resend](https://resend.com) account.
2. Generate an API key (starts with `re_...`).
3. In Supabase Dashboard → Project Settings → Authentication → SMTP Settings:
   - Enable Custom SMTP
   - Sender email: `onboarding@resend.dev` (free, no domain)
   - Sender name: `CarpoolSync`
   - Host: `smtp.resend.com`
   - Port: `465`
   - Username: `resend`
   - Password: *(your Resend API key)*
4. Save.

Now magic-link and password-reset emails go out without rate limits (3,000/month free on Resend).

---

## Common errors and what they mean

| Error message | Most likely cause | Fix |
|---|---|---|
| "Invalid email or password" | Typo or wrong account | Use 👁 toggle, or Section 2/3 |
| "The CSRF tokens do not match" | Stale browser cookie | Hard-refresh (`Cmd+Shift+R`), or open in Incognito |
| "Too many login attempts" | Hit rate limit | Wait 5–10 min |
| "Sign-in link is invalid or expired" | Magic link is >1hr old | Request a new one |
| Sign-in link opens the app but you stay logged out | Redirect target not on the Supabase allow list | Section 2 |

---

## Last-resort: dump the data and email it to yourself

If you ever fear data loss:

```bash
railway run "tar czf - /data" > carpool-backup-$(date +%Y%m%d).tgz
```

This dumps the entire volume to a local tar.gz. Open it with any unarchiver to browse the raw JSON files.
