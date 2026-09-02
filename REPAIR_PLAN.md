# Repair Plan

Audit of `main` at commit `9d76e5d`, 2 September 2026. Written for whoever picks
this up next, including a fresh Claude session. Read this after
PROJECT_HANDOFF.md and before touching anything.

The app boots and the core flow works. It was installed, booted and exercised
end to end in a clean sandbox: create a group, sign in, add a trip, add a
recurring series, load the dashboard, subscribe to the calendar feed. The gap
between that and reliable is one dashboard-bricking bug, a password reset that
fails more often than it works, and a set of admin routes that hand one group's
data to another.

## Already fixed

Committed on `claude/repair-blockers-2026-09-02`. Covered by
`tests/verify_flow.py`, which stands the app up against a throwaway data
directory and runs 27 checks. Run it with the server up on port 3000.

1. A trip saved with an empty or malformed arrive-by time made `gcal_url` raise
   on every dashboard load, so one bad Add Trip left the whole group on the 500
   page with no interface left to delete the trip. The three schedule routes now
   reject a bad date, time or destination with a 400, and the dashboard degrades
   a bad trip to a missing calendar link.
2. `/admin/backup` tarred every group's data, including live display tokens,
   unused invite tokens and unused reset tokens, for anyone who registered a
   group at `/create-group`. It now requires `OWNER_EMAILS`. `/admin/system` is
   scoped to the caller's own group under the same rule.
3. The parent dashboard's script aborted at an admin-only `#m-end-date`
   listener, which killed the calendar, the karma gauges and ride tracking for
   every non-admin.
4. `/health/smtp-test` sent a real password-reset email to any address in the
   query string, unauthenticated and unthrottled. Removed.
5. Production hardening was inferred from `DATA_DIR`, so a local `.env` turned
   on Secure cookies and broke every local login, and removing the Railway
   volume would silently turn the hardening off. It now reads
   `RAILWAY_ENVIRONMENT`, `FLASK_ENV` and `FLASK_TESTING`.
6. A second "Kids arrived" tap inside the Twilio send window texted every family
   twice and advanced the rotation twice. The trip is claimed first now.
7. Twilio calls had no HTTP timeout. Ten seconds now.
8. Six smaller repairs: `DATA_DIR` from `.env` was ignored; the rate limiter
   trusted a caller-supplied header; `drive_token` was serialized to every group
   member; family ids from a name with an apostrophe broke their own handlers;
   the delete confirmation skipped for those names; `/login` was CSRF-exempt.

## Start here, in this order

1. **Read the Railway variables.** `USE_SUPABASE_AUTH`, `USE_SUPABASE_DB`, and
   whether `DATA_DIR` still points at the volume. Four findings below change
   severity on the answer. 5 minutes.
2. **Set `OWNER_EMAILS` and deploy the branch.** Keeps the platform-wide backup
   and system views for the deployment owner. 10 minutes.
3. **Drop to one worker with threads.** `--workers 1 --threads 8 -k gthread` in
   the Procfile. Removes the cross-worker reset failure, the duplicated
   scheduler, the doubled rate limits and the racing startup migration at once.
   It does not fix the in-process read-modify-write race. 5 minutes.
4. **Validate the Trip Settings form.** Same bug class as item 1 above, on
   `/save-trip` instead of the schedule routes. 1 hour.
5. **Fix where the Supabase code verifier is stored.** See below. Half a day.
6. **Make `/signup` create a Supabase user, and add a bcrypt fallback.** Only
   after step 1 confirms the flag. 2 hours.
7. **Decide what happens to `/emergency-login`.** 15 minutes plus a decision.
8. **Close the JSON write races, then pre-flight and finish the Postgres move.**

## Open findings

Severity, then effort. "Flag on" means it only bites when the matching Supabase
flag is set.

### Blockers

- **Password reset and magic link fail across requests.** In PKCE mode the
  verifier is written into one process-local slot on a module-level client, and
  the callback reads it back from the same key. A second send overwrites it,
  another worker never had it, a redeploy wipes it. Generate the verifier
  yourself, keep it in the server-side session, pass it to the exchange call
  explicitly. `auth_supabase.py:41-92`, `supabase_client.py:41-51`. Half a day.
  This is what the last six commits were chasing.
- **Invited parents can never log in (flag on).** Invite signup writes a bcrypt
  hash and no Supabase user, and login under the flag has no bcrypt fallback.
  Admin reset, forgot-password and the startup reset all report success and
  change nothing for these accounts. `portal.py:1643-1657`, `portal.py:605-630`.
  2 hours.
- **Trip Settings stores an unvalidated date and timezone.** Saved, then every
  dashboard load, bulletin load and 15-minute job raises on it.
  `portal.py:2028-2052`, `config.py:48-56`. 1 hour.
- **Duplicate emails crash-loop the app if `USE_SUPABASE_DB` is turned on.**
  Unique index on `lower(email)`, duplicate accounts are a known state of
  `users.json`, and the migration re-raises at import. Railway retries three
  times and leaves the site down. `supabase/schema.sql:35`, `portal.py:86`.
  30 minutes of pre-flight before any flip.

### High

- **Lost updates on every JSON store.** Read and write take the lock
  separately, so two workers interleave and one save erases the other. Two
  families signing up in the same second can lose one family record, leaving a
  dangling `family_id` that raises on every dashboard load for that user.
  `storage.py:130-185`, `auth.py:88-103`, `families.py:79-81`. Route mutations
  through the single-lock `update_json` helper. Half a day.
- **`/emergency-login` forges a session from a URL token.** No throttle, no POST
  requirement, and with `EMERGENCY_RESET_EMAIL` unset it falls back to the
  oldest account. The token lands in proxy logs and browser history.
  `portal.py:730-764`. 15 minutes.
- **Identity writes replace the whole table (flag on).** `db_save_users` upserts
  the caller's list then deletes every row not in it, with no lock. Two
  concurrent saves and the second deletes the user the first just created.
  `db_identity.py:88-137`. Half a day.
- **No error handling or timeout on the Postgres path (flag on).** The user
  lookup runs on every authenticated request with no try block and a 120 second
  default timeout. `db_identity.py:55-84`, `supabase_client.py:47-56`. 2 hours.
- **The dashboard makes hundreds of round trips (flag on).** The calendar-link
  builders reload the group config per trip. A weekday series over one term is
  several hundred sequential queries per page load. `portal.py:1882-1884`,
  `portal.py:514-517`. 2 to 4 hours.
- **"Sent to all families" when nothing was sent.** Running Late and Kids
  Arrived swallow every Twilio exception and still return success. The arrival
  still advances the rotation and writes history. `portal.py:2428-2473`,
  `portal.py:1074-1099`. 1 hour.
- **A billable route-matrix call on every GPS ping.** The browser posts a
  location every few seconds during a ride, and each post computes ETAs through
  the Routes Matrix API in the request thread. `portal.py:2745-2769`,
  `templates/dashboard.html:1824`. 2 hours.

### Medium

- Orphaned Supabase identities are claimable, and deleting a user leaves the
  auth user in place. `portal.py:1468-1481`, `portal.py:3537`,
  `auth_supabase.py:237-242`.
- `/health` is public and prints the Supabase URL, every group id, user counts
  and partial emails. `portal.py:1143-1219`.
- Flask sessions still live on the disposable volume. `portal.py:369-370`.
- Group delete and stale-group detection reach across groups, gated only by a
  role anyone can obtain. `portal.py:3375`, `portal.py:3624-3640`.
- `/stop-ride` has no driver check, and the magic-link form has no CSRF token.
  `portal.py:2737`, `templates/login.html:196`.
- Multi-membership rows are destroyed on the next save, so the join table cannot
  hold a second group. `db_identity.py:62`, `db_identity.py:130-135`.
- Fifteen of the nineteen tables in `supabase/schema.sql` have no reader and no
  writer. Live data sits in `group_files` as jsonb, and the blob shapes disagree
  with the normalized columns, so finishing the migration against the file as
  written would silently drop fields.
- Startup migration runs once per worker, concurrently. `portal.py:73-86`.
- `templates/welcome.html:174` hardcodes the WhatsApp sandbox keyword that
  signup reads from the environment, and says reset links arrive by WhatsApp,
  which is wrong under Supabase Auth. `/bulletin/<group_id>` reads the settings
  date rather than the schedule.

## Optional upgrades

- Split `portal.py`. It is 3,680 lines and 153KB in one module.
- Adopt numbered migrations. Every statement in the schema file is
  `CREATE IF NOT EXISTS`, which can never add or drop a column.
- Run `tests/verify_flow.py` in CI. There is none today, and a push to main
  deploys straight to production.
- Back up on a schedule rather than on a button press.
- Move the fan-out messages off the request thread into the existing scheduler.
- Normalize one blob at a time, starting with the schedule.

## Unknowns

Data files are gitignored and there is no schema version anywhere, so these have
to come from the Railway and Supabase dashboards.

- Is `USE_SUPABASE_AUTH` set to 1? The handoff doc says no. The last six commits
  only make sense if the answer is yes.
- Is `USE_SUPABASE_DB` set?
- Has `supabase/schema.sql` been applied, and at which commit? The three docs
  give three different answers, and the `/health` probe checks only one table,
  so a partial apply reads as complete.
