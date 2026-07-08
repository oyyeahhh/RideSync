-- Phase 2b migration: users/memberships/groups move into Postgres while
-- families STAY in JSON files for now — so memberships.family_id can't be
-- a real foreign key yet (the referenced family rows don't exist in Postgres).
--
-- Run this once in Supabase Dashboard → SQL Editor BEFORE setting
-- USE_SUPABASE_DB=1 in Railway. Re-running is safe.
--
-- The FK comes back in Phase 2c when families move into Postgres too.

alter table public.memberships drop constraint if exists memberships_family_id_fkey;
