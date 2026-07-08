-- Phase 2c migration: per-group data files move off the Railway volume into
-- Postgres. Each JSON file (families, rotation, schedule, trips, karma,
-- absences, location, route_cache, confirmations, swap_state, trip_config)
-- is stored as one jsonb blob keyed by (group_id, filename).
--
-- Run this once in Supabase Dashboard → SQL Editor BEFORE deploying the
-- Phase 2c code (it's already covered by USE_SUPABASE_DB=1). Re-running is safe.
--
-- group_id is plain text (not an FK): groups may exist only implicitly and the
-- app deletes these rows explicitly when a group is removed.

create table if not exists public.group_files (
  group_id    text not null,
  filename    text not null,
  payload     jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now(),
  primary key (group_id, filename)
);
create index if not exists group_files_group_idx on public.group_files (group_id);

-- Deny-by-default like every other table: RLS on, no policies → only the
-- service-role key (the Flask server) can touch it.
alter table public.group_files enable row level security;
