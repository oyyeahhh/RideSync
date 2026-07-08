-- CarpoolSync canonical Postgres schema
-- Run this once in Supabase Dashboard → SQL Editor → New query → paste → Run.
-- Re-running is safe: every CREATE uses IF NOT EXISTS.

-- ─── extensions ───────────────────────────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ─── groups (top-level tenancy unit) ──────────────────────────────────────────
create table if not exists public.groups (
  id              text primary key,            -- "grp_xxxxxxxx"
  name            text not null,
  display_token   text unique,                 -- public URL for /display/<token>
  config          jsonb not null default '{}'::jsonb,  -- trip_config blob
  created_at      timestamptz not null default now()
);

-- ─── users (app accounts; may link to auth.users via supabase_uid) ────────────
-- A user is a PERSON, not a seat in one carpool. Group-scoped fields
-- (role, family_id, group_id) live in `memberships` below so one account
-- can belong to several carpools with a different role/family in each.
-- Migration note (Phase 2b): each legacy users.json record maps to one
-- `users` row plus one `memberships` row carrying its group_id/family_id/role.
create table if not exists public.users (
  id              text primary key,            -- "user_xxxxxxxx"
  supabase_uid    uuid references auth.users(id) on delete set null,
  email           text not null,
  password_hash   text,                        -- bcrypt; nullable if magic-link only
  name            text not null default '',
  phone           text,
  child_name      text,                        -- signup echo; canonical copy in kids
  address         text,                        -- signup echo; canonical copy in families
  joined_at       timestamptz not null default now(),
  calendar_token  text unique                  -- for cookieless /calendar/<token>.ics
);
create unique index if not exists users_email_lower_idx on public.users (lower(email));

-- ─── families (units within a group) ──────────────────────────────────────────
create table if not exists public.families (
  id                    text primary key,      -- "fam_xxxxxxxx"
  group_id              text not null references public.groups(id) on delete cascade,
  name                  text not null,
  primary_address       text,
  primary_address_lat   double precision,
  primary_address_lng   double precision,
  created_at            timestamptz not null default now()
);
create index if not exists families_group_idx on public.families (group_id);

-- ─── memberships (user ↔ group join; the multi-carpool seam) ──────────────────
-- One row per (user, group). Role and family are PER GROUP: the same person
-- can be admin of the soccer carpool and a plain parent in the school one.
-- The app's "active group" is a session concept, not a data concept.
create table if not exists public.memberships (
  user_id     text not null references public.users(id) on delete cascade,
  group_id    text not null references public.groups(id) on delete cascade,
  family_id   text,  -- plain text (not an FK) while families live in JSON; Phase 2c restores the reference
  role        text not null default 'parent',  -- 'admin' | 'parent', scoped to this group
  joined_at   timestamptz not null default now(),
  primary key (user_id, group_id)
);
create index if not exists memberships_group_idx on public.memberships (group_id);
create index if not exists memberships_family_idx on public.memberships (family_id);

-- ─── guardians (parents within a family) ──────────────────────────────────────
create table if not exists public.guardians (
  id          text primary key,
  family_id   text not null references public.families(id) on delete cascade,
  name        text not null,
  phone       text,
  email       text,
  is_driver   boolean not null default true
);
create index if not exists guardians_family_idx on public.guardians (family_id);

-- ─── kids (children within a family) ──────────────────────────────────────────
create table if not exists public.kids (
  id          text primary key,
  family_id   text not null references public.families(id) on delete cascade,
  name        text not null
);
create index if not exists kids_family_idx on public.kids (family_id);

-- ─── rotation (driver cycle per group) ────────────────────────────────────────
create table if not exists public.rotation (
  group_id        text primary key references public.groups(id) on delete cascade,
  order_ids       text[] not null default '{}',
  current_index   integer not null default 0
);

-- ─── schedule (upcoming trips) ────────────────────────────────────────────────
create table if not exists public.schedule (
  id                          text primary key,            -- "uuid hex slice"
  series_id                   text,
  group_id                    text not null references public.groups(id) on delete cascade,
  trip_date                   date not null,
  arrival_time                text,                        -- "HH:MM"
  return_time                 text,
  destination_name            text,
  destination_address         text,
  driver_family_id            text,
  driver_name                 text,
  return_driver_family_id     text,
  return_driver_name          text,
  route_sent                  boolean not null default false,
  rotation_advanced           boolean not null default false
);
create index if not exists schedule_group_date_idx on public.schedule (group_id, trip_date);
create index if not exists schedule_series_idx on public.schedule (series_id);

-- ─── trips (history log) ──────────────────────────────────────────────────────
create table if not exists public.trips (
  id                  uuid primary key default uuid_generate_v4(),
  group_id            text not null references public.groups(id) on delete cascade,
  trip_date           date not null,
  driver_family_id    text,
  driver_name         text,
  miles               double precision not null default 0,
  minutes             integer not null default 0,
  pickup_family_ids   text[] not null default '{}',
  created_at          timestamptz not null default now()
);
create index if not exists trips_group_date_idx on public.trips (group_id, trip_date);

-- ─── absences (per-day, per-family flags) ─────────────────────────────────────
create table if not exists public.absences (
  group_id      text not null references public.groups(id) on delete cascade,
  absent_date   date not null,
  family_id     text not null,
  primary key (group_id, absent_date, family_id)
);

-- ─── karma (fairness ledger) ──────────────────────────────────────────────────
create table if not exists public.karma (
  group_id      text not null references public.groups(id) on delete cascade,
  family_id     text not null,
  family_name   text,
  requested     integer not null default 0,
  covered       integer not null default 0,
  primary key (group_id, family_id)
);

-- ─── invites (sign-up tokens) ─────────────────────────────────────────────────
create table if not exists public.invites (
  token         text primary key,
  phone         text,
  group_id      text references public.groups(id) on delete cascade,
  family_id     text,
  family_name   text,
  created_at    timestamptz not null default now(),
  used          boolean not null default false
);
create index if not exists invites_used_idx on public.invites (used, created_at);

-- ─── password resets ──────────────────────────────────────────────────────────
create table if not exists public.password_resets (
  token         text primary key,
  user_id       text not null references public.users(id) on delete cascade,
  created_at    timestamptz not null default now(),
  used          boolean not null default false
);

-- ─── geocode cache (global) ───────────────────────────────────────────────────
create table if not exists public.geocode_cache (
  address       text primary key,
  latitude      double precision not null,
  longitude     double precision not null,
  cached_at     timestamptz not null default now()
);

-- ─── route cache (latest computed pickup list per group) ──────────────────────
create table if not exists public.route_cache (
  group_id      text primary key references public.groups(id) on delete cascade,
  payload       jsonb not null,                           -- full cache blob
  updated_at    timestamptz not null default now()
);

-- ─── live driver location (per group, ephemeral but persisted) ────────────────
create table if not exists public.location (
  group_id      text primary key references public.groups(id) on delete cascade,
  active        boolean not null default false,
  driver_name   text,
  trip_leg      text,
  latitude      double precision,
  longitude     double precision,
  started_at    timestamptz,
  updated_at    timestamptz default now()
);

-- ─── confirmations + swap state (used by SMS webhook) ─────────────────────────
create table if not exists public.confirmations (
  group_id      text not null references public.groups(id) on delete cascade,
  phone         text not null,
  message       text,
  received_at   timestamptz not null default now(),
  primary key (group_id, phone)
);

create table if not exists public.swap_state (
  group_id      text primary key references public.groups(id) on delete cascade,
  state         jsonb not null default '{}'::jsonb,
  updated_at    timestamptz not null default now()
);


-- ─── group_files (Phase 2c: per-group JSON blobs) ─────────────────────────────
-- Each per-group data file (families/rotation/schedule/trips/karma/absences/
-- location/route_cache/confirmations/swap_state/trip_config) stored as one
-- jsonb blob. group_id is plain text; the app deletes rows on group removal.
create table if not exists public.group_files (
  group_id    text not null,
  filename    text not null,
  payload     jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now(),
  primary key (group_id, filename)
);
create index if not exists group_files_group_idx on public.group_files (group_id);


-- ─── Row-Level Security: ENABLED, no policies (deny-by-default) ───────────────
-- The Flask app uses the service-role key, which BYPASSES RLS — so enabling
-- RLS changes nothing for the app. What it does change: the anon
-- ("publishable") key gets zero table access through Supabase's REST API.
-- With RLS disabled, Supabase's default grants would let any holder of the
-- anon key read every table (kids' names, addresses, phones). RLS on +
-- no policies = locked to everyone except the server.
-- Phase 2c adds real per-group policies here if browsers ever talk to
-- Supabase directly.
alter table public.groups          enable row level security;
alter table public.users           enable row level security;
alter table public.memberships     enable row level security;
alter table public.families        enable row level security;
alter table public.guardians       enable row level security;
alter table public.kids            enable row level security;
alter table public.rotation        enable row level security;
alter table public.schedule        enable row level security;
alter table public.trips           enable row level security;
alter table public.absences        enable row level security;
alter table public.karma           enable row level security;
alter table public.invites         enable row level security;
alter table public.password_resets enable row level security;
alter table public.geocode_cache   enable row level security;
alter table public.route_cache     enable row level security;
alter table public.location        enable row level security;
alter table public.confirmations   enable row level security;
alter table public.swap_state      enable row level security;
alter table public.group_files     enable row level security;
